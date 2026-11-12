"""Vendor simulators: auth, faults, rate limits, idempotency, webhooks, harness, CLI."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

from app.platform.connectors.http import shared_ssl_context
from app.platform.simulators.__main__ import build_app
from app.platform.simulators.billing import create_billing_app
from app.platform.simulators.crm import create_crm_app
from app.platform.simulators.esign import (
    SIGNATURE_HEADER,
    create_esign_app,
    sign_webhook,
    verify_webhook_signature,
)
from app.platform.simulators.harness import run_simulator

ClientFactory = Callable[..., httpx.AsyncClient]


def _crm(admin_token: str, **kw: Any) -> tuple[FastAPI, str, str]:
    app = create_crm_app(client_id="cid-1", client_secret="csecret-1", admin_token=admin_token, **kw)
    return app, "cid-1", "csecret-1"


# ------------------------------------------------------------------ admin + faults
async def test_admin_api_requires_token_and_is_hidden(
    asgi_client: ClientFactory, admin_token: str, admin_headers: dict[str, str]
) -> None:
    app = create_billing_app(api_token="tok-123456", admin_token=admin_token)
    c = asgi_client(app)
    assert (await c.get("/_sim/faults")).status_code == 401
    assert (await c.get("/_sim/faults", headers={"X-Sim-Admin-Token": "wrong"})).status_code == 401
    assert (await c.get("/_sim/faults", headers=admin_headers)).status_code == 200
    spec = (await c.get("/openapi.json")).json()
    assert not any(p.startswith("/_sim") for p in spec["paths"])


async def test_fault_injection(asgi_client: ClientFactory, admin_token: str, admin_headers: dict[str, str]) -> None:
    app = create_billing_app(api_token="tok-123456", admin_token=admin_token, seed=7)
    c = asgi_client(app, headers={"Authorization": "Bearer tok-123456"})
    assert (await c.get("/health")).json()["status"] == "ok"

    await c.put("/_sim/faults", json={"outage": True}, headers=admin_headers)
    assert (await c.get("/health")).status_code == 503
    assert (await c.get("/customers")).status_code == 503

    await c.delete("/_sim/faults", headers=admin_headers)
    await c.put("/_sim/faults", json={"error_rate": 1.0, "error_status": 502}, headers=admin_headers)
    assert (await c.get("/customers")).status_code == 502

    await c.delete("/_sim/faults", headers=admin_headers)
    await c.put("/_sim/faults", json={"throttle_next": 1, "retry_after_s": 9}, headers=admin_headers)
    r = await c.get("/customers")
    assert r.status_code == 429 and r.headers["Retry-After"] == "9"
    assert (await c.get("/customers")).status_code == 200

    await c.put("/_sim/faults", json={"latency_ms": 150}, headers=admin_headers)
    started = time.perf_counter()
    assert (await c.get("/customers")).status_code == 200
    assert time.perf_counter() - started >= 0.14

    assert (await c.put("/_sim/faults", json={"error_rate": 2}, headers=admin_headers)).status_code == 422
    state = (await c.get("/_sim/state", headers=admin_headers)).json()
    assert state["counters"]["throttled"] == 1 and state["counters"]["faults_injected"] >= 3


async def test_rate_limit_returns_429_with_retry_after(asgi_client: ClientFactory, admin_token: str) -> None:
    app = create_billing_app(api_token="tok-123456", admin_token=admin_token, rate_limit=2, rate_window_s=30)
    c = asgi_client(app, headers={"Authorization": "Bearer tok-123456"})
    assert [(await c.get("/customers")).status_code for _ in range(2)] == [200, 200]
    r = await c.get("/customers")
    assert r.status_code == 429 and 1 <= int(r.headers["Retry-After"]) <= 30
    assert (await c.get("/health")).status_code == 200  # health is not metered


# ------------------------------------------------------------------ CRM
async def test_crm_oauth_client_credentials(asgi_client: ClientFactory, admin_token: str) -> None:
    app, cid, csecret = _crm(admin_token)
    c = asgi_client(app)
    assert (await c.get("/opportunities/opp-001")).status_code == 401
    basic = base64.b64encode(f"{cid}:{csecret}".encode()).decode()
    r = await c.post(
        "/oauth/token",
        content="grant_type=client_credentials",
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200 and r.json()["token_type"] == "Bearer" and r.json()["expires_in"] == 3600
    token = r.json()["access_token"]
    r2 = await c.post(
        "/oauth/token", data={"grant_type": "client_credentials", "client_id": cid, "client_secret": csecret}
    )
    assert r2.status_code == 200
    bad = await c.post(
        "/oauth/token", data={"grant_type": "client_credentials", "client_id": cid, "client_secret": "nope"}
    )
    assert bad.status_code == 401 and bad.json() == {"error": "invalid_client"}
    assert (await c.post("/oauth/token", data={"grant_type": "password"})).status_code == 400
    opp = await c.get("/opportunities/opp-002", headers={"Authorization": f"Bearer {token}"})
    assert opp.json()["payment_terms"] == "Net 30 days"
    r = await c.get("/opportunities/opp-002", headers={"Authorization": "Bearer forged"})
    assert r.status_code == 401 and "invalid_token" in r.headers["WWW-Authenticate"]


async def test_crm_tokens_expire(asgi_client: ClientFactory, admin_token: str) -> None:
    app, cid, csecret = _crm(admin_token, token_ttl_s=0)
    c = asgi_client(app)
    token = (
        await c.post(
            "/oauth/token", data={"grant_type": "client_credentials", "client_id": cid, "client_secret": csecret}
        )
    ).json()["access_token"]
    assert (await c.get("/accounts", headers={"Authorization": f"Bearer {token}"})).status_code == 401


async def test_crm_cursor_pagination(asgi_client: ClientFactory, admin_token: str) -> None:
    app, cid, csecret = _crm(admin_token, extra_opportunities=4)
    c = asgi_client(app)
    token = (
        await c.post(
            "/oauth/token", data={"grant_type": "client_credentials", "client_id": cid, "client_secret": csecret}
        )
    ).json()["access_token"]
    c.headers["Authorization"] = f"Bearer {token}"
    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = (await c.get("/opportunities", params={"limit": 2, **({"cursor": cursor} if cursor else {})})).json()
        seen += [o["id"] for o in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 7 == len(set(seen))
    assert (await c.get("/opportunities", params={"cursor": "!!garbage"})).status_code == 400


# ------------------------------------------------------------------ billing
async def test_billing_bearer_validation_and_idempotency(asgi_client: ClientFactory, admin_token: str) -> None:
    app = create_billing_app(api_token="tok-123456", admin_token=admin_token)
    c = asgi_client(app)
    body = {
        "legal_name": "Acme GmbH",
        "address": "Berlin, DE",
        "currency_code": "EUR",
        "terms_code": "NET30",
        "external_ref": "acc-002",
    }
    assert (await c.post("/customers", json=body)).status_code == 401
    c.headers["Authorization"] = "Bearer tok-123456"
    bad = await c.post("/customers", json={**body, "currency_code": "Euro"})
    assert bad.status_code == 422 and bad.json()["detail"][0]["field"] == "currency_code"

    first = await c.post("/customers", json=body, headers={"Idempotency-Key": "k-1"})
    replay = await c.post("/customers", json=body, headers={"Idempotency-Key": "k-1"})
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json() and replay.headers["Idempotent-Replayed"] == "true"
    clash = await c.post("/customers", json={**body, "legal_name": "Other"}, headers={"Idempotency-Key": "k-1"})
    assert clash.status_code == 422
    assert (await c.post("/customers", json=body)).json()["id"] != first.json()["id"]  # no key: new resource

    inv = await c.post("/invoices", json={"customer_id": first.json()["id"], "total": 100.0, "external_ref": "opp-1"})
    assert inv.status_code == 201 and inv.json()["status"] == "open"
    assert (
        await c.post("/invoices", json={"customer_id": "cus-x", "total": 1, "external_ref": "r"})
    ).status_code == 422


# ------------------------------------------------------------------ e-sign
async def test_esign_api_key_and_deprecation(asgi_client: ClientFactory, admin_token: str) -> None:
    app = create_esign_app(api_key="key-123456", webhook_secret="whsec-123456", admin_token=admin_token)
    c = asgi_client(app)
    assert (await c.get("/v3/agreements/agr-001")).status_code == 401
    c.headers["X-API-Key"] = "key-123456"
    agr = (await c.get("/v3/agreements/agr-001")).json()
    assert agr["external_ref"] == "opp-001" and agr["signers"][0]["email"] == "dana@northwind.example"
    r = await c.post("/v2/envelopes", json={"document_id": "d1", "signer_email": "x@y.example"})
    assert r.status_code == 201 and r.headers["Deprecation"] == "true" and r.headers["Sunset"] == "2027-03-31"
    r = await c.post("/v2/envelopes", json={"document_id": "d1", "signers": [{"email": "x@y.example"}]})
    assert "Deprecation" not in r.headers
    spec = (await c.get("/openapi.json")).json()
    field = spec["components"]["schemas"]["EnvelopeCreate"]["properties"]["signer_email"]
    assert field["deprecated"] is True and field["x-replaced-by"] == "signers[].email"
    assert "agreement.completed" in spec["webhooks"] and spec["x-webhook-subscribe-path"] == "/v3/webhooks"


def test_webhook_signature_helpers() -> None:
    body = b'{"event":"agreement.completed"}'
    now = 1_800_000_000
    header = sign_webhook("whsec", body, now)
    assert verify_webhook_signature("whsec", body, header, now=now)
    assert not verify_webhook_signature("whsec", body + b" ", header, now=now)
    assert not verify_webhook_signature("other", body, header, now=now)
    assert not verify_webhook_signature("whsec", body, header, now=now + 301)
    assert not verify_webhook_signature("whsec", body, None)
    assert not verify_webhook_signature("whsec", body, "t=abc,v1=00")
    rotated = header + ",v1=" + "0" * 64  # multiple signatures (secret rotation) still verify
    assert verify_webhook_signature("whsec", body, rotated, now=now)


async def test_webhook_delivered_over_socket_and_signature_verifies(
    asgi_client: ClientFactory, admin_token: str, admin_headers: dict[str, str]
) -> None:
    received: list[tuple[bytes, dict[str, str]]] = []
    receiver = FastAPI()

    @receiver.post("/hooks/esign")
    async def hook(request: Request) -> dict[str, bool]:
        received.append((await request.body(), dict(request.headers)))
        return {"ok": True}

    secret = "whsec-" + "s" * 20
    app = create_esign_app(api_key="key-123456", webhook_secret=secret, admin_token=admin_token)
    c = asgi_client(app, headers={"X-API-Key": "key-123456"})
    with run_simulator(receiver) as receiver_url:
        sub = await c.post(
            "/v3/webhooks", json={"event": "agreement.completed", "callback_url": f"{receiver_url}/hooks/esign"}
        )
        assert sub.status_code == 201
        done = await c.post("/_sim/agreements/agr-002/complete", json={"amount": 120000.0}, headers=admin_headers)
    assert done.status_code == 200
    assert done.json()["deliveries"][0]["status_code"] == 200
    assert len(received) == 1
    body, headers = received[0]
    event = json.loads(body)
    assert event["agreement_id"] == "agr-002" and event["external_ref"] == "opp-002"
    assert verify_webhook_signature(secret, body, headers[SIGNATURE_HEADER.lower()])
    assert not verify_webhook_signature("wrong-secret", body, headers[SIGNATURE_HEADER.lower()])
    assert (await c.get("/v3/agreements/agr-002")).json()["status"] == "completed"


async def test_webhook_delivery_failures_are_recorded(
    asgi_client: ClientFactory, admin_token: str, admin_headers: dict[str, str]
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    app = create_esign_app(
        api_key="key-123456",
        webhook_secret="whsec-123456",
        admin_token=admin_token,
        delivery_transport=httpx.MockTransport(refuse),
        delivery_attempts=2,
    )
    c = asgi_client(app, headers={"X-API-Key": "key-123456"})
    await c.post("/v3/webhooks", json={"event": "agreement.completed", "callback_url": "http://receiver.test/h"})
    result = (await c.post("/_sim/agreements/agr-001/complete", headers=admin_headers)).json()
    assert result["deliveries"][0]["attempts"] == 2 and result["deliveries"][0]["error"] == "ConnectError"
    bad = await c.post("/v3/webhooks", json={"event": "agreement.completed", "callback_url": "not-a-url"})
    assert bad.status_code == 422


# ------------------------------------------------------------------ harness + CLI
def test_harness_serves_real_socket(admin_token: str) -> None:
    app = create_billing_app(api_token="tok-123456", admin_token=admin_token)
    with run_simulator(app) as url, httpx.Client(verify=shared_ssl_context()) as http:
        assert url.startswith("http://127.0.0.1:")
        assert http.get(f"{url}/health").json() == {"status": "ok", "service": "billing"}


@pytest.mark.parametrize("name", ["crm", "esign", "billing"])
def test_cli_builds_each_app(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_ADMIN_TOKEN", "pinned-admin")
    generated: dict[str, str] = {}
    app = build_app(name, generated)
    assert app.state.sim.name == name and app.state.sim.admin_token == "pinned-admin"
    assert "SIM_ADMIN_TOKEN" not in generated and generated  # the rest were generated
