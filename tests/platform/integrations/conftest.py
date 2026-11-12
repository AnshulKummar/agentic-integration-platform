"""Fixtures for the integrations area (helpers are fixtures: tests use importlib mode)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.demo.kg_store import load_fixture_json
from app.platform.core_models import Integration, IntegrationVersion, Tenant, VersionStatus
from app.platform.db import Database
from app.platform.integrations import service
from app.platform.principal import Principal, Role

LOOP1_INTENT = (
    "Connect our CRM to the billing system so every closed-won account becomes a billing customer, "
    "including tax calculations."
)
#: The engineer's corrections during loop-1 review (the demo's ENGINEER_FIXES).
ENGINEER_EDITS: list[dict[str, str]] = [
    {"target": "Customer.currency_code", "transform": "to_iso4217()"},
    {"target": "Customer.terms_code", "transform": "TERMS_MAP lookup"},
    {"target": "TaxQuote.jurisdiction", "source": "Account.shipping_address", "transform": "geocode(ship-to)"},
]


@pytest.fixture
def loop1_intent() -> str:
    return LOOP1_INTENT


@pytest.fixture
def engineer_edits() -> list[dict[str, str]]:
    return [dict(e) for e in ENGINEER_EDITS]


@pytest.fixture
def spec_v3() -> dict[str, Any]:
    return load_fixture_json("esign_openapi_v3_0.json")


Actor = Callable[..., Principal]


@pytest.fixture
def as_user(act_as: Callable[[Principal], None], principal_for: Callable[..., Principal]) -> Actor:
    """as_user(tenant, Role.PM, user_id="pm-1") -> Principal; subsequent requests run as that principal."""

    def _as(tenant: Tenant, *roles: Role, user_id: str) -> Principal:
        principal = principal_for(tenant, *roles, user_id=user_id, email=f"{user_id}@example.com")
        act_as(principal)
        return principal

    return _as


@pytest.fixture
def go_live(db: Database) -> Callable[[str, str], Awaitable[service.LearnOutcome]]:
    """Stand-in for the deployments area: mark a version live, then call the learning hook."""

    async def _go_live(tenant_id: str, version_id: str) -> service.LearnOutcome:
        async with db.session(tenant_id) as s:
            stmt = select(IntegrationVersion).where(IntegrationVersion.id == version_id)
            version = (await s.execute(stmt)).scalar_one()
            version.status = VersionStatus.LIVE
            integ = (await s.execute(select(Integration).where(Integration.id == version.integration_id))).scalar_one()
            integ.active_version_id = version.id
            await s.flush()
            outcome = await service.learn_from_version(s, tenant_id, version_id)
            await s.commit()
            return outcome

    return _go_live


@pytest.fixture
def run_loop1(
    client: httpx.AsyncClient, as_user: Actor, loop1_intent: str, engineer_edits: list[dict[str, str]]
) -> Callable[[Tenant], Awaitable[dict[str, Any]]]:
    """Bootstrap + loop 1 through the API up to 'approved'. Returns the final version JSON."""

    async def _run(tenant: Tenant) -> dict[str, Any]:
        as_user(tenant, Role.ADMIN, user_id="admin-1")
        r = await client.post("/v1/kg/bootstrap")
        assert r.status_code == 200, r.text
        as_user(tenant, Role.PM, user_id="pm-author")
        r = await client.post("/v1/integrations", json={
            "intent": loop1_intent, "sourceSystem": "sys-crm", "targetSystem": "sys-billing",
            "trigger": "proc-closedwon",
        })
        assert r.status_code == 201, r.text
        draft = r.json()
        iid, vid = draft["integration"]["id"], draft["version"]["id"]
        base = f"/v1/integrations/{iid}/versions/{vid}"
        as_user(tenant, Role.ENGINEER, user_id="eng-1")
        r = await client.patch(f"{base}/mappings", json={"edits": engineer_edits})
        assert r.status_code == 200, r.text
        as_user(tenant, Role.PM, user_id="pm-2")
        r = await client.post(f"{base}/approvals", json={"role": "pm", "comment": "Matches the roadmap intent."})
        assert r.status_code == 200, r.text
        as_user(tenant, Role.ENGINEER, user_id="eng-1")
        r = await client.post(f"{base}/approvals", json={"role": "eng", "comment": "Rollback plan OK."})
        assert r.status_code == 200, r.text
        version: dict[str, Any] = r.json()
        assert version["status"] == "approved"
        return version

    return _run


@pytest.fixture
async def no_learning_tenant(new_tenant: Callable[[str], Awaitable[Tenant]], db: Database) -> Tenant:
    tenant = await new_tenant("initech")
    async with db.session() as s:
        row = await s.get(Tenant, tenant.id)
        assert row is not None
        row.learning_enabled = False
        await s.commit()
    tenant.learning_enabled = False
    return tenant
