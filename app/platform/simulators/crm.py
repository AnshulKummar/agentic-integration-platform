"""CRM Platform simulator: OAuth2 client-credentials, expiring tokens, cursor pagination.

Field names match ``app.demo.mocks`` (Opportunity / Account / Contact) so KG
mappings carry over. Operation ids keep the demo's tool names
(``crm.get_opportunity``, ``crm.find_opportunity``, ``crm.find_contact`` ...).
"""

from __future__ import annotations

import base64
import binascii
import copy
import hmac
import secrets
import time
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.openapi.models import OAuthFlowClientCredentials, OAuthFlows
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2
from pydantic import BaseModel

from app.demo.mocks import SEED_ACCOUNTS, SEED_CONTACTS, SEED_OPPORTUNITIES
from app.platform.simulators.common import Page, add_admin_routes, create_sim_app, paginate

TOKEN_PATH = "/oauth/token"


class Account(BaseModel):
    id: str
    name: str
    billing_address: str
    shipping_address: str
    currency: str


class Contact(BaseModel):
    id: str
    account_id: str
    email: str


class Opportunity(BaseModel):
    id: str
    account_id: str
    contact_id: str
    amount: float
    payment_terms: str
    stage: str
    contract_id: str


class OpportunityPatch(BaseModel):
    stage: str


def _seed_extra(n: int) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    accounts = sorted(SEED_ACCOUNTS)
    for i in range(n):
        opp_id = f"opp-{1000 + i}"
        account = accounts[i % len(accounts)]
        contact = next(c for c, v in SEED_CONTACTS.items() if v["account_id"] == account)
        out[opp_id] = {
            "id": opp_id,
            "account_id": account,
            "contact_id": contact,
            "amount": 1000.0 * (i + 1),
            "payment_terms": "NET30",
            "stage": "Prospecting",
            "contract_id": f"agr-{1000 + i}",
        }
    return out


