"""Health endpoints, /metrics, request ids, structured logs, tracing fallback."""

from __future__ import annotations

import itertools
import logging
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from app.platform.config import PlatformSettings
from app.platform.db import Database
from app.platform.observability import tracing
from app.platform.observability.logs import JsonFormatter, configure_logging
from app.platform.observability.metrics import REGISTRY
from app.platform.principal import Role


async def test_healthz_and_readyz_ok(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"database": "ok"}}


async def test_readyz_503_when_database_unreachable(client: httpx.AsyncClient, app: FastAPI, tmp_path) -> None:  # type: ignore[no-untyped-def]
    broken = Database(f"sqlite+aiosqlite:///{(tmp_path / 'missing-dir' / 'nope.db').as_posix()}")
    app.state.db = broken
    try:
        r = await client.get("/readyz")
    finally:
        await broken.dispose()
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"].startswith("unreachable")
    assert "missing-dir" not in r.text  # no DSN / path leakage


async def test_readyz_checks_docker_binary(settings: PlatformSettings, db: Database, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.platform.app import create_app
    from app.platform.observability import router as obs_router

    app = create_app(settings.model_copy(update={"sandbox_backend": "docker"}))
    app.state.db = db
    monkeypatch.setattr(obs_router.shutil, "which", lambda _: None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"] == {"database": "ok", "docker": "docker binary not found on PATH"}


async def test_request_id_echoed_or_generated(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-ID": "abc-123.x"})
    assert r.headers["x-request-id"] == "abc-123.x"
    generated = (await client.get("/healthz")).headers["x-request-id"]
    assert len(generated) == 32
    # Unsafe values are replaced, not reflected.
    r = await client.get("/healthz", headers={"X-Request-ID": "bad value<script>"})
    assert r.headers["x-request-id"] != "bad value<script>" and len(r.headers["x-request-id"]) == 32


async def test_metrics_use_route_templates_after_activity(  # type: ignore[no-untyped-def]
    client: httpx.AsyncClient, act_as, principal_for, tenant
) -> None:
    act_as(principal_for(tenant, Role.VIEWER))
    route = "/v1/deployments/{deployment_id}"
    before = REGISTRY.get_sample_value(
        "pmaip_http_requests_total", {"method": "GET", "route": route, "status": "404"}
    ) or 0.0
    for i in range(3):
        assert (await client.get(f"/v1/deployments/missing-{i}")).status_code == 404
    await client.get("/definitely/not/a/route")

    r = await client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    text = r.text
    assert "pmaip_http_requests_total" in text and "pmaip_http_request_duration_seconds_bucket" in text
    assert REGISTRY.get_sample_value(
        "pmaip_http_requests_total", {"method": "GET", "route": route, "status": "404"}
    ) == before + 3
    assert "missing-0" not in text  # raw paths never become labels
    assert 'route="<unmatched>"' in text
    for name in ("pmaip_rollbacks_total", "pmaip_deployments_total", "pmaip_integration_executions_total"):
        assert f"# TYPE {name} counter" in text or f"# TYPE {name.removesuffix('_total')} counter" in text


async def test_metrics_disabled_returns_404(settings: PlatformSettings, db: Database) -> None:
    from app.platform.app import create_app

    app = create_app(settings.model_copy(update={"metrics_enabled": False}))
    app.state.db = db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/metrics")).status_code == 404


async def test_json_logs_carry_request_and_tenant_id(  # type: ignore[no-untyped-def]
    client: httpx.AsyncClient, act_as, principal_for, tenant, json_logs
) -> None:
    act_as(principal_for(tenant, Role.VIEWER))
    r = await client.get("/v1/deployments", headers={"X-Request-ID": "req-42"})
    assert r.status_code == 200
    lines = [rec for rec in json_logs() if rec.get("request_id") == "req-42"]
    access = next(rec for rec in lines if rec["message"] == "request")
    assert access["tenant_id"] == tenant.id
    assert access["route"] == "/v1/deployments" and access["status"] == 200 and access["method"] == "GET"
    assert access["level"] == "INFO" and access["logger"] == "app.platform.http"
    assert {"timestamp", "duration_ms"} <= set(access)


async def test_slow_request_logs_warning(client: httpx.AsyncClient, monkeypatch, json_logs) -> None:  # type: ignore[no-untyped-def]
    from app.platform.observability import middleware

    ticks = itertools.count()
    # Only the middleware's clock is faked: each reading is 3 s after the previous one.
    monkeypatch.setattr(middleware, "time", SimpleNamespace(perf_counter=lambda: next(ticks) * 3.0))
    await client.get("/healthz")
    slow = [rec for rec in json_logs() if rec["message"] == "slow request"]
    assert slow and slow[0]["level"] == "WARNING" and slow[0]["duration_ms"] == 3000.0


def test_json_formatter_redacts_secret_like_fields() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.api_key = "sk-live-123"
    record.detail = {"password": "hunter2", "system": "crm"}
    out = JsonFormatter().format(record)
    assert "sk-live-123" not in out and "hunter2" not in out
    assert '"message": "hello world"' in out and '"system": "crm"' in out


def test_configure_logging_is_idempotent() -> None:
    root = logging.getLogger()
    configure_logging("INFO", json_lines=True)
    configure_logging("INFO", json_lines=False)
    ours = [h for h in root.handlers if getattr(h, "_pmaip_handler", False)]
    assert len(ours) == 1


def test_tracing_disabled_without_endpoint_or_package(  # type: ignore[no-untyped-def]
    settings: PlatformSettings, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(tracing, "_disabled_logged", False)
    with_endpoint = settings.model_copy(update={"otel_endpoint": "http://otel:4318"})
    with caplog.at_level(logging.INFO, logger=tracing.__name__):
        assert tracing.setup_tracing(FastAPI(), with_endpoint) is False
        assert tracing.setup_tracing(FastAPI(), settings) is False
    disabled = [r for r in caplog.records if r.getMessage().startswith("tracing disabled")]
    assert len(disabled) == 1 and "opentelemetry" in disabled[0].getMessage()
