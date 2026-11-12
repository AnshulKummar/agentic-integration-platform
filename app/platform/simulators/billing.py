"""Billing / ERP simulator: bearer-token auth, strict validation, Idempotency-Key.

Field names match ``app.demo.mocks`` (Customer / Invoice). Validation mirrors a
real ERP: ISO 4217 currency codes and a fixed terms-code enum (422 otherwise).

Idempotency: a POST carrying ``Idempotency-Key`` is executed once; replays with
the same key and the same payload return the original response (status and
body) with ``Idempotent-Replayed: true``; reusing a key with a different payload
is rejected with 422. Without the header every POST creates a new resource, as
real billing APIs do. The header is read from the request rather than declared
as an OpenAPI parameter, so generated tools do not expose it as a body field
(connectors send it from ``_idempotency_key``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import itertools
import json
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.demo.mocks import ISO_4217, TERMS_CODES
from app.platform.simulators.common import Page, add_admin_routes, create_sim_app, paginate

IDEMPOTENCY_HEADER = "Idempotency-Key"
BEARER = HTTPBearer(auto_error=False)


class CustomerCreate(BaseModel):
    legal_name: str
    address: str
    currency_code: str = Field(description="ISO 4217 code, e.g. USD")
    terms_code: str = Field(description="One of NET15, NET30, NET60, DUE_ON_RECEIPT")
    external_ref: str = Field(description="Caller reference, e.g. CRM account id")
    tax_profile: str = ""


class Customer(CustomerCreate):
    id: str


class InvoiceCreate(BaseModel):
    customer_id: str
    total: float = Field(ge=0)
    external_ref: str
    terms_code: str = ""


class Invoice(InvoiceCreate):
    id: str
    status: str = "open"


def _validate_customer(body: CustomerCreate) -> None:
    errors: list[dict[str, str]] = []
    if body.currency_code not in ISO_4217:
        errors.append({"field": "currency_code", "message": f"{body.currency_code!r} is not an ISO 4217 code"})
    if body.terms_code not in TERMS_CODES:
        errors.append({"field": "terms_code", "message": f"{body.terms_code!r} is not one of {sorted(TERMS_CODES)}"})
    if errors:
        raise HTTPException(422, errors)


def create_billing_app(
    *,
    api_token: str,
    admin_token: str,
    rate_limit: int | None = 600,
    rate_window_s: float = 60.0,
    seed: int | None = None,
) -> FastAPI:
    if not api_token:
        raise ValueError("api_token is required")
    app, state = create_sim_app(
        name="billing",
        title="Billing / ERP API",
        version="7.2.0",
        description="Customers and invoices. Bearer-token auth; POSTs honour Idempotency-Key.",
        admin_token=admin_token,
        rate_limit=rate_limit,
        rate_window_s=rate_window_s,
        seed=seed,
    )
    customers: dict[str, dict[str, Any]] = {}
    invoices: dict[str, dict[str, Any]] = {}
    #: (method path, key) -> (payload hash, status, body)
    idempotency: dict[tuple[str, str], tuple[str, int, Any]] = {}
    ids = itertools.count(1)
    write_lock = asyncio.Lock()
    state.extra_state = lambda: {
        "customers": list(customers.values()),
        "invoices": list(invoices.values()),
        "idempotency_keys": len(idempotency),
    }


    async def require_bearer(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)]) -> None:
        if creds is None or not hmac.compare_digest(creds.credentials.encode(), api_token.encode()):
            raise HTTPException(401, "invalid or missing bearer token", headers={"WWW-Authenticate": "Bearer"})

    async def idempotent(
        request: Request, payload: BaseModel, create: Callable[[], dict[str, Any]], status_code: int = 201
    ) -> JSONResponse:
        """Run ``create()`` once per Idempotency-Key (scoped to method + path)."""
        key = request.headers.get(IDEMPOTENCY_HEADER)
        async with write_lock:
            if key is None:
                return JSONResponse(jsonable_encoder(create()), status_code=status_code)
            if not 1 <= len(key) <= 255:
                raise HTTPException(400, "Idempotency-Key must be 1-255 characters")
            scope = (f"{request.method} {request.url.path}", key)
            digest = hashlib.sha256(json.dumps(payload.model_dump(), sort_keys=True).encode()).hexdigest()
            if scope in idempotency:
                stored_digest, stored_status, stored_body = idempotency[scope]
                if stored_digest != digest:
                    raise HTTPException(422, "Idempotency-Key was already used with a different request body")
                state.bump("idempotent_replays")
                return JSONResponse(stored_body, status_code=stored_status, headers={"Idempotent-Replayed": "true"})
            body = jsonable_encoder(create())
            idempotency[scope] = (digest, status_code, body)
            return JSONResponse(body, status_code=status_code)

    def next_id(prefix: str) -> str:
        return f"{prefix}-{next(ids):04d}"

    def find(store: dict[str, dict[str, Any]], key: str, kind: str) -> dict[str, Any]:
        if key not in store:
            raise HTTPException(404, f"{kind} {key} not found")
        return store[key]

    api = APIRouter(dependencies=[Depends(require_bearer)], tags=["billing"])

    @api.post(
        "/customers",
        operation_id="create_customer",
        summary="Create a billing customer",
        status_code=201,
        response_model=Customer,
    )
    async def create_customer(body: CustomerCreate, request: Request) -> JSONResponse:
        _validate_customer(body)

        def create() -> dict[str, Any]:
            cust_id = next_id("cus")
            customers[cust_id] = {"id": cust_id, **body.model_dump()}
            return customers[cust_id]

        return await idempotent(request, body, create)

    @api.get(
        "/customers",
        operation_id="list_customers",
        summary="List billing customers (paginated)",
        response_model=Page[Customer],
    )
    async def list_customers(
        external_ref: str | None = None, cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100)
    ) -> dict[str, Any]:
        rows = [customers[k] for k in sorted(customers)]
        if external_ref is not None:
            rows = [r for r in rows if r["external_ref"] == external_ref]
        items, nxt = paginate(rows, cursor, limit)
        return {"items": items, "next_cursor": nxt}

    @api.get(
        "/customers/{customer_id}",
        operation_id="get_customer",
        summary="Get a billing customer",
        response_model=Customer,
    )
    async def get_customer(customer_id: str) -> dict[str, Any]:
        return find(customers, customer_id, "customer")

    @api.post(
        "/invoices", operation_id="create_invoice", summary="Create an invoice", status_code=201, response_model=Invoice
    )
    async def create_invoice(body: InvoiceCreate, request: Request) -> JSONResponse:
        if body.customer_id not in customers:
            raise HTTPException(422, [{"field": "customer_id", "message": "unknown customer"}])
        if body.terms_code and body.terms_code not in TERMS_CODES:
            raise HTTPException(422, [{"field": "terms_code", "message": f"{body.terms_code!r} is not valid"}])

        def create() -> dict[str, Any]:
            inv_id = next_id("inv")
            invoices[inv_id] = {"id": inv_id, "status": "open", **body.model_dump()}
            return invoices[inv_id]

        return await idempotent(request, body, create)

    @api.get(
        "/invoices", operation_id="list_invoices", summary="List invoices (paginated)", response_model=Page[Invoice]
    )
    async def list_invoices(
        customer_id: str | None = None, cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100)
    ) -> dict[str, Any]:
        rows = [invoices[k] for k in sorted(invoices)]
        if customer_id is not None:
            rows = [r for r in rows if r["customer_id"] == customer_id]
        items, nxt = paginate(rows, cursor, limit)
        return {"items": items, "next_cursor": nxt}

    @api.get("/invoices/{invoice_id}", operation_id="get_invoice", summary="Get an invoice", response_model=Invoice)
    async def get_invoice(invoice_id: str) -> dict[str, Any]:
        return find(invoices, invoice_id, "invoice")

    app.include_router(api)

    def admin(router: APIRouter) -> None:
        @router.post("/reset")
        async def reset() -> dict[str, str]:
            customers.clear()
            invoices.clear()
            idempotency.clear()
            return {"status": "reset"}

    add_admin_routes(app, state, admin)
    return app
