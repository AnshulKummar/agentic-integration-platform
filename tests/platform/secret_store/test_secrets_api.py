"""Secrets HTTP API: write-only values, roles, audit, tenant isolation."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from sqlalchemy import select

from app.platform.core_models import AuditEvent, Tenant
from app.platform.db import Database
from app.platform.principal import Principal, Role

VALUE = "sk_live_marker_7f3a9c2e"


async def test_put_returns_metadata_only_and_list_never_returns_values(
    client: httpx.AsyncClient,
    tenant: Tenant,
    act_as: Callable[[Principal], None],
    principal_for: Callable[..., Principal],
    db: Database,
) -> None:
    act_as(principal_for(tenant, Role.ENGINEER))
    r = await client.put("/v1/secrets/billing_token", json={"value": VALUE, "description": "Billing API"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert VALUE not in r.text and "value" not in body
    assert body["name"] == "billing_token" and body["version"] == 1 and len(body["fingerprint"]) == 12

    r2 = await client.put("/v1/secrets/billing_token", json={"value": VALUE + "-2"})
    assert r2.json()["version"] == 2

    listed = await client.get("/v1/secrets")
    assert listed.status_code == 200
    assert VALUE not in listed.text
    assert [s["name"] for s in listed.json()] == ["billing_token"]

    async with db.session() as s:
        audits = list((await s.execute(select(AuditEvent).where(AuditEvent.target_type == "secret"))).scalars())
    assert [a.action for a in audits] and all(VALUE not in str(a.detail) for a in audits)
    assert all(a.actor_id == "user-1" for a in audits)


async def test_validation_errors_do_not_echo_value(
    client: httpx.AsyncClient,
    tenant: Tenant,
    act_as: Callable[[Principal], None],
    principal_for: Callable[..., Principal],
) -> None:
    act_as(principal_for(tenant, Role.ADMIN))
    long_value = "Q" * (64 * 1024 + 5)
    r = await client.put("/v1/secrets/big", json={"value": long_value})
    assert r.status_code == 422 and "QQQQ" not in r.text
    r = await client.put("/v1/secrets/bad%20name", json={"value": VALUE})
    assert r.status_code == 422 and VALUE not in r.text
    r = await client.put("/v1/secrets/x", json={"value": VALUE, "unexpected": 1})
    assert r.status_code == 422 and VALUE not in r.text


async def test_roles(
    client: httpx.AsyncClient,
    tenant: Tenant,
    act_as: Callable[[Principal], None],
    principal_for: Callable[..., Principal],
) -> None:
    act_as(principal_for(tenant, Role.VIEWER))
    assert (await client.put("/v1/secrets/a", json={"value": VALUE})).status_code == 403
    act_as(principal_for(tenant, Role.PM))
    assert (await client.put("/v1/secrets/a", json={"value": VALUE})).status_code == 403
    act_as(principal_for(tenant, Role.ENGINEER))
    assert (await client.put("/v1/secrets/a", json={"value": VALUE})).status_code == 200
    assert (await client.delete("/v1/secrets/a")).status_code == 403  # delete is admin-only
    act_as(principal_for(tenant, Role.ADMIN))
    assert (await client.delete("/v1/secrets/a")).status_code == 204
    assert (await client.delete("/v1/secrets/a")).status_code == 404
    assert (await client.get("/v1/secrets")).json() == []


async def test_tenant_isolation(
    client: httpx.AsyncClient,
    tenant: Tenant,
    other_tenant: Tenant,
    act_as: Callable[[Principal], None],
    principal_for: Callable[..., Principal],
) -> None:
    act_as(principal_for(tenant, Role.ADMIN))
    assert (await client.put("/v1/secrets/crm_secret", json={"value": VALUE})).status_code == 200

    act_as(principal_for(other_tenant, Role.ADMIN, user_id="intruder"))
    assert (await client.get("/v1/secrets")).json() == []
    assert (await client.delete("/v1/secrets/crm_secret")).status_code == 404
    # Same name in another tenant is an independent secret (version 1).
    r = await client.put("/v1/secrets/crm_secret", json={"value": "theirs-value"})
    assert r.json()["version"] == 1

    act_as(principal_for(tenant, Role.ADMIN))
    secrets = (await client.get("/v1/secrets")).json()
    assert len(secrets) == 1 and secrets[0]["version"] == 1
