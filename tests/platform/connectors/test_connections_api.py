"""Connections HTTP API + registry: validation, tools, health, tenant isolation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.platform.config import PlatformSettings
from app.platform.connectors.models import ConnectionRecord
from app.platform.connectors.registry import build_connector
from app.platform.core_models import AuditEvent, Tenant
from app.platform.db import Database
from app.platform.errors import Conflict, NotFound
from app.platform.principal import Principal, Role
from app.platform.secrets.store import DbSecretStore


async def _connect_crm(client: httpx.AsyncClient, sim: Any, system_id: str = "crm") -> httpx.Response:
    for name, value in (("crm_client_id", sim.creds["client_id"]), ("crm_client_secret", sim.creds["client_secret"])):
        assert (await client.put(f"/v1/secrets/{name}", json={"value": value})).status_code == 200
    return await client.put(
        f"/v1/connections/{system_id}",
        json={
            "base_url": sim.base_url,
            "spec": sim.spec(),
            "tool_prefix": "crm",
            "auth": {
                "kind": "oauth2_client_credentials",
                "secrets": {"client_id": "crm_client_id", "client_secret": "crm_client_secret"},
                "token_url": "/oauth/token",
                "scopes": ["crm.read"],
            },
        },
    )


async def test_create_list_tools_health(
    client: httpx.AsyncClient,
    tenant: Tenant,
    act_as: Callable[[Principal], None],
    principal_for: Callable[..., Principal],
    make_crm: Any,
    db: Database,
) -> None:
    sim = make_crm()
    act_as(principal_for(tenant, Role.ENGINEER))
    r = await _connect_crm(client, sim)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tool_count"] >= 8 and body["auth"]["secrets"]["client_secret"] == "crm_client_secret"
    assert sim.creds["client_secret"] not in r.text

    listed = (await client.get("/v1/connections")).json()
    assert [c["system_id"] for c in listed] == ["crm"]
    assert sim.creds["client_secret"] not in str(listed)

    tools = (await client.get("/v1/connections/crm/tools")).json()["tools"]
    by_name = {t["name"]: t for t in tools}
    assert by_name["crm.get_opportunity"]["method"] == "GET"
    assert by_name["crm.get_opportunity"]["inputSchema"]["required"] == ["opportunity_id"]

    health = (await client.post("/v1/connections/crm/health")).json()
    assert health["ok"] is True and health["detail"] == "HTTP 200"
    sim.faults(outage=True)
    health = (await client.post("/v1/connections/crm/health")).json()
    assert health["ok"] is False and health["detail"] == "HTTP 503"

    async with db.session() as s:
        actions = [a.action for a in (await s.execute(select(AuditEvent))).scalars()]
    assert "connection.created" in actions


async def test_registry_builds_working_connector(
    client: httpx.AsyncClient,
    tenant: Tenant,
    act_as: Callable[[Principal], None],
    principal_for: Callable[..., Principal],
    make_crm: Any,
    db: Database,
    settings: PlatformSettings,
) -> None:
    sim = make_crm()
    act_as(principal_for(tenant, Role.ADMIN))
    assert (await _connect_crm(client, sim)).status_code == 200
    store = DbSecretStore(db, settings)
    connector = await build_connector(db, store, settings, tenant.id, "crm")
    try:
        opp = await connector.call("crm.find_opportunity", {"contract_id": "agr-003"})
        assert opp["payment_terms"] == "Due on receipt"
    finally:
        await connector.aclose()

    # Disabled connections cannot be used for calls.
    r = await client.put("/v1/connections/crm", json={**(await _payload(client, sim)), "enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    with pytest.raises(Conflict):
        await build_connector(db, store, settings, tenant.id, "crm")


async def _payload(client: httpx.AsyncClient, sim: Any) -> dict[str, Any]:
    current = (await client.get("/v1/connections/crm")).json()
    return {"base_url": current["base_url"], "spec": sim.spec(), "tool_prefix": "crm", "auth": current["auth"]}


@pytest.mark.parametrize(
    ("patch", "needle"),
    [
        ({"base_url": "ftp://example.com"}, "base_url"),
        ({"base_url": "https://user:pw@example.com"}, "credentials"),
        ({"spec": {"swagger": "2.0", "paths": {}}}, "OpenAPI"),
        ({"auth": {"kind": "api_key", "secrets": {}}}, "api_key"),
        ({"auth": {"kind": "api_key", "secrets": {"api_key": "missing_secret"}}}, "unknown secret"),
        ({"auth": {"kind": "bearer", "secrets": {"token": "Bearer abc.def ghi"}}}, "NAME of a stored secret"),
        (
            {"auth": {"kind": "oauth2_client_credentials", "secrets": {"client_id": "a", "client_secret": "b"}}},
            "token_url",
        ),
        ({"tool_prefix": "Bad Prefix"}, "tool_prefix"),
    ],
)
async def test_validation(
    client: httpx.AsyncClient,
    tenant: Tenant,
    act_as: Callable[[Principal], None],
    principal_for: Callable[..., Principal],
    patch: dict[str, Any],
    needle: str,
) -> None:
    act_as(principal_for(tenant, Role.ENGINEER))
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "x", "version": "1"},
        "paths": {"/things": {"get": {"operationId": "list_things", "responses": {}}}},
    }
    payload = {"base_url": "https://api.example.com", "spec": spec, **patch}
    r = await client.put("/v1/connections/sys", json=payload)
    assert r.status_code == 422, r.text
    assert needle in r.json()["error"]["message"]
    assert "abc.def" not in r.text


async def test_roles(
    client: httpx.AsyncClient,
    tenant: Tenant,
    act_as: Callable[[Principal], None],
    principal_for: Callable[..., Principal],
    make_crm: Any,
) -> None:
    sim = make_crm()
    act_as(principal_for(tenant, Role.VIEWER))
    r = await client.put("/v1/connections/crm", json={"base_url": sim.base_url, "spec": sim.spec()})
    assert r.status_code == 403
    act_as(principal_for(tenant, Role.ENGINEER))
    assert (await _connect_crm(client, sim)).status_code == 200
    assert (await client.delete("/v1/connections/crm")).status_code == 403
    act_as(principal_for(tenant, Role.ADMIN))
    assert (await client.delete("/v1/connections/crm")).status_code == 204
    assert (await client.get("/v1/connections/crm")).status_code == 404


async def test_tenant_isolation(
    client: httpx.AsyncClient,
    tenant: Tenant,
    other_tenant: Tenant,
    act_as: Callable[[Principal], None],
    principal_for: Callable[..., Principal],
    make_crm: Any,
    db: Database,
    settings: PlatformSettings,
) -> None:
    sim = make_crm()
    act_as(principal_for(tenant, Role.ADMIN))
    assert (await _connect_crm(client, sim)).status_code == 200

    act_as(principal_for(other_tenant, Role.ADMIN, user_id="intruder"))
    assert (await client.get("/v1/connections")).json() == []
    for method, path in (
        ("GET", "/v1/connections/crm"),
        ("GET", "/v1/connections/crm/tools"),
        ("POST", "/v1/connections/crm/health"),
        ("DELETE", "/v1/connections/crm"),
    ):
        r = await client.request(method, path)
        assert r.status_code == 404, (method, path, r.text)
    # Cannot reference the first tenant's secrets by name.
    r = await client.put(
        "/v1/connections/crm",
        json={
            "base_url": sim.base_url,
            "spec": sim.spec(),
            "auth": {"kind": "api_key", "secrets": {"api_key": "crm_client_secret"}},
        },
    )
    assert r.status_code == 422 and "unknown secret" in r.text
    with pytest.raises(NotFound):
        await build_connector(db, DbSecretStore(db, settings), settings, other_tenant.id, "crm")

    async with db.session() as s:
        rows = list((await s.execute(select(ConnectionRecord))).scalars())
    assert [(r.tenant_id, r.system_id) for r in rows] == [(tenant.id, "crm")]
