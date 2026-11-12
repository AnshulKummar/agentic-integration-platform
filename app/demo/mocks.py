"""In-process mock target apps: CRM Platform, Billing / ERP, Tax Service, e-Signature App.

Each app is a small FastAPI sub-application over an in-memory ``MockWorld``. The
demo server mounts them under ``/mock``; the MCP runtime calls them in-process via
``httpx.ASGITransport``. Billing validates formats strictly (ISO 4217 currency,
terms-code enum) so sandbox tests catch the same mistakes a real ERP would.
"""

from __future__ import annotations

import copy
import itertools
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel

from app.demo.kg_store import load_fixture_json

logger = logging.getLogger(__name__)

ISO_4217 = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "INR", "SEK"}
TERMS_CODES = {"NET15", "NET30", "NET60", "DUE_ON_RECEIPT"}
TAX_RATES = {"DE": 0.19, "GB": 0.20, "US-MA": 0.0625, "US-CA": 0.0725, "US-NY": 0.04}
JURISDICTION_RE = re.compile(r"^[A-Z]{2}(-[A-Z]{2})?$")

SPEC_FILES = {"2.4.0": "esign_openapi_v2_4.json", "3.0.0": "esign_openapi_v3_0.json"}

SEED_ACCOUNTS: dict[str, dict[str, Any]] = {
    "acc-001": {
        "id": "acc-001", "name": "Northwind Traders Inc.",
        "billing_address": "100 Main St, Boston, MA, US", "shipping_address": "100 Main St, Boston, MA, US",
        "currency": "USD",
    },
    "acc-002": {
        "id": "acc-002", "name": "Acme GmbH",
        "billing_address": "1 Market St, San Francisco, CA, US", "shipping_address": "Hauptstrasse 5, Berlin, DE",
        "currency": "Euro",
    },
    "acc-003": {
        "id": "acc-003", "name": "Globex Ltd",
        "billing_address": "10 King St, London, GB", "shipping_address": "10 King St, London, GB",
        "currency": "Pound Sterling",
    },
}
SEED_CONTACTS: dict[str, dict[str, Any]] = {
    "con-001": {"id": "con-001", "account_id": "acc-001", "email": "dana@northwind.example"},
    "con-002": {"id": "con-002", "account_id": "acc-002", "email": "jonas@acme.example"},
    "con-003": {"id": "con-003", "account_id": "acc-003", "email": "hank@globex.example"},
}
SEED_OPPORTUNITIES: dict[str, dict[str, Any]] = {
    "opp-001": {
        "id": "opp-001", "account_id": "acc-001", "contact_id": "con-001", "amount": 48000.0,
        "payment_terms": "NET30", "stage": "Closed Won", "contract_id": "agr-001",
    },
    "opp-002": {
        "id": "opp-002", "account_id": "acc-002", "contact_id": "con-002", "amount": 120000.0,
        "payment_terms": "Net 30 days", "stage": "Closed Won", "contract_id": "agr-002",
    },
    "opp-003": {
        "id": "opp-003", "account_id": "acc-003", "contact_id": "con-003", "amount": 36000.0,
        "payment_terms": "Due on receipt", "stage": "Closed Won", "contract_id": "agr-003",
    },
}


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


@dataclass
class MockWorld:
    """All mutable state of the four mock apps."""

    accounts: dict[str, dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(SEED_ACCOUNTS))
    contacts: dict[str, dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(SEED_CONTACTS))
    opportunities: dict[str, dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(SEED_OPPORTUNITIES))
    customers: dict[str, dict[str, Any]] = field(default_factory=dict)
    invoices: dict[str, dict[str, Any]] = field(default_factory=dict)
    agreements: dict[str, dict[str, Any]] = field(default_factory=_seed_agreements)
    envelopes: dict[str, dict[str, Any]] = field(default_factory=dict)
    subscriptions: list[dict[str, Any]] = field(default_factory=list)
    deliveries: list[dict[str, Any]] = field(default_factory=list)
    esign_published_version: str = "3.0.0"
    faults: dict[str, int] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def next_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids):04d}"

    def reset(self) -> None:
        fresh = MockWorld()
        self.__dict__.update(fresh.__dict__)

    def check_fault(self, app: str) -> None:
        """Raise 503 if a fault is armed for ``app`` (consumes one failure)."""
        self.calls.append(app)
        remaining = self.faults.get(app, 0)
        if remaining > 0:
            self.faults[app] = remaining - 1
            raise HTTPException(status_code=503, detail=f"{app} unavailable (injected fault)")

    def esign_spec(self, version: str | None = None) -> dict[str, Any]:
        v = version or self.esign_published_version
        if v not in SPEC_FILES:
            raise HTTPException(status_code=404, detail=f"unknown spec version {v!r}")
        return load_fixture_json(SPEC_FILES[v])


