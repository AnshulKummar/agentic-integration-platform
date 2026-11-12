"""e-Signature App simulator: API-key auth, a deprecated field, HMAC-signed webhooks.

Mirrors ``app/demo/fixtures/esign_openapi_v3_0.json``: v2 envelopes (with the
deprecated ``signer_email``, replaced by ``signers[].email``) and v3 agreements
with recipients (``signers``), signed form ``fields`` and the
``agreement.completed`` webhook.

Webhook deliveries carry ``X-Esign-Signature: t=<unix>,v1=<hex>`` where
``hex = HMAC-SHA256(webhook_secret, f"{t}." + raw_body)``; receivers verify with
``verify_webhook_signature`` (constant-time, with a timestamp tolerance).
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import itertools
import json
import logging
import time
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from app.demo.mocks import SEED_CONTACTS, SEED_OPPORTUNITIES
from app.platform.connectors.http import shared_ssl_context
from app.platform.simulators.common import add_admin_routes, add_openapi_extensions, create_sim_app

log = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"
SIGNATURE_HEADER = "X-Esign-Signature"
WEBHOOK_SUBSCRIBE_PATH = "/v3/webhooks"
COMPLETED_EVENT = "agreement.completed"
SUNSET = "2027-03-31"


# ---------------------------------------------------------------- signatures
def sign_webhook(secret: str, body: bytes, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_webhook_signature(
    secret: str, body: bytes, header: str | None, *, tolerance_s: int = 300, now: float | None = None
) -> bool:
    """True iff ``header`` is a valid signature of ``body`` within ``tolerance_s`` seconds."""
    if not header:
        return False
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        key, _, value = item.strip().partition("=")
        parts.setdefault(key, []).append(value)
    try:
        timestamp = int(parts["t"][0])
    except (KeyError, ValueError, IndexError):
        return False
    if abs((time.time() if now is None else now) - timestamp) > tolerance_s:
        return False
    expected = sign_webhook(secret, body, timestamp).split("v1=", 1)[1]
    return any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", []))


# ---------------------------------------------------------------- schemas
class Signer(BaseModel):
    email: str
    role: Literal["signer", "cc"] = "signer"


class EnvelopeCreate(BaseModel):
    document_id: str
    # Deprecated via JSON-schema extras (not pydantic's ``deprecated=``, which warns on access).
    signer_email: str | None = Field(
        default=None,
        description="Signer e-mail address",
        json_schema_extra={"deprecated": True, "x-replaced-by": "signers[].email", "x-sunset": SUNSET},
    )
    signers: list[Signer] | None = None


class Envelope(BaseModel):
    id: str
    status: Literal["sent", "delivered", "completed", "declined"]
    document_id: str
    signer_email: str | None = Field(
        default=None,
        description="Signer e-mail address",
        json_schema_extra={"deprecated": True, "x-replaced-by": "signers[].email", "x-sunset": SUNSET},
    )
    signers: list[Signer]


class AgreementCreate(BaseModel):
    template_id: str = Field(description="Agreement template to instantiate")
    external_ref: str = Field(default="", description="Caller reference, e.g. CRM contract id")
    signers: list[Signer] = Field(min_length=1)


class AgreementFields(BaseModel):
    amount: float | None = Field(default=None, description="Contract value captured at signing")
    payment_terms: str | None = Field(default=None, description="Payment terms as written in the agreement")


class Agreement(BaseModel):
    id: str
    template_id: str
    status: Literal["draft", "out_for_signature", "completed", "cancelled"]
    external_ref: str
    signers: list[Signer]
    fields: AgreementFields


class SubscriptionCreate(BaseModel):
    event: Literal["agreement.completed"]
    callback_url: str = Field(description="Where events are delivered")


class Subscription(BaseModel):
    id: str
    event: str
    callback_url: str


class AgreementCompletedEvent(BaseModel):
    event: Literal["agreement.completed"]
    agreement_id: str
    external_ref: str
    completed_at: str


class CompleteRequest(BaseModel):
    amount: float | None = None
    payment_terms: str | None = None


def _seed_agreements() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for opp in SEED_OPPORTUNITIES.values():
        contact = SEED_CONTACTS[opp["contact_id"]]
        out[opp["contract_id"]] = {
            "id": opp["contract_id"],
            "template_id": "tpl-msa",
            "external_ref": opp["id"],
            "status": "out_for_signature",
            "signers": [{"email": contact["email"], "role": "signer"}],
            "fields": {"amount": opp["amount"], "payment_terms": opp["payment_terms"]},
        }
    return out


def create_esign_app(
    *,
    api_key: str,
    admin_token: str,
    webhook_secret: str,
    rate_limit: int | None = 600,
    rate_window_s: float = 60.0,
    delivery_transport: httpx.AsyncBaseTransport | None = None,
    delivery_attempts: int = 3,
    seed: int | None = None,
) -> FastAPI:
    if not api_key or not webhook_secret:
        raise ValueError("api_key and webhook_secret are required")
    app, state = create_sim_app(
        name="esign",
        title="e-Signature App API",
        version="3.0.0",
        description=(
            "Agreements API v3. Template-based agreements, signed form data and completion webhooks. "
            "Envelope.signer_email is deprecated in favour of signers[].email."
        ),
        admin_token=admin_token,
        rate_limit=rate_limit,
        rate_window_s=rate_window_s,
        seed=seed,
    )
    add_openapi_extensions(app, {"x-webhook-subscribe-path": WEBHOOK_SUBSCRIBE_PATH})
    envelopes: dict[str, dict[str, Any]] = {}
    agreements = copy.deepcopy(_seed_agreements())
    subscriptions: dict[str, dict[str, Any]] = {}
    deliveries: list[dict[str, Any]] = []
    ids = itertools.count(1)
    state.extra_state = lambda: {
        "subscriptions": list(subscriptions.values()),
        "deliveries": list(deliveries),
        "agreements": len(agreements),
        "envelopes": len(envelopes),
    }

    key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

    async def require_key(supplied: str | None = Depends(key_header)) -> None:
        if not supplied or not hmac.compare_digest(supplied.encode(), api_key.encode()):
            raise HTTPException(401, "invalid or missing API key")

    def next_id(prefix: str) -> str:
        return f"{prefix}-{next(ids):04d}"

    def find(store: dict[str, dict[str, Any]], key: str, kind: str) -> dict[str, Any]:
        if key not in store:
            raise HTTPException(404, f"{kind} {key} not found")
        return store[key]

    api = APIRouter(dependencies=[Depends(require_key)])

    @api.post(
        "/v2/envelopes",
        operation_id="sendEnvelope",
        summary="Send a document for signature",
        status_code=201,
        response_model=Envelope,
        tags=["envelopes"],
    )
    async def send_envelope(body: EnvelopeCreate, response: Response) -> dict[str, Any]:
        signers = [s.model_dump() for s in body.signers or []]
        if not signers and body.signer_email:
            signers = [{"email": body.signer_email, "role": "signer"}]
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = SUNSET
            state.bump("deprecated_field_used")
        if not signers:
            raise HTTPException(422, [{"field": "signers", "message": "at least one signer is required"}])
        env_id = next_id("env")
        envelopes[env_id] = {"id": env_id, "status": "sent", "document_id": body.document_id, "signers": signers}
        return {**envelopes[env_id], "signer_email": signers[0]["email"]}

    @api.get(
        "/v2/envelopes/{id}/signers",
        operation_id="getSigners",
        summary="List the signers of an envelope",
        response_model=Envelope,
        tags=["envelopes"],
    )
    async def get_signers(id: str) -> dict[str, Any]:
        record = envelopes.get(id) or agreements.get(id)
        if record is None:
            raise HTTPException(404, f"{id} not found")
        signers = record["signers"]
        return {
            "id": id,
            "status": "sent" if record["status"] != "completed" else "completed",
            "document_id": record.get("document_id", record.get("template_id", "")),
            "signers": signers,
            "signer_email": signers[0]["email"] if signers else None,
        }

    @api.post(
        "/v3/agreements",
        operation_id="createAgreement",
        summary="Create agreement from template",
        status_code=201,
        response_model=Agreement,
        tags=["agreements"],
    )
    async def create_agreement(body: AgreementCreate) -> dict[str, Any]:
        agr_id = next_id("agr")
        agreements[agr_id] = {
            "id": agr_id,
            "template_id": body.template_id,
            "external_ref": body.external_ref,
            "status": "out_for_signature",
            "signers": [s.model_dump() for s in body.signers],
            "fields": {},
        }
        return agreements[agr_id]

    @api.get(
        "/v3/agreements/{id}",
        operation_id="getAgreement",
        summary="Get an agreement",
        response_model=Agreement,
        tags=["agreements"],
    )
    async def get_agreement(id: str) -> dict[str, Any]:
        return find(agreements, id, "agreement")

    @api.get(
        "/v3/agreements/{id}/fields",
        operation_id="getAgreementFields",
        summary="Signed form data (amount, term, payment terms)",
        response_model=AgreementFields,
        tags=["agreements"],
    )
    async def get_agreement_fields(id: str) -> dict[str, Any]:
        fields: dict[str, Any] = find(agreements, id, "agreement")["fields"]
        return fields

    @api.post(
        WEBHOOK_SUBSCRIBE_PATH,
        operation_id="subscribeWebhook",
        summary="Subscribe to an event",
        status_code=201,
        response_model=Subscription,
        tags=["webhooks"],
    )
    async def subscribe(body: SubscriptionCreate) -> dict[str, Any]:
        url = httpx.URL(body.callback_url)
        if url.scheme not in ("http", "https") or not url.host:
            raise HTTPException(422, [{"field": "callback_url", "message": "must be an absolute http(s) URL"}])
        sub = {"id": next_id("sub"), "event": body.event, "callback_url": body.callback_url}
        subscriptions[sub["id"]] = sub
        return sub

    @api.delete(
        WEBHOOK_SUBSCRIBE_PATH + "/{subscription_id}",
        operation_id="unsubscribeWebhook",
        summary="Delete a webhook subscription",
        status_code=204,
        tags=["webhooks"],
    )
    async def unsubscribe(subscription_id: str) -> Response:
        find(subscriptions, subscription_id, "subscription")
        del subscriptions[subscription_id]
        return Response(status_code=204)

    app.include_router(api)

    @app.webhooks.post(COMPLETED_EVENT, operation_id="agreementCompleted", summary="Fires when all parties have signed")
    def agreement_completed(body: AgreementCompletedEvent) -> None:
        """Delivered with an ``X-Esign-Signature`` header (HMAC-SHA256)."""

    async def deliver(sub: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode()
        delivery_id = next_id("dlv")
        record: dict[str, Any] = {
            "id": delivery_id,
            "subscription": sub["id"],
            "event": event["event"],
            "attempts": 0,
            "status_code": None,
            "error": None,
        }
        async with httpx.AsyncClient(transport=delivery_transport, timeout=5.0, verify=shared_ssl_context()) as client:
            for attempt in range(1, max(1, delivery_attempts) + 1):
                record["attempts"] = attempt
                headers = {
                    "Content-Type": "application/json",
                    SIGNATURE_HEADER: sign_webhook(webhook_secret, body, int(time.time())),
                    "X-Esign-Event": event["event"],
                    "X-Esign-Delivery": delivery_id,
                }
                try:
                    resp = await client.post(sub["callback_url"], content=body, headers=headers)
                except httpx.HTTPError as exc:
                    record["error"] = type(exc).__name__
                    log.warning("esign sim: delivery %s attempt %d failed: %s", delivery_id, attempt, record["error"])
                else:
                    record["status_code"] = resp.status_code
                    record["error"] = None
                    if resp.status_code < 500:
                        break
                if attempt < delivery_attempts:
                    await asyncio.sleep(0.1 * attempt)
        deliveries.append(record)
        return record

    def admin(router: APIRouter) -> None:
        @router.post("/agreements/{agreement_id}/complete")
        async def complete(agreement_id: str, body: CompleteRequest | None = None) -> dict[str, Any]:
            agr = find(agreements, agreement_id, "agreement")
            agr["status"] = "completed"
            if body is not None:
                agr["fields"].update(body.model_dump(exclude_none=True))
            event = {
                "event": COMPLETED_EVENT,
                "agreement_id": agreement_id,
                "external_ref": agr["external_ref"],
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            results = [await deliver(s, event) for s in list(subscriptions.values()) if s["event"] == COMPLETED_EVENT]
            return {"event": event, "deliveries": results}

    add_admin_routes(app, state, admin)
    return app
