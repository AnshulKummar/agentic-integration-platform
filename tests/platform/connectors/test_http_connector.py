"""HttpConnector against the vendor simulators over real sockets."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from app.platform.connectors.http import ConnectorHTTPError, HttpConnector, UnknownTool, parse_retry_after
from app.platform.errors import UpstreamError, ValidationFailed
from app.platform.interfaces import Connector
from app.platform.observability.metrics import REGISTRY

MakeConnector = Callable[..., Awaitable[HttpConnector]]


def _retries(system: str, reason: str) -> float:
    value = REGISTRY.get_sample_value("pmaip_connector_retries_total", {"system": system, "reason": reason})
    return value or 0.0


# ------------------------------------------------------------------ tools / binding
async def test_tools_generated_from_live_openapi(make_crm: Any, make_connector: MakeConnector) -> None:
    connector = await make_connector(make_crm())
    assert isinstance(connector, Connector)
    names = connector.tools()
    assert {"crm.get_opportunity", "crm.find_opportunity", "crm.find_contact", "crm.update_opportunity"} <= set(names)
    assert not any("_sim" in n or "oauth" in n for n in names)  # admin/token endpoints are hidden
    with pytest.raises(UnknownTool):
        await connector.call("crm.drop_database", {})
    with pytest.raises(ValidationFailed):
        await connector.call("crm.get_opportunity", {})


# ------------------------------------------------------------------ OAuth2
async def test_oauth_token_fetched_once_and_reused(make_crm: Any, make_connector: MakeConnector) -> None:
    sim = make_crm()
    connector = await make_connector(sim)
    opp = await connector.call("crm.get_opportunity", {"opportunity_id": "opp-001"})
    assert opp["amount"] == 48000.0 and opp["contract_id"] == "agr-001"
    found = await connector.call("crm_find_opportunity", {"contract_id": "agr-002"})  # underscore alias
    assert found["id"] == "opp-002"
    updated = await connector.call("crm.update_opportunity", {"opportunity_id": "opp-003", "stage": "Invoiced"})
    assert updated["stage"] == "Invoiced"
    assert sim.state.counters["tokens_issued"] == 1


async def test_oauth_token_refreshed_proactively_on_expiry(make_crm: Any, make_connector: MakeConnector) -> None:
    sim = make_crm(token_ttl_s=60)
    now = [1000.0]
    connector = await make_connector(sim, clock=lambda: now[0])
    await connector.call("crm.get_account", {"account_id": "acc-001"})
    now[0] += 55  # past expires_in - skew (60 - 6)
    await connector.call("crm.get_account", {"account_id": "acc-002"})
    assert sim.state.counters["tokens_issued"] == 2


async def test_oauth_401_on_server_expiry_refreshes_once(make_crm: Any, make_connector: MakeConnector) -> None:
    sim = make_crm(token_ttl_s=1)
    frozen = [0.0]  # connector believes its token is still valid
    connector = await make_connector(sim, clock=lambda: frozen[0])
    await connector.call("crm.get_account", {"account_id": "acc-001"})
    time.sleep(1.2)  # server-side expiry
    before = _retries(connector.system_id, "auth_refresh")
    account = await connector.call("crm.get_account", {"account_id": "acc-003"})
    assert account["name"] == "Globex Ltd"
    assert sim.state.counters["tokens_issued"] == 2
    assert _retries(connector.system_id, "auth_refresh") == before + 1

    sim.admin("POST", "/_sim/revoke-tokens")
    assert (await connector.call("crm.get_contact", {"contact_id": "con-001"}))["email"] == "dana@northwind.example"
    assert sim.state.counters["tokens_issued"] == 3


async def test_oauth_bad_client_secret_fails_without_leaking(
    make_crm: Any, make_connector: MakeConnector, secret_store: Any, tenant: Any
) -> None:
    sim = make_crm()
    connector = await make_connector(sim)
    await secret_store.put(tenant.id, "crm_client_secret", "wrong-secret-value")
    with pytest.raises(ConnectorHTTPError) as err:
        await connector.call("crm.get_account", {"account_id": "acc-001"})
    assert err.value.upstream_status == 401
    assert "wrong-secret-value" not in str(err.value) and sim.creds["client_id"] not in str(err.value)


# ------------------------------------------------------------------ retries
async def test_429_retried_honouring_retry_after(
    make_billing: Any, make_connector: MakeConnector, sleeps: list[float]
) -> None:
    sim = make_billing()
    connector = await make_connector(sim)
    sim.faults(throttle_next=2, retry_after_s=7)
    before = _retries(connector.system_id, "429")
    page = await connector.call("billing.list_customers", {})
    assert page == {"items": [], "next_cursor": None}
    assert sleeps == [7.0, 7.0]
    assert _retries(connector.system_id, "429") == before + 2


async def test_real_rate_limit_window_is_waited_out(make_billing: Any, make_connector: MakeConnector) -> None:
    sim = make_billing(rate_limit=2, rate_window_s=1.0)
    connector = await make_connector(sim, real_sleep=True)
    started = time.monotonic()
    for _ in range(3):
        await connector.call("billing.list_invoices", {})
    assert sim.state.counters["throttled"] >= 1
    assert time.monotonic() - started >= 0.5


async def test_retry_after_above_cap_is_not_waited(
    make_billing: Any, make_connector: MakeConnector, sleeps: list[float]
) -> None:
    sim = make_billing()
    connector = await make_connector(sim, max_retry_after_s=60.0)
    sim.faults(throttle_next=1, retry_after_s=3600)
    with pytest.raises(ConnectorHTTPError) as err:
        await connector.call("billing.list_customers", {})
    assert err.value.upstream_status == 429 and sleeps == []


async def test_post_without_idempotency_key_is_not_retried(
    make_billing: Any, make_connector: MakeConnector, sleeps: list[float]
) -> None:
    sim = make_billing()
    connector = await make_connector(sim)
    customer = {
        "legal_name": "Acme GmbH",
        "address": "Berlin, DE",
        "currency_code": "EUR",
        "terms_code": "NET30",
        "external_ref": "acc-002",
    }
    sim.faults(error_rate=1.0, error_status=503)
    requests_before = sim.state.counters.get("requests", 0)
    with pytest.raises(UpstreamError) as err:
        await connector.call("billing.create_customer", customer)
    assert isinstance(err.value, ConnectorHTTPError) and err.value.upstream_status == 503
    assert sleeps == [] and sim.state.counters["requests"] == requests_before + 1

    sim.admin("DELETE", "/_sim/faults")
    sim.faults(throttle_next=1)
    with pytest.raises(ConnectorHTTPError):
        await connector.call("billing.create_customer", customer)
    assert sleeps == []


async def test_idempotent_post_retried_and_deduplicated(
    make_billing: Any, make_connector: MakeConnector, sleeps: list[float]
) -> None:
    sim = make_billing()
    connector = await make_connector(sim)
    customer = {
        "legal_name": "Northwind Traders Inc.",
        "address": "100 Main St, Boston, MA, US",
        "currency_code": "USD",
        "terms_code": "NET30",
        "external_ref": "acc-001",
    }
    sim.faults(throttle_next=1, retry_after_s=1)
    first = await connector.call("billing.create_customer", {**customer, "_idempotency_key": "evt-123"})
    assert sleeps == [1.0]
    replay = await connector.call("billing.create_customer", {**customer, "_idempotency_key": "evt-123"})
    assert replay == first
    assert sim.state.counters["idempotent_replays"] == 1
    customers = await connector.call("billing.list_customers", {"_paginate": True})
    assert [c["id"] for c in customers["items"]] == [first["id"]]

    with pytest.raises(ConnectorHTTPError) as err:  # same key, different payload
        await connector.call(
            "billing.create_customer", {**customer, "legal_name": "Other", "_idempotency_key": "evt-123"}
        )
    assert err.value.upstream_status == 422

    # Without a key, two calls create two customers (the reason keys matter).
    await connector.call("billing.create_customer", customer)
    await connector.call("billing.create_customer", customer)
    assert len((await connector.call("billing.list_customers", {}))["items"]) == 3


async def test_connect_errors_retried_even_for_post(
    make_billing: Any, make_connector: MakeConnector, sleeps: list[float]
) -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.method)
        if len(attempts) < 3:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(201, json={"id": "cus-1"})

    connector = await make_connector(make_billing(), transport=httpx.MockTransport(handler))
    result = await connector.call(
        "billing.create_customer",
        {"legal_name": "x", "address": "y", "currency_code": "USD", "terms_code": "NET30", "external_ref": "r"},
    )
    assert result == {"id": "cus-1"} and attempts == ["POST"] * 3 and len(sleeps) == 2
    assert all(0 < s <= 8.0 for s in sleeps)


async def test_read_timeout_retried_for_get_not_for_post(make_billing: Any, make_connector: MakeConnector) -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.method)
        raise httpx.ReadTimeout("slow", request=request)

    connector = await make_connector(make_billing(), max_retries=2, transport=httpx.MockTransport(handler))
    with pytest.raises(ConnectorHTTPError):
        await connector.call("billing.get_customer", {"customer_id": "cus-1"})
    assert attempts == ["GET"] * 3
    attempts.clear()
    with pytest.raises(ConnectorHTTPError, match="not retried"):
        await connector.call("billing.create_invoice", {"customer_id": "c", "total": 1, "external_ref": "r"})
    assert attempts == ["POST"]


async def test_outage_maps_to_upstream_error_after_retries(
    make_esign: Any, make_connector: MakeConnector, sleeps: list[float]
) -> None:
    sim = make_esign()
    connector = await make_connector(sim, max_retries=3)
    sim.faults(outage=True)
    with pytest.raises(UpstreamError) as err:
        await connector.call("esign.get_agreement", {"id": "agr-001"})
    assert isinstance(err.value, ConnectorHTTPError) and err.value.upstream_status == 503 and err.value.retryable
    assert len(sleeps) == 3 and err.value.status == 502
    assert sim.creds["api_key"] not in err.value.message
    assert (await connector.health()).ok is False


async def test_latency_fault_hits_timeout(make_esign: Any, make_connector: MakeConnector) -> None:
    sim = make_esign()
    connector = await make_connector(sim, max_retries=0)
    connector._client.timeout = httpx.Timeout(0.2)
    sim.faults(latency_ms=1000)
    with pytest.raises(ConnectorHTTPError, match="ReadTimeout"):
        await connector.call("esign.get_agreement", {"id": "agr-001"})


# ------------------------------------------------------------------ pagination / esign / health
async def test_cursor_pagination(make_crm: Any, make_connector: MakeConnector) -> None:
    sim = make_crm(extra_opportunities=7)
    connector = await make_connector(sim)
    one_page = await connector.call("crm.list_opportunities", {"limit": 3})
    assert len(one_page["items"]) == 3 and one_page["next_cursor"]
    everything = await connector.call("crm.list_opportunities", {"limit": 3, "_paginate": True})
    assert len(everything["items"]) == 10 and everything["pages"] == 4
    assert everything["next_cursor"] is None and everything["truncated"] is False
    assert len({o["id"] for o in everything["items"]}) == 10
    capped = await connector.call("crm.list_opportunities", {"limit": 3, "_paginate": True, "_max_pages": 2})
    assert len(capped["items"]) == 6 and capped["truncated"] is True and capped["next_cursor"]
    filtered = await connector.call("crm.list_opportunities", {"stage": "Closed Won", "_paginate": True})
    assert {o["id"] for o in filtered["items"]} == {"opp-001", "opp-002", "opp-003"}
    with pytest.raises(ValidationFailed):
        await connector.call("crm.list_opportunities", {"_paginate": True, "_max_pages": 1000})
    with pytest.raises(ValidationFailed):
        await connector.call("crm.update_opportunity", {"opportunity_id": "opp-001", "stage": "x", "_paginate": True})


async def test_esign_api_key_and_deprecated_field(make_esign: Any, make_connector: MakeConnector) -> None:
    sim = make_esign()
    connector = await make_connector(sim)
    agreement = await connector.call(
        "esign.create_agreement",
        {"template_id": "tpl-msa", "external_ref": "opp-001", "signers": [{"email": "dana@northwind.example"}]},
    )
    assert agreement["status"] == "out_for_signature" and agreement["signers"][0]["role"] == "signer"
    fields = await connector.call("esign.get_agreement_fields", {"id": "agr-001"})
    assert fields == {"amount": 48000.0, "payment_terms": "NET30"}
    envelope = await connector.call("esign.send_envelope", {"document_id": "doc-1", "signer_email": "a@b.example"})
    assert envelope["signers"] == [{"email": "a@b.example", "role": "signer"}]
    assert sim.state.counters["deprecated_field_used"] == 1
    schema = next(t for t in connector.tool_definitions() if t["name"] == "esign.send_envelope")["inputSchema"]
    assert schema["properties"]["signer_email"]["deprecated"] is True


async def test_health_ok_and_fail(make_billing: Any, make_connector: MakeConnector) -> None:
    sim = make_billing()
    connector = await make_connector(sim)
    status = await connector.health()
    assert status.ok and status.detail == "HTTP 200" and status.latency_ms >= 0
    sim.faults(outage=True)
    status = await connector.health()
    assert not status.ok and status.detail == "HTTP 503"


async def test_health_unreachable(make_billing: Any, make_connector: MakeConnector) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    connector = await make_connector(make_billing(), transport=httpx.MockTransport(handler))
    status = await connector.health()
    assert not status.ok and status.detail == "ConnectError"


def test_parse_retry_after() -> None:
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    assert parse_retry_after(format_datetime(now + timedelta(seconds=30), usegmt=True), now=now) == 30.0
    assert parse_retry_after(format_datetime(now - timedelta(seconds=30), usegmt=True), now=now) == 0.0