# ------------------------------------------------------------------ bodies
class OpportunityPatch(BaseModel):
    stage: str


class CustomerCreate(BaseModel):
    legal_name: str
    address: str
    currency_code: str
    terms_code: str
    external_ref: str
    tax_profile: str = ""


class InvoiceCreate(BaseModel):
    customer_id: str
    total: float
    external_ref: str
    terms_code: str = ""


class TaxRequest(BaseModel):
    jurisdiction: str
    amount: float


class FaultRequest(BaseModel):
    app: str
    failures: int = 1


class PublishRequest(BaseModel):
    version: str


# ------------------------------------------------------------------- apps
def create_crm_app(world: MockWorld) -> FastAPI:
    app = FastAPI(title="CRM Platform (mock)", version="1.0.0")

    @app.get("/health", operation_id="health")
    async def health() -> dict[str, str]:
        world.check_fault("crm")
        return {"status": "ok"}

    @app.get("/accounts/{account_id}", operation_id="get_account", summary="Get an account")
    async def get_account(account_id: str) -> dict[str, Any]:
        world.check_fault("crm")
        if account_id not in world.accounts:
            raise HTTPException(404, f"account {account_id} not found")
        return world.accounts[account_id]

    @app.get("/opportunities/{opportunity_id}", operation_id="get_opportunity", summary="Get an opportunity")
    async def get_opportunity(opportunity_id: str) -> dict[str, Any]:
        world.check_fault("crm")
        if opportunity_id not in world.opportunities:
            raise HTTPException(404, f"opportunity {opportunity_id} not found")
        return world.opportunities[opportunity_id]

    @app.get("/opportunities", operation_id="find_opportunity", summary="Find the opportunity for a contract")
    async def find_opportunity(contract_id: str) -> dict[str, Any]:
        world.check_fault("crm")
        for opp in world.opportunities.values():
            if opp["contract_id"] == contract_id:
                return opp
        raise HTTPException(404, f"no opportunity for contract {contract_id}")

    @app.patch("/opportunities/{opportunity_id}", operation_id="update_opportunity", summary="Update stage")
    async def update_opportunity(opportunity_id: str, body: OpportunityPatch) -> dict[str, Any]:
        world.check_fault("crm")
        if opportunity_id not in world.opportunities:
            raise HTTPException(404, f"opportunity {opportunity_id} not found")
        world.opportunities[opportunity_id]["stage"] = body.stage
        return world.opportunities[opportunity_id]

    @app.get("/contacts", operation_id="find_contact", summary="Find a contact by e-mail")
    async def find_contact(email: str) -> dict[str, Any]:
        world.check_fault("crm")
        for contact in world.contacts.values():
            if contact["email"].lower() == email.lower():
                return contact
        raise HTTPException(404, f"no contact with email {email}")

    return app


def create_billing_app(world: MockWorld) -> FastAPI:
    app = FastAPI(title="Billing / ERP (mock)", version="7.2.0")

    @app.get("/health", operation_id="health")
    async def health() -> dict[str, str]:
        world.check_fault("billing")
        return {"status": "ok"}

    @app.post("/customers", operation_id="create_customer", summary="Create a billing customer", status_code=201)
    async def create_customer(body: CustomerCreate, response: Response) -> dict[str, Any]:
        world.check_fault("billing")
        errors: list[dict[str, str]] = []
        if body.currency_code not in ISO_4217:
            errors.append({"field": "currency_code", "message": f"{body.currency_code!r} is not an ISO 4217 code"})
        if body.terms_code not in TERMS_CODES:
            errors.append({"field": "terms_code", "message": f"{body.terms_code!r} is not one of {sorted(TERMS_CODES)}"})
        if errors:
            raise HTTPException(422, errors)
        for cust in world.customers.values():
            if cust["external_ref"] == body.external_ref:
                response.status_code = 200  # idempotent replay
                return cust
        cust_id = world.next_id("cus")
        world.customers[cust_id] = {"id": cust_id, **body.model_dump()}
        return world.customers[cust_id]

    @app.get("/customers", operation_id="list_customers", summary="List billing customers")
    async def list_customers() -> list[dict[str, Any]]:
        world.check_fault("billing")
        return list(world.customers.values())

    @app.post("/invoices", operation_id="create_invoice", summary="Create an invoice", status_code=201)
    async def create_invoice(body: InvoiceCreate, response: Response) -> dict[str, Any]:
        world.check_fault("billing")
        if body.customer_id not in world.customers:
            raise HTTPException(422, [{"field": "customer_id", "message": "unknown customer"}])
        for inv in world.invoices.values():
            if inv["external_ref"] == body.external_ref:
                response.status_code = 200
                return inv
        inv_id = world.next_id("inv")
        world.invoices[inv_id] = {"id": inv_id, **body.model_dump()}
        return world.invoices[inv_id]

    return app


