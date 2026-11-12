"""DefaultToolBroker: allow-list, budget, error conversion, metrics, scrubbing."""

from __future__ import annotations

from typing import Any

import pytest

from app.platform.errors import Forbidden, UpstreamError
from app.platform.interfaces import HealthStatus, ToolBroker
from app.platform.observability.metrics import REGISTRY
from app.platform.sandbox.broker import DefaultToolBroker, ToolDenied, ToolFailed, safe_error, scrub


class Conn:
    system_id = "crm"

    def __init__(self, fail: BaseException | None = None) -> None:
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def tools(self) -> list[str]:
        return ["crm.get_account"]

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        self.calls.append((tool, args))
        if self.fail is not None:
            raise self.fail
        return {"id": args.get("account_id")}

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=True, latency_ms=1.0)

    async def aclose(self) -> None:
        return None


def tool_calls(tool: str, outcome: str) -> float:
    return REGISTRY.get_sample_value("pmaip_tool_calls_total", {"tool": tool, "outcome": outcome}) or 0.0


async def test_allowed_call_is_executed_recorded_and_counted() -> None:
    conn = Conn()
    broker = DefaultToolBroker({"crm.get_account": conn}, ["crm.get_account"], max_tool_calls=5)
    assert isinstance(broker, ToolBroker)
    before = tool_calls("crm.get_account", "ok")
    assert await broker.call("crm.get_account", {"account_id": "acc-1"}) == {"id": "acc-1"}
    assert conn.calls == [("crm.get_account", {"account_id": "acc-1"})]
    assert [(c.tool, c.ok) for c in broker.calls] == [("crm.get_account", True)]
    assert tool_calls("crm.get_account", "ok") == before + 1


async def test_disallowed_tool_never_reaches_connector() -> None:
    conn = Conn()
    broker = DefaultToolBroker({"crm.get_account": conn, "crm.delete": conn}, {"crm.get_account"}, max_tool_calls=5)
    before = tool_calls("<not-allowed>", "denied")
    with pytest.raises(ToolDenied) as err:
        await broker.call("crm.delete", {})
    assert isinstance(err.value, Forbidden)
    assert conn.calls == []
    assert broker.calls[-1].ok is False
    assert tool_calls("<not-allowed>", "denied") == before + 1


async def test_budget_counts_failed_calls_too() -> None:
    conn = Conn(fail=RuntimeError("down"))
    broker = DefaultToolBroker({"crm.get_account": conn}, {"crm.get_account"}, max_tool_calls=2)
    for _ in range(2):
        with pytest.raises(ToolFailed):
            await broker.call("crm.get_account", {})
    with pytest.raises(ToolDenied, match="budget of 2"):
        await broker.call("crm.get_account", {})
    assert len(conn.calls) == 2


async def test_allowed_tool_without_connector_is_denied() -> None:
    broker = DefaultToolBroker({}, {"crm.get_account"}, max_tool_calls=2)
    with pytest.raises(ToolDenied, match="no connector"):
        await broker.call("crm.get_account", {})


async def test_connector_errors_become_structured_scrubbed_payloads() -> None:
    class VendorError(UpstreamError):
        def __init__(self) -> None:
            super().__init__("vendor said no; Authorization: Bearer abc.def.ghi; client_secret=s3cr3t")
            self.upstream_status = 401
            self.detail = {"request": {"headers": {"X-API-Key": "k-123"}}, "fields": ["email"], "token": "t-1"}

    broker = DefaultToolBroker({"crm.get_account": Conn(fail=VendorError())}, {"crm.get_account"}, 3)
    with pytest.raises(ToolFailed) as err:
        await broker.call("crm.get_account", {})
    payload = err.value.payload
    assert payload["tool"] == "crm.get_account"
    assert payload["code"] == "upstream_error"
    assert payload["status"] == 401
    assert payload["detail"]["fields"] == ["email"]
    text = repr(payload)
    for secret in ("abc.def.ghi", "s3cr3t", "k-123", "t-1"):
        assert secret not in text
    assert broker.calls[-1].error == payload["message"]


@pytest.mark.parametrize(
    "raw, secret",
    [
        ("Authorization: Bearer eyJhbGciOi.payload.sig", "eyJhbGciOi"),
        ("GET https://api.example.com/x?api_key=AKIA123&x=1", "AKIA123"),
        ("connect to postgres://admin:pa55w0rd@db:5432/x failed", "pa55w0rd"),
        ('{"access_token": "tok-999"}', "tok-999"),
        ("x-api-key=zzz-111", "zzz-111"),
    ],
)
def test_scrub_removes_credentials(raw: str, secret: str) -> None:
    assert secret not in scrub(raw)


def test_safe_error_bounds_size() -> None:
    class Big(Exception):
        detail = ["x" * 400] * 50

    payload = safe_error("t", Big("m" * 5000))
    assert len(payload["message"]) <= 500
    assert payload["detail"] == "[detail too large]"
    assert payload["status"] is None