def create_crm_app(
    *,
    client_id: str,
    client_secret: str,
    admin_token: str,
    token_ttl_s: int = 3600,
    rate_limit: int | None = 600,
    rate_window_s: float = 60.0,
    extra_opportunities: int = 0,
    seed: int | None = None,
) -> FastAPI:
    if not client_id or not client_secret:
        raise ValueError("client_id and client_secret are required")
    app, state = create_sim_app(
        name="crm",
        title="CRM Platform API",
        version="1.4.0",
        description="Accounts, contacts and opportunities. OAuth2 client-credentials; cursor pagination.",
        admin_token=admin_token,
        rate_limit=rate_limit,
        rate_window_s=rate_window_s,
        seed=seed,
    )
    accounts = copy.deepcopy(SEED_ACCOUNTS)
    contacts = copy.deepcopy(SEED_CONTACTS)
    opportunities = {**copy.deepcopy(SEED_OPPORTUNITIES), **_seed_extra(extra_opportunities)}
    tokens: dict[str, float] = {}  # token -> monotonic expiry
    state.extra_state = lambda: {"active_tokens": sum(1 for exp in tokens.values() if exp > time.monotonic())}

    oauth2 = OAuth2(
        flows=OAuthFlows(
            clientCredentials=OAuthFlowClientCredentials(tokenUrl=TOKEN_PATH, scopes={"crm.read": "", "crm.write": ""})
        ),
        auto_error=False,
    )

    def _unauthorized(error: str) -> HTTPException:
        return HTTPException(401, error, headers={"WWW-Authenticate": f'Bearer error="{error}"'})

    async def require_token(authorization: str | None = Depends(oauth2)) -> None:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise _unauthorized("invalid_request")
        token = authorization[7:].strip()
        expiry = tokens.get(token)
        if expiry is None:
            raise _unauthorized("invalid_token")
        if expiry <= time.monotonic():
            tokens.pop(token, None)
            raise _unauthorized("invalid_token")

    @app.post(TOKEN_PATH, include_in_schema=False)
    async def issue_token(request: Request) -> JSONResponse:
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8", "replace")).items()}
        cid, csecret = form.get("client_id", ""), form.get("client_secret", "")
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                cid, _, csecret = base64.b64decode(header[6:]).decode().partition(":")
            except (binascii.Error, UnicodeDecodeError):
                return JSONResponse({"error": "invalid_client"}, status_code=401)
        if form.get("grant_type") != "client_credentials":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        valid = hmac.compare_digest(cid.encode(), client_id.encode()) and hmac.compare_digest(
            csecret.encode(), client_secret.encode()
        )
        if not valid:
            state.bump("token_failures")
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        token = secrets.token_urlsafe(32)
        tokens[token] = time.monotonic() + token_ttl_s
        state.bump("tokens_issued")
        return JSONResponse(
            {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": token_ttl_s,
                "scope": form.get("scope", "crm.read crm.write"),
            },
            headers={"Cache-Control": "no-store"},
        )

    api = APIRouter(dependencies=[Depends(require_token)], tags=["crm"])

    def _require(store: dict[str, dict[str, Any]], key: str, kind: str) -> dict[str, Any]:
        if key not in store:
            raise HTTPException(404, f"{kind} {key} not found")
        return store[key]

    @api.get("/accounts", operation_id="list_accounts", summary="List accounts", response_model=Page[Account])
    async def list_accounts(cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100)) -> dict[str, Any]:
        items, nxt = paginate([accounts[k] for k in sorted(accounts)], cursor, limit)
        return {"items": items, "next_cursor": nxt}

    @api.get("/accounts/{account_id}", operation_id="get_account", summary="Get an account", response_model=Account)
    async def get_account(account_id: str) -> dict[str, Any]:
        return _require(accounts, account_id, "account")

    @api.get(
        "/contacts/search", operation_id="find_contact", summary="Find a contact by e-mail", response_model=Contact
    )
    async def find_contact(email: str) -> dict[str, Any]:
        for contact in contacts.values():
            if contact["email"].lower() == email.lower():
                return contact
        raise HTTPException(404, f"no contact with email {email}")

    @api.get("/contacts/{contact_id}", operation_id="get_contact", summary="Get a contact", response_model=Contact)
    async def get_contact(contact_id: str) -> dict[str, Any]:
        return _require(contacts, contact_id, "contact")

    @api.get(
        "/opportunities",
        operation_id="list_opportunities",
        summary="List opportunities (paginated)",
        response_model=Page[Opportunity],
    )
    async def list_opportunities(
        stage: str | None = None,
        account_id: str | None = None,
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        rows = [opportunities[k] for k in sorted(opportunities)]
        if stage is not None:
            rows = [r for r in rows if r["stage"] == stage]
        if account_id is not None:
            rows = [r for r in rows if r["account_id"] == account_id]
        items, nxt = paginate(rows, cursor, limit)
        return {"items": items, "next_cursor": nxt}

    @api.get(
        "/opportunities/search",
        operation_id="find_opportunity",
        summary="Find the opportunity for a contract",
        response_model=Opportunity,
    )
    async def find_opportunity(contract_id: str) -> dict[str, Any]:
        for opp in opportunities.values():
            if opp["contract_id"] == contract_id:
                return opp
        raise HTTPException(404, f"no opportunity for contract {contract_id}")

    @api.get(
        "/opportunities/{opportunity_id}",
        operation_id="get_opportunity",
        summary="Get an opportunity",
        response_model=Opportunity,
    )
    async def get_opportunity(opportunity_id: str) -> dict[str, Any]:
        return _require(opportunities, opportunity_id, "opportunity")

    @api.patch(
        "/opportunities/{opportunity_id}",
        operation_id="update_opportunity",
        summary="Update stage",
        response_model=Opportunity,
    )
    async def update_opportunity(opportunity_id: str, body: OpportunityPatch) -> dict[str, Any]:
        opp = _require(opportunities, opportunity_id, "opportunity")
        opp["stage"] = body.stage
        return opp

    app.include_router(api)

    def admin(router: APIRouter) -> None:
        @router.post("/revoke-tokens")
        async def revoke_tokens() -> dict[str, int]:
            revoked = len(tokens)
            tokens.clear()
            return {"revoked": revoked}

    add_admin_routes(app, state, admin)
    return app