def create_tax_app(world: MockWorld) -> FastAPI:
    app = FastAPI(title="Tax Service (mock)", version="2.0.0")

    @app.get("/health", operation_id="health")
    async def health() -> dict[str, str]:
        world.check_fault("tax")
        return {"status": "ok"}

    @app.post("/tax/calculate", operation_id="calculate", summary="Calculate tax for an amount")
    async def calculate(body: TaxRequest) -> dict[str, Any]:
        world.check_fault("tax")
        if not JURISDICTION_RE.match(body.jurisdiction):
            raise HTTPException(422, [{"field": "jurisdiction", "message": "expected a region code like DE or US-CA"}])
        rate = TAX_RATES.get(body.jurisdiction, 0.10)
        return {
            "profile_id": f"tp-{body.jurisdiction.lower()}",
            "jurisdiction": body.jurisdiction,
            "rate": rate,
            "tax": round(body.amount * rate, 2),
        }

    return app


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split(".") if p.isdigit())


def create_esign_app(world: MockWorld) -> FastAPI:
    # openapi_url=None: this app serves the vendor's hand-written spec instead.
    app = FastAPI(title="e-Signature App (mock)", openapi_url=None, docs_url=None, redoc_url=None)

    def require_v3() -> None:
        if _version_tuple(world.esign_published_version) < (3,):
            raise HTTPException(404, "Agreements API requires v3")

    @app.get("/openapi.json")
    async def spec(version: str | None = None) -> dict[str, Any]:
        world.check_fault("esign")
        return world.esign_spec(version)

    @app.get("/versions")
    async def versions() -> dict[str, Any]:
        return {"latest": world.esign_published_version, "available": sorted(SPEC_FILES)}

    @app.post("/v2/envelopes", status_code=201)
    async def send_envelope(body: dict[str, Any], response: Response) -> dict[str, Any]:
        world.check_fault("esign")
        signers = body.get("signers")
        if not signers and body.get("signer_email"):
            signers = [{"email": body["signer_email"], "role": "signer"}]
            if _version_tuple(world.esign_published_version) >= (3,):
                response.headers["Deprecation"] = "true"
                response.headers["Sunset"] = "2027-03-31"
        if not signers:
            raise HTTPException(422, [{"field": "signers", "message": "at least one signer is required"}])
        env_id = world.next_id("env")
        world.envelopes[env_id] = {
            "id": env_id, "status": "sent", "document_id": body.get("document_id", ""), "signers": signers,
        }
        return world.envelopes[env_id]

    @app.get("/v2/envelopes/{envelope_id}/signers")
    async def get_signers(envelope_id: str) -> dict[str, Any]:
        world.check_fault("esign")
        record = world.envelopes.get(envelope_id) or world.agreements.get(envelope_id)
        if record is None:
            raise HTTPException(404, f"{envelope_id} not found")
        signers = record["signers"]
        out: dict[str, Any] = {"id": envelope_id, "status": record["status"], "signers": signers}
        out["signer_email"] = signers[0]["email"] if signers else None
        return out

    @app.post("/v3/agreements", status_code=201)
    async def create_agreement(body: dict[str, Any]) -> dict[str, Any]:
        world.check_fault("esign")
        require_v3()
        if not body.get("template_id") or not body.get("signers"):
            raise HTTPException(422, [{"field": "template_id/signers", "message": "required"}])
        agr_id = world.next_id("agr")
        world.agreements[agr_id] = {
            "id": agr_id, "template_id": body["template_id"], "external_ref": body.get("external_ref", ""),
            "status": "out_for_signature", "signers": body["signers"], "fields": {},
        }
        return world.agreements[agr_id]

    @app.get("/v3/agreements/{agreement_id}/fields")
    async def get_agreement_fields(agreement_id: str) -> dict[str, Any]:
        world.check_fault("esign")
        require_v3()
        agr = world.agreements.get(agreement_id)
        if agr is None:
            raise HTTPException(404, f"agreement {agreement_id} not found")
        fields: dict[str, Any] = agr["fields"]
        return fields

    @app.post("/v3/webhooks", status_code=201)
    async def subscribe(body: dict[str, Any]) -> dict[str, Any]:
        world.check_fault("esign")
        require_v3()
        sub = {"id": world.next_id("sub"), "event": body.get("event"), "callback_url": body.get("callback_url")}
        world.subscriptions.append(sub)
        return sub

    @app.post("/_simulate/agreements/{agreement_id}/complete")
    async def simulate_complete(agreement_id: str) -> dict[str, Any]:
        agr = world.agreements.get(agreement_id)
        if agr is None:
            raise HTTPException(404, f"agreement {agreement_id} not found")
        agr["status"] = "completed"
        event = {
            "event": "agreement.completed", "agreement_id": agreement_id,
            "external_ref": agr["external_ref"], "completed_at": "2026-11-12T10:00:00Z",
        }
        for sub in world.subscriptions:
            if sub["event"] == "agreement.completed":
                world.deliveries.append({"subscription": sub["id"], **event})
        return event

    return app


