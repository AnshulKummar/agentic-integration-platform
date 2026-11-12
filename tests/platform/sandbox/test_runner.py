"""DefaultIntegrationRunner end to end: DB version -> sandbox -> ExecutionRecord + metrics."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import select

from app.demo.runtime import sandbox_factory
from app.platform.config import PlatformSettings
from app.platform.core_models import ExecutionRecord, Integration, IntegrationVersion, Tenant, VersionStatus
from app.platform.db import Database
from app.platform.errors import Conflict, NotFound, PlatformError, ValidationFailed
from app.platform.interfaces import Connector, IntegrationRunner
from app.platform.observability.metrics import REGISTRY
from app.platform.sandbox import build_sandbox
from app.platform.sandbox.codegen import render_runnable, required_tools
from app.platform.sandbox.runner import DefaultIntegrationRunner, default_tool_routing, event_id_of


@pytest.fixture
def world_and_factory(mcp_connector: Any) -> tuple[Any, Callable[[str, str], Awaitable[Connector]], list[Any]]:
    apps, mcp = sandbox_factory()()
    created: list[Any] = []

    async def factory(tenant_id: str, system_id: str) -> Connector:
        conn = mcp_connector(mcp, system_id)
        created.append((tenant_id, conn))
        return conn  # type: ignore[no-any-return]

    return apps.world, factory, created


async def store_version(
    db: Database, tenant: Tenant, integ: Any, *, status: str = VersionStatus.APPROVED,
    allowed: list[str] | None = None, code: str | None = None,
) -> IntegrationVersion:
    rows = [r.to_json() for r in integ.mappings]
    async with db.session(tenant.id) as s:
        integration = Integration(name=integ.name, source_system="crm", target_system="billing", trigger=integ.trigger)
        s.add(integration)
        await s.flush()
        version = IntegrationVersion(
            integration_id=integration.id,
            number=1,
            status=status,
            mappings=rows,
            code=code if code is not None else render_runnable(
                rows, name=integ.name, source_system=integ.source, target_system=integ.target, trigger=integ.trigger
            ),
            allowed_tools=allowed if allowed is not None else required_tools(rows, trigger=integ.trigger),
        )
        s.add(version)
        await s.commit()
        return version


async def records(db: Database, tenant: Tenant) -> list[ExecutionRecord]:
    async with db.session(tenant.id) as s:
        return list((await s.execute(select(ExecutionRecord))).scalars())


def executions(tenant: str, integration: str, stage: str, outcome: str) -> float:
    labels = {"tenant": tenant, "integration": integration, "stage": stage, "outcome": outcome}
    return REGISTRY.get_sample_value("pmaip_integration_executions_total", labels) or 0.0


async def test_runs_version_end_to_end_and_writes_execution_record(
    db: Database, tenant: Tenant, settings: PlatformSettings, demo_integrations: Any, world_and_factory: Any
) -> None:
    world, factory, created = world_and_factory
    version = await store_version(db, tenant, demo_integrations.loop2)
    runner = DefaultIntegrationRunner(db, build_sandbox(settings), settings, factory)
    assert isinstance(runner, IntegrationRunner)
    before = executions(tenant.id, version.integration_id, "staging", "ok")

    res = await runner.run(tenant.id, version.id, {"agreement_id": "agr-002", "event": "agreement.completed"},
                           stage="staging", deployment_id="dep-1")

    assert res.ok, res.error
    assert res.output is not None and res.output["opportunity_id"] == "opp-002"
    assert len(world.customers) == 1 and len(world.invoices) == 1
    assert world.opportunities["opp-002"]["stage"] == "Contract Signed"
    [rec] = await records(db, tenant)
    assert (rec.version_id, rec.integration_id, rec.stage, rec.ok) == (version.id, version.integration_id,
                                                                       "staging", True)
    assert rec.deployment_id == "dep-1"
    assert rec.event_id == "agr-002"
    assert rec.tool_calls == len(res.tool_calls) == 9
    assert rec.error is None and rec.duration_ms > 0
    # one connector per system, all closed, all for the right tenant
    assert sorted(c.system_id for _, c in created) == ["billing", "crm", "esign", "tax"]
    assert all(t == tenant.id and c.closed for t, c in created)
    assert executions(tenant.id, version.integration_id, "staging", "ok") == before + 1
    assert (REGISTRY.get_sample_value("pmaip_integration_execution_duration_seconds_count", {"stage": "staging"})
            or 0) >= 1


async def test_other_tenant_cannot_run_or_see_version(
    db: Database, tenant: Tenant, other_tenant: Tenant, settings: PlatformSettings, demo_integrations: Any,
    world_and_factory: Any,
) -> None:
    _, factory, created = world_and_factory
    version = await store_version(db, tenant, demo_integrations.loop1)
    runner = DefaultIntegrationRunner(db, build_sandbox(settings), settings, factory)
    with pytest.raises(NotFound):
        await runner.run(other_tenant.id, version.id, {"opportunity_id": "opp-001"}, stage="sandbox")
    assert created == []
    assert await records(db, other_tenant) == []
    assert await records(db, tenant) == []


async def test_disallowed_tool_fails_run_and_is_recorded(
    db: Database, tenant: Tenant, settings: PlatformSettings, demo_integrations: Any, world_and_factory: Any
) -> None:
    world, factory, _ = world_and_factory
    allowed = ["crm.get_opportunity", "crm.get_account", "tax.calculate"]  # billing.create_customer not approved
    version = await store_version(db, tenant, demo_integrations.loop1, allowed=allowed)
    runner = DefaultIntegrationRunner(db, build_sandbox(settings), settings, factory)
    res = await runner.run(tenant.id, version.id, {"opportunity_id": "opp-001"}, stage="sandbox")
    assert not res.ok
    assert "billing.create_customer" in (res.error or "")
    assert world.customers == {}
    [rec] = await records(db, tenant)
    assert rec.ok is False and "not in this version's allowed tools" in (rec.error or "")
    assert rec.tool_calls == 4  # 3 executed + 1 denied


async def test_invalid_stored_code_is_recorded_not_run(
    db: Database, tenant: Tenant, settings: PlatformSettings, demo_integrations: Any, world_and_factory: Any
) -> None:
    _, factory, created = world_and_factory
    bad = "import os\ndef run(event, tools):\n    return {'cwd': os.getcwd()}\n"
    version = await store_version(db, tenant, demo_integrations.loop1, code=bad)
    runner = DefaultIntegrationRunner(db, build_sandbox(settings), settings, factory)
    res = await runner.run(tenant.id, version.id, {"opportunity_id": "opp-001"}, stage="sandbox")
    assert not res.ok and "validation failed" in (res.error or "")
    assert created == []
    [rec] = await records(db, tenant)
    assert rec.ok is False


async def test_unapproved_version_never_runs_on_live_traffic(
    db: Database, tenant: Tenant, settings: PlatformSettings, demo_integrations: Any, world_and_factory: Any
) -> None:
    _, factory, _ = world_and_factory
    version = await store_version(db, tenant, demo_integrations.loop1, status=VersionStatus.IN_REVIEW)
    runner = DefaultIntegrationRunner(db, build_sandbox(settings), settings, factory)
    with pytest.raises(Conflict):
        await runner.run(tenant.id, version.id, {"opportunity_id": "opp-001"}, stage="production")
    res = await runner.run(tenant.id, version.id, {"opportunity_id": "opp-001"}, stage="sandbox")
    assert res.ok, res.error
    with pytest.raises(ValidationFailed):
        await runner.run(tenant.id, version.id, {}, stage="moon")


async def test_connector_setup_failure_is_recorded(
    db: Database, tenant: Tenant, settings: PlatformSettings, demo_integrations: Any
) -> None:
    async def factory(tenant_id: str, system_id: str) -> Connector:
        raise PlatformError(f"secret for {system_id} missing", code="secret_missing")

    version = await store_version(db, tenant, demo_integrations.loop1)
    runner = DefaultIntegrationRunner(db, build_sandbox(settings), settings, factory)
    res = await runner.run(tenant.id, version.id, {"opportunity_id": "opp-001"}, stage="sandbox")
    assert not res.ok and "secret_missing" in (res.error or "")
    [rec] = await records(db, tenant)
    assert rec.ok is False


def test_routing_and_event_ids() -> None:
    assert default_tool_routing("crm.get_account") == "crm"
    assert default_tool_routing("billing_create_customer") == "billing"
    with pytest.raises(ValidationFailed):
        default_tool_routing("nodots")
    assert event_id_of({"event_id": "e1", "opportunity_id": "opp"}) == "e1"
    assert event_id_of({"opportunity_id": "opp-1"}) == "opp-1"
    assert event_id_of({}) == ""
