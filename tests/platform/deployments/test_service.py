"""Deployment state machine: gates, canary, promotion, watch, automatic and manual rollback."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import select

from app.platform.core_models import AuditEvent, ExecutionRecord, VersionStatus
from app.platform.db import Database
from app.platform.deployments import jobs
from app.platform.deployments.health import BASELINE_MARGIN, WindowStats, baseline_check, p95
from app.platform.deployments.models import (
    DeploymentStageRecord,
    DeploymentStatus,
    HealthCheckRecord,
    Job,
    Stage,
    StageStatus,
)
from app.platform.deployments.routing import dispatch_event
from app.platform.errors import Conflict, NotFound
from app.platform.observability.metrics import REGISTRY


def _metric(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def _stages(db: Database, tenant_id: str, deployment_id: str) -> dict[str, str]:
    async with db.session(tenant_id) as s:
        rows = (
            await s.execute(select(DeploymentStageRecord).where(DeploymentStageRecord.deployment_id == deployment_id))
        ).scalars()
        return {r.stage: r.status for r in rows}


async def _checks(db: Database, tenant_id: str, deployment_id: str) -> list[HealthCheckRecord]:
    async with db.session(tenant_id) as s:
        rows = await s.execute(select(HealthCheckRecord).where(HealthCheckRecord.deployment_id == deployment_id))
        return list(rows.scalars())


async def _promote(service, send_events, integ, tenant_id: str, version_id: str):  # type: ignore[no-untyped-def]
    dep = await service.start(tenant_id=tenant_id, actor_id="eng-1", integration_id=integ.id, version_id=version_id)
    assert await service.run_to_rest(tenant_id, dep.id) == 30.0  # waiting in canary
    await send_events(integ, 40, prefix=f"canary-{version_id}")
    assert await service.advance(tenant_id, dep.id) is None  # promoted; watch job takes over
    return dep


async def test_happy_path_to_production(
    db, tenant, service, send_events, make_integration, make_version, load, clock, runner, connectors
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ, make_active=True)
    v2 = await make_version(integ)
    before = _metric("pmaip_deployments_total", outcome="succeeded")

    dep = await service.start(tenant_id=tenant.id, actor_id="eng-1", integration_id=integ.id, version_id=v2.id)
    assert dep.status == DeploymentStatus.PENDING and dep.previous_version_id == v1.id
    _, _, statuses = await load(tenant.id, dep.id)
    assert statuses[v2.id] == VersionStatus.DEPLOYING

    # sandbox -> staging -> canary in one go, then waits for canary traffic
    assert await service.run_to_rest(tenant.id, dep.id) == 30.0
    dep, integ_row, _ = await load(tenant.id, dep.id)
    assert (dep.status, dep.current_stage) == (DeploymentStatus.RUNNING, Stage.CANARY)
    assert integ_row.active_version_id == v1.id
    assert {c.system_id for c in connectors.opened} == {"crm", "billing"}
    assert all(c.closed for c in connectors.opened)
    assert sum(1 for _, stage, _ in runner.calls if stage == "synthetic") == 6  # 3 sandbox + 3 staging

    # Not enough canary events yet: keep waiting.
    await send_events(integ, 4)
    assert await service.advance(tenant.id, dep.id) == 30.0

    counts = await send_events(integ, 40)
    assert counts["canary"] >= 10 and counts["production"] >= 10
    assert await service.advance(tenant.id, dep.id) is None
    dep, integ_row, statuses = await load(tenant.id, dep.id)
    assert dep.current_stage == Stage.WATCH and dep.status == DeploymentStatus.RUNNING
    assert integ_row.active_version_id == v2.id
    assert statuses == {v1.id: VersionStatus.SUPERSEDED, v2.id: VersionStatus.LIVE}

    # Watch window: healthy production traffic on v2, then the window closes.
    counts = await send_events(integ, 15, prefix="post")
    assert counts == {"production": 15}
    assert await service.watch_tick(tenant.id, dep.id) == 30.0
    clock.advance(301)
    assert await service.watch_tick(tenant.id, dep.id) is None
    dep, integ_row, _ = await load(tenant.id, dep.id)
    assert dep.status == DeploymentStatus.SUCCEEDED and dep.finished_at is not None
    assert integ_row.active_version_id == v2.id
    assert set((await _stages(db, tenant.id, dep.id)).values()) == {StageStatus.PASSED}
    names = {c.check for c in await _checks(db, tenant.id, dep.id)}
    assert {"synthetic_transaction", "connector_health:crm", "connector_health:billing", "error_rate",
            "p95_latency_ms", "canary_vs_baseline"} <= names
    assert _metric("pmaip_deployments_total", outcome="succeeded") == before + 1
    async with db.session(tenant.id) as s:
        actions = {a.action for a in (await s.execute(select(AuditEvent))).scalars()}
        executions = list((await s.execute(select(ExecutionRecord))).scalars())
    assert {"deployment.started", "deployment.promoted", "deployment.succeeded"} <= actions
    assert {e.stage for e in executions} == {"synthetic", "canary", "production"}


async def test_synthetic_failure_at_sandbox_fails_and_keeps_active(
    db, tenant, service, make_integration, make_version, load, runner
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ, make_active=True)
    v2 = await make_version(integ)
    runner.synthetic_fails.add(v2.id)
    before = _metric("pmaip_deployments_total", outcome="failed")

    dep = await service.start(tenant_id=tenant.id, actor_id="eng-1", integration_id=integ.id, version_id=v2.id)
    assert await service.run_to_rest(tenant.id, dep.id) is None
    dep, integ_row, statuses = await load(tenant.id, dep.id)
    assert dep.status == DeploymentStatus.FAILED
    assert "sandbox checks failed" in (dep.failure_reason or "")
    assert integ_row.active_version_id == v1.id
    assert statuses == {v1.id: VersionStatus.LIVE, v2.id: VersionStatus.FAILED}
    assert await _stages(db, tenant.id, dep.id) == {
        "sandbox": "failed", "staging": "skipped", "canary": "skipped", "production": "skipped", "watch": "skipped",
    }
    check = next(c for c in await _checks(db, tenant.id, dep.id) if c.check == "synthetic_transaction")
    assert (check.ok, check.observed, check.threshold) == (False, 3.0, 0.0)
    assert _metric("pmaip_deployments_total", outcome="failed") == before + 1


async def test_connector_unhealthy_at_staging_fails(
    db, tenant, service, make_integration, make_version, load, connectors
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ, make_active=True)
    v2 = await make_version(integ)
    connectors.unhealthy.add("billing")

    dep = await service.start(tenant_id=tenant.id, actor_id="eng-1", integration_id=integ.id, version_id=v2.id)
    assert await service.run_to_rest(tenant.id, dep.id) is None
    dep, integ_row, statuses = await load(tenant.id, dep.id)
    assert dep.status == DeploymentStatus.FAILED
    assert "connector_health:billing" in (dep.failure_reason or "")
    assert integ_row.active_version_id == v1.id and statuses[v2.id] == VersionStatus.FAILED
    stages = await _stages(db, tenant.id, dep.id)
    assert (stages["sandbox"], stages["staging"], stages["canary"]) == ("passed", "failed", "skipped")


async def test_canary_breach_rolls_back_automatically(
    db, tenant, service, worker, hooks, send_events, make_integration, make_version, load, runner, caplog
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ, make_active=True)
    v2 = await make_version(integ)
    runner.error_rate[v2.id] = 0.5
    rolled: list[str] = []

    async def _on_rolled_back(event) -> None:  # type: ignore[no-untyped-def]
        rolled.append(event.reason)

    hooks.on_rolled_back(_on_rolled_back)
    before = _metric("pmaip_rollbacks_total", reason="canary_breach")
    before_dep = _metric("pmaip_deployments_total", outcome="rolled_back")

    dep = await service.start(tenant_id=tenant.id, actor_id="eng-1", integration_id=integ.id, version_id=v2.id)
    await service.run_to_rest(tenant.id, dep.id)
    await send_events(integ, 40)
    with caplog.at_level(logging.ERROR, logger="app.platform.deployments.service"):
        assert await service.advance(tenant.id, dep.id) is None

    dep, integ_row, statuses = await load(tenant.id, dep.id)
    assert dep.status == DeploymentStatus.ROLLED_BACK
    assert (dep.failure_reason or "").startswith("canary breach: error_rate")
    assert integ_row.active_version_id == v1.id
    assert statuses == {v1.id: VersionStatus.LIVE, v2.id: VersionStatus.ROLLED_BACK}
    stages = await _stages(db, tenant.id, dep.id)
    assert (stages["canary"], stages["production"], stages["watch"]) == ("failed", "skipped", "skipped")
    failing = {c.check for c in await _checks(db, tenant.id, dep.id) if not c.ok}
    assert {"error_rate", "canary_vs_baseline"} <= failing
    assert _metric("pmaip_rollbacks_total", reason="canary_breach") == before + 1
    assert _metric("pmaip_deployments_total", outcome="rolled_back") == before_dep + 1
    alert = next(r for r in caplog.records if r.getMessage() == "deployment rolled back")
    assert alert.levelno == logging.ERROR and alert.__dict__["alert"] == "deployment_rollback"
    assert alert.__dict__["automatic"] is True
    async with db.session(tenant.id) as s:
        audit = (await s.execute(select(AuditEvent).where(AuditEvent.action == "deployment.rolled_back"))).scalar_one()
    assert audit.detail["automatic"] is True and audit.target_id == dep.id

    # Traffic now goes entirely to v1.
    assert await send_events(integ, 10, prefix="after") == {"production": 10}
    # The on_rolled_back hook is delivered by the worker.
    await worker.drain()
    assert rolled == [dep.failure_reason]


async def test_watch_window_breach_rolls_back_to_previous(
    db, tenant, service, send_events, make_integration, make_version, load, runner, clock
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ, make_active=True)
    v2 = await make_version(integ)
    before = _metric("pmaip_rollbacks_total", reason="watch_breach")
    dep = await _promote(service, send_events, integ, tenant.id, v2.id)
    _, integ_row, _ = await load(tenant.id, dep.id)
    assert integ_row.active_version_id == v2.id

    runner.latency_ms[v2.id] = 5000.0  # p95 SLO breach after go-live
    clock.advance(30)
    await send_events(integ, 12, prefix="post")
    assert await service.watch_tick(tenant.id, dep.id) is None

    dep, integ_row, statuses = await load(tenant.id, dep.id)
    assert dep.status == DeploymentStatus.ROLLED_BACK
    assert "post-deploy watch breach: p95_latency_ms" in (dep.failure_reason or "")
    assert integ_row.active_version_id == v1.id
    assert statuses == {v1.id: VersionStatus.LIVE, v2.id: VersionStatus.ROLLED_BACK}
    stages = await _stages(db, tenant.id, dep.id)
    assert (stages["production"], stages["watch"]) == ("passed", "failed")
    assert _metric("pmaip_rollbacks_total", reason="watch_breach") == before + 1


async def test_first_deploy_rollback_leaves_no_active_version(
    db, tenant, service, send_events, make_integration, make_version, load, runner, clock
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ)
    dep = await service.start(tenant_id=tenant.id, actor_id="eng-1", integration_id=integ.id, version_id=v1.id)
    assert dep.previous_version_id is None
    await service.run_to_rest(tenant.id, dep.id)
    counts = await send_events(integ, 40)
    # With no live version, non-canary events are rejected (the sender retries later).
    assert counts["canary"] >= 10 and counts["rejected"] >= 10
    check = None
    assert await service.advance(tenant.id, dep.id) is None
    for c in await _checks(db, tenant.id, dep.id):
        if c.check == "canary_vs_baseline":
            check = c
    assert check is not None and check.ok and check.detail["baseline"] is None

    runner.error_rate[v1.id] = 1.0
    await send_events(integ, 12, prefix="post")
    assert await service.watch_tick(tenant.id, dep.id) is None
    dep, integ_row, statuses = await load(tenant.id, dep.id)
    assert dep.status == DeploymentStatus.ROLLED_BACK
    assert integ_row.active_version_id is None
    assert statuses == {v1.id: VersionStatus.ROLLED_BACK}
    with pytest.raises(Conflict) as err:
        await dispatch_event(db, service.deps, tenant_id=tenant.id, integration_id=integ.id, event={"id": "x"})
    assert err.value.code == "no_live_version"


async def test_deploy_requires_dual_approval_and_approved_status(
    tenant, service, make_integration, make_version
) -> None:
    integ = await make_integration(tenant)
    only_pm = await make_version(integ, approvals=("pm",))
    with pytest.raises(Conflict) as err:
        await service.start(tenant_id=tenant.id, actor_id="e", integration_id=integ.id, version_id=only_pm.id)
    assert err.value.code == "approval_required" and "eng" in err.value.message

    in_review = await make_version(integ, status=VersionStatus.IN_REVIEW)
    with pytest.raises(Conflict) as err:
        await service.start(tenant_id=tenant.id, actor_id="e", integration_id=integ.id, version_id=in_review.id)
    assert err.value.code == "version_not_approved"


async def test_one_deployment_at_a_time(tenant, service, make_integration, make_version) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ)
    v2 = await make_version(integ)
    await service.start(tenant_id=tenant.id, actor_id="e", integration_id=integ.id, version_id=v1.id)
    with pytest.raises(Conflict) as err:
        await service.start(tenant_id=tenant.id, actor_id="e", integration_id=integ.id, version_id=v2.id)
    assert err.value.code == "deployment_in_progress"


async def test_manual_rollback_after_success_restores_previous(
    db, tenant, service, send_events, make_integration, make_version, load, clock
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ, make_active=True)
    v2 = await make_version(integ)
    dep = await _promote(service, send_events, integ, tenant.id, v2.id)
    clock.advance(301)
    assert await service.watch_tick(tenant.id, dep.id) is None
    before = _metric("pmaip_rollbacks_total", reason="manual")

    await service.rollback(tenant_id=tenant.id, deployment_id=dep.id, actor_id="eng-2", reason="customer report")
    dep, integ_row, statuses = await load(tenant.id, dep.id)
    assert dep.status == DeploymentStatus.ROLLED_BACK and dep.failure_reason == "manual rollback: customer report"
    assert integ_row.active_version_id == v1.id
    assert statuses == {v1.id: VersionStatus.LIVE, v2.id: VersionStatus.ROLLED_BACK}
    assert _metric("pmaip_rollbacks_total", reason="manual") == before + 1
    with pytest.raises(Conflict):
        await service.rollback(tenant_id=tenant.id, deployment_id=dep.id, actor_id="eng-2", reason="again")


async def test_manual_rollback_during_canary_wins_over_pending_promotion(
    tenant, service, send_events, make_integration, make_version, load
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ, make_active=True)
    v2 = await make_version(integ)
    dep = await service.start(tenant_id=tenant.id, actor_id="e", integration_id=integ.id, version_id=v2.id)
    await service.run_to_rest(tenant.id, dep.id)
    await send_events(integ, 40)
    await service.rollback(tenant_id=tenant.id, deployment_id=dep.id, actor_id="e", reason="")
    # The worker's next advance is a no-op: no promotion after a rollback.
    assert await service.advance(tenant.id, dep.id) is None
    dep, integ_row, statuses = await load(tenant.id, dep.id)
    assert dep.status == DeploymentStatus.ROLLED_BACK and integ_row.active_version_id == v1.id
    assert statuses[v2.id] == VersionStatus.ROLLED_BACK


async def test_runner_crash_is_retried_then_aborts(
    db, tenant, service, worker, make_integration, make_version, load, runner, clock
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ, make_active=True)
    v2 = await make_version(integ)
    runner.raises.add(v2.id)
    dep = await service.start(tenant_id=tenant.id, actor_id="e", integration_id=integ.id, version_id=v2.id)
    for _ in range(6):
        await worker.drain()
        clock.advance(400)
    async with db.session() as s:
        job = (await s.execute(select(Job).where(Job.kind == jobs.JOB_ADVANCE))).scalar_one()
    assert job.status == "failed" and job.attempts == job.max_attempts
    assert "UpstreamError" in (job.last_error or "")
    dep, integ_row, statuses = await load(tenant.id, dep.id)
    assert dep.status == DeploymentStatus.FAILED
    assert "health checks could not be evaluated" in (dep.failure_reason or "")
    assert integ_row.active_version_id == v1.id


async def test_other_tenant_cannot_touch_deployment(
    tenant, other_tenant, service, make_integration, make_version
) -> None:
    integ = await make_integration(tenant)
    v1 = await make_version(integ)
    dep = await service.start(tenant_id=tenant.id, actor_id="e", integration_id=integ.id, version_id=v1.id)
    with pytest.raises(NotFound):
        await service.advance(other_tenant.id, dep.id)
    with pytest.raises(NotFound):
        await service.rollback(tenant_id=other_tenant.id, deployment_id=dep.id, actor_id="x", reason="")
    with pytest.raises(NotFound):
        await service.start(tenant_id=other_tenant.id, actor_id="x", integration_id=integ.id, version_id=v1.id)


def test_p95_and_baseline_math() -> None:
    assert p95([]) == 0.0
    assert p95([float(i) for i in range(1, 101)]) == 95.0
    canary = WindowStats(events=100, errors=5, p95_ms=10)
    assert baseline_check(canary, WindowStats(events=100, errors=4, p95_ms=10)).ok
    worse = baseline_check(canary, WindowStats(events=100, errors=0, p95_ms=10))
    assert not worse.ok and worse.observed == pytest.approx(0.05) and worse.threshold == BASELINE_MARGIN
    assert baseline_check(canary, None).ok