def create_control_router(world: MockWorld) -> APIRouter:
    router = APIRouter(prefix="/_control", tags=["mock-control"])

    @router.post("/faults")
    async def arm_fault(body: FaultRequest) -> dict[str, Any]:
        world.faults[body.app] = body.failures
        logger.info("mock.fault_armed", extra={"app": body.app, "failures": body.failures})
        return {"faults": world.faults}

    @router.post("/publish")
    async def publish(body: PublishRequest) -> dict[str, str]:
        if body.version not in SPEC_FILES:
            raise HTTPException(404, f"unknown version {body.version!r}")
        world.esign_published_version = body.version
        return {"published": body.version}

    @router.get("/state")
    async def state() -> dict[str, Any]:
        return {
            "customers": list(world.customers.values()),
            "invoices": list(world.invoices.values()),
            "subscriptions": world.subscriptions,
            "deliveries": world.deliveries,
            "esignPublishedVersion": world.esign_published_version,
            "faults": world.faults,
        }

    return router


@dataclass
class MockApps:
    world: MockWorld
    crm: FastAPI
    billing: FastAPI
    tax: FastAPI
    esign: FastAPI
    root: FastAPI


def create_mock_apps(world: MockWorld | None = None) -> MockApps:
    world = world or MockWorld()
    crm, billing, tax, esign = (
        create_crm_app(world), create_billing_app(world), create_tax_app(world), create_esign_app(world)
    )
    root = FastAPI(title="PMAIP mock target apps")
    root.include_router(create_control_router(world))
    root.mount("/crm", crm)
    root.mount("/billing", billing)
    root.mount("/tax", tax)
    root.mount("/esign", esign)
    return MockApps(world=world, crm=crm, billing=billing, tax=tax, esign=esign, root=root)


# ------------------------------------------------------------- transforms
TERMS_MAP: dict[str, str] = {
    "net 15": "NET15", "net15": "NET15", "net 30": "NET30", "net 30 days": "NET30", "net30": "NET30",
    "net 60": "NET60", "net60": "NET60", "due on receipt": "DUE_ON_RECEIPT", "due_on_receipt": "DUE_ON_RECEIPT",
}
CURRENCY_NAMES: dict[str, str] = {
    "us dollar": "USD", "dollar": "USD", "euro": "EUR", "pound sterling": "GBP", "british pound": "GBP",
    "swiss franc": "CHF", "yen": "JPY",
}


def to_iso4217(value: str) -> str:
    v = value.strip()
    if v.upper() in ISO_4217:
        return v.upper()
    code = CURRENCY_NAMES.get(v.lower())
    if code is None:
        raise ValueError(f"cannot map currency {value!r} to ISO 4217")
    return code


def terms_lookup(value: str) -> str:
    v = value.strip()
    if v.upper() in TERMS_CODES:
        return v.upper()
    code = TERMS_MAP.get(v.lower())
    if code is None:
        raise ValueError(f"no terms code for {value!r}")
    return code


def geocode(address: str) -> str:
    """Address -> tax region code: 'Boston, MA, US' -> 'US-MA', 'Berlin, DE' -> 'DE'."""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        raise ValueError("empty address")
    country = parts[-1].upper()
    if country == "US" and len(parts) >= 2:
        return f"US-{parts[-2].upper()}"
    return country
