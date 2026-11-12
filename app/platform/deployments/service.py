"""Deployment state machine.

    start -> sandbox (synthetic) -> staging (synthetic + connector health)
          -> canary (canary_percent of live events; SLO + baseline once canary_min_events seen)
          -> production (active_version_id flips) -> watch (post_deploy_watch_s, checks every
             health_check_interval_s) -> succeeded

- A failed check before production: deployment ``failed``, version ``failed``,
  active version untouched.
- A breach during canary or watch: AUTOMATIC ROLLBACK to ``previous_version_id``
  (None on a first deploy), version ``rolled_back``, deployment ``rolled_back``.

Each step is idempotent and short-transactioned: the deployment is read, slow
checks run outside any transaction, then the transition is applied under a row
lock and only if the deployment is still in the stage the checks were run for
(a concurrent manual rollback wins cleanly). Lifecycle hooks are delivered via
``deployment_hook`` jobs committed in the same transaction (outbox).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.audit import record_audit
from app.platform.config import PlatformSettings
from app.platform.core_models import Approval, Integration, IntegrationVersion, VersionStatus
from app.platform.db import Database
from app.platform.deployments import health, jobs
from app.platform.deployments.clock import as_utc
from app.platform.deployments.deps import DeploymentDeps
from app.platform.deployments.health import CheckResult
from app.platform.deployments.hooks import DeploymentEvent, HookKind
from app.platform.deployments.models import (
    Deployment,
    DeploymentStageRecord,
    DeploymentStatus,
    HealthCheckRecord,
    Stage,
    StageStatus,
)
from app.platform.errors import Conflict, NotFound
from app.platform.observability.metrics import DEPLOYMENTS, ROLLBACKS
from app.platform.tenancy import TenantRepository

log = logging.getLogger(__name__)

REQUIRED_APPROVALS = frozenset({"pm", "eng"})


class RollbackReason:
    """Bounded vocabulary for the ``pmaip_rollbacks_total{reason}`` label."""

    CANARY_BREACH = "canary_breach"
    WATCH_BREACH = "watch_breach"
    HEALTH_UNVERIFIABLE = "health_unverifiable"
    MANUAL = "manual"


@dataclass(frozen=True)
class _Snapshot:
    deployment: Deployment
    integration: Integration
    version: IntegrationVersion


class DeploymentService:
    def __init__(self, db: Database, settings: PlatformSettings, deps: DeploymentDeps) -> None:
        self.db = db
        self.settings = settings
        self.deps = deps

    def now(self) -> datetime:
        return self.deps.clock.now()

    # --- start -------------------------------------------------------------------

    async def start(self, *, tenant_id: str, actor_id: str | None, integration_id: str, version_id: str) -> Deployment:
        now = self.now()
        async with self.db.session(tenant_id) as s:
            integration = await _lock_integration(s, tenant_id, integration_id)
            version = await TenantRepository(s, tenant_id, IntegrationVersion).get(version_id)
            if version is None or version.integration_id != integration.id:
                raise NotFound(f"IntegrationVersion {version_id} not found")
            if version.status != VersionStatus.APPROVED:
                raise Conflict(
                    f"version status is '{version.status}'; only approved versions can be deployed",
                    code="version_not_approved",
                )
            approvals = await TenantRepository(s, tenant_id, Approval).list(Approval.version_id == version.id)
            missing = REQUIRED_APPROVALS - {a.role for a in approvals}
            if missing:
                raise Conflict(
                    f"deployment requires pm and eng approval; missing: {', '.join(sorted(missing))}",
                    code="approval_required",
                )
            in_flight = await TenantRepository(s, tenant_id, Deployment).list(
                Deployment.integration_id == integration.id, Deployment.status.in_(DeploymentStatus.ACTIVE)
            )
            if in_flight:
                raise Conflict(
                    f"deployment {in_flight[0].id} is already in progress for this integration",
                    code="deployment_in_progress",
                )
            deployment = TenantRepository(s, tenant_id, Deployment).add(
                Deployment(
                    integration_id=integration.id,
                    version_id=version.id,
                    previous_version_id=integration.active_version_id,
                    status=DeploymentStatus.PENDING,
                    current_stage=Stage.SANDBOX,
                    canary_percent=self.settings.canary_percent,
                    initiated_by=actor_id,
                    started_at=now,
                )
            )
            await s.flush()
            for position, stage in enumerate(Stage.ORDER):
                s.add(
                    DeploymentStageRecord(
                        tenant_id=tenant_id, deployment_id=deployment.id, stage=stage, position=position
                    )
                )
            version.status = VersionStatus.DEPLOYING
            record_audit(
                s,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="deployment.started",
                target_type="deployment",
                target_id=deployment.id,
                detail={"integration_id": integration.id, "version_id": version.id,
                        "previous_version_id": integration.active_version_id},
            )
            jobs.enqueue(
                s, kind=jobs.JOB_ADVANCE, payload={"deployment_id": deployment.id}, tenant_id=tenant_id, run_at=now
            )
            await s.commit()
        log.info(
            "deployment started",
            extra={"deployment_id": deployment.id, "integration_id": integration_id, "version_id": version_id},
        )
        return deployment

    # --- progression ---------------------------------------------------------------

    async def advance(self, tenant_id: str, deployment_id: str) -> float | None:
        """Run the current stage once. Returns seconds until the next step should
        run (0 = immediately), or None when the job is finished."""
        snap = await self._snapshot(tenant_id, deployment_id)
        dep = snap.deployment
        if dep.status in DeploymentStatus.TERMINAL:
            return None
        if dep.current_stage == Stage.SANDBOX:
            return await self._run_sandbox(snap)
        if dep.current_stage == Stage.STAGING:
            return await self._run_staging(snap)
        if dep.current_stage == Stage.CANARY:
            return await self._run_canary(snap)
        if dep.current_stage == Stage.WATCH:
            return await self.watch_tick(tenant_id, deployment_id)
        raise RuntimeError(f"deployment {dep.id} in unexpected stage {dep.current_stage!r}")

    async def run_to_rest(self, tenant_id: str, deployment_id: str) -> float | None:
        """Advance repeatedly while steps ask to run immediately (tests, CLI)."""
        while True:
            delay = await self.advance(tenant_id, deployment_id)
            if delay is None or delay > 0:
                return delay

    async def _run_sandbox(self, snap: _Snapshot) -> float | None:
        dep = snap.deployment
        await self._mark_running(dep.tenant_id, dep.id, Stage.SANDBOX)
        check = await health.synthetic_transaction(
            self.db, self.deps, integration=snap.integration, version=snap.version, deployment_id=dep.id
        )
        return await self._gate(dep, Stage.SANDBOX, [check], next_stage=Stage.STAGING)

    async def _run_staging(self, snap: _Snapshot) -> float | None:
        dep = snap.deployment
        await self._mark_running(dep.tenant_id, dep.id, Stage.STAGING)
        checks = [
            await health.synthetic_transaction(
                self.db, self.deps, integration=snap.integration, version=snap.version, deployment_id=dep.id
            ),
            *await health.connector_health(self.deps, self.settings, integration=snap.integration),
        ]
        return await self._gate(dep, Stage.STAGING, checks, next_stage=Stage.CANARY)

    async def _gate(self, dep: Deployment, stage: str, checks: list[CheckResult], *, next_stage: str) -> float | None:
        """Pre-production gate: pass -> next stage now; fail -> deployment failed."""
        now = self.now()
        failed = [c for c in checks if not c.ok]
        async with self.db.session(dep.tenant_id) as s:
            locked = await _lock_deployment(s, dep.tenant_id, dep.id)
            if not _still_in(locked, stage):
                return None
            _add_checks(s, locked, stage, checks, now)
            if failed:
                reason = f"{stage} checks failed: " + "; ".join(c.summary() for c in failed)
                await self._fail(s, locked, stage, reason, now)
                await s.commit()
                self._after_fail(locked, reason)
                return None
            _set_stage(await _stage(s, locked.id, stage), StageStatus.PASSED, now, {"checks": len(checks)})
            nxt = await _stage(s, locked.id, next_stage)
            _set_stage(nxt, StageStatus.RUNNING, now)
            locked.current_stage = next_stage
            if next_stage == Stage.CANARY:
                locked.canary_started_at = now
            await s.commit()
        log.info("deployment stage passed", extra={"deployment_id": dep.id, "stage": stage, "next_stage": next_stage})
        return 0.0

    async def _run_canary(self, snap: _Snapshot) -> float | None:
        dep = snap.deployment
        if dep.canary_started_at is None:
            raise RuntimeError(f"deployment {dep.id} is in canary without canary_started_at")
        since = as_utc(dep.canary_started_at)
        canary = await health.window_stats(
            self.db, tenant_id=dep.tenant_id, integration_id=dep.integration_id,
            version_id=dep.version_id, stage=Stage.CANARY, since=since,
        )
        if canary.events < self.settings.canary_min_events:
            return float(self.settings.health_check_interval_s)
        baseline = None
        if dep.previous_version_id is not None:
            baseline = await health.window_stats(
                self.db, tenant_id=dep.tenant_id, integration_id=dep.integration_id,
                version_id=dep.previous_version_id, stage=Stage.PRODUCTION, since=since,
            )
        checks = [*health.slo_checks(canary, self.settings), health.baseline_check(canary, baseline)]
        failed = [c for c in checks if not c.ok]
        if failed:
            reason = "canary breach: " + "; ".join(c.summary() for c in failed)
            await self._rollback(
                dep.tenant_id, dep.id, reason=reason, label=RollbackReason.CANARY_BREACH, actor_id=None,
                expect_stage=Stage.CANARY, checks=checks,
            )
            return None
        await self._promote(dep, checks)
        return None

    async def _promote(self, dep: Deployment, checks: list[CheckResult]) -> None:
        now = self.now()
        interval = self.settings.health_check_interval_s
        async with self.db.session(dep.tenant_id) as s:
            locked = await _lock_deployment(s, dep.tenant_id, dep.id)
            if not _still_in(locked, Stage.CANARY):
                return
            integration = await _lock_integration(s, dep.tenant_id, dep.integration_id)
            versions = TenantRepository(s, dep.tenant_id, IntegrationVersion)
            version = await versions.require(locked.version_id)
            _add_checks(s, locked, Stage.CANARY, checks, now)
            _set_stage(await _stage(s, locked.id, Stage.CANARY), StageStatus.PASSED, now)
            if integration.active_version_id is not None and integration.active_version_id != version.id:
                old = await versions.get(integration.active_version_id)
                if old is not None:
                    old.status = VersionStatus.SUPERSEDED
            integration.active_version_id = version.id
            version.status = VersionStatus.LIVE
            _set_stage(
                await _stage(s, locked.id, Stage.PRODUCTION), StageStatus.PASSED, now,
                {"active_version_id": version.id, "superseded_version_id": locked.previous_version_id},
            )
            _set_stage(await _stage(s, locked.id, Stage.WATCH), StageStatus.RUNNING, now)
            locked.current_stage = Stage.WATCH
            locked.promoted_at = now
            locked.watch_until = now + timedelta(seconds=self.settings.post_deploy_watch_s)
            record_audit(
                s, tenant_id=dep.tenant_id, actor_id=None, action="deployment.promoted",
                target_type="deployment", target_id=locked.id,
                detail={"integration_id": integration.id, "version_id": version.id,
                        "previous_version_id": locked.previous_version_id},
            )
            self._enqueue_hook(s, locked, "on_live", None, now)
            jobs.enqueue(
                s, kind=jobs.JOB_WATCH, payload={"deployment_id": locked.id}, tenant_id=dep.tenant_id,
                run_at=now + timedelta(seconds=interval),
            )
            await s.commit()
        log.info(
            "deployment promoted to production",
            extra={"deployment_id": dep.id, "integration_id": dep.integration_id, "version_id": dep.version_id},
        )

    async def watch_tick(self, tenant_id: str, deployment_id: str) -> float | None:
        """One post-deploy health evaluation. Returns seconds to the next tick or None when done."""
        dep = (await self._snapshot(tenant_id, deployment_id)).deployment
        if dep.status != DeploymentStatus.RUNNING or dep.current_stage != Stage.WATCH:
            return None
        if dep.promoted_at is None or dep.watch_until is None:
            raise RuntimeError(f"deployment {dep.id} is in watch without promotion timestamps")
        now = self.now()
        stats = await health.window_stats(
            self.db, tenant_id=tenant_id, integration_id=dep.integration_id, version_id=dep.version_id,
            stage=Stage.PRODUCTION, since=as_utc(dep.promoted_at),
        )
        enough = stats.events >= self.settings.canary_min_events
        checks = health.slo_checks(stats, self.settings) if enough else []
        failed = [c for c in checks if not c.ok]
        if failed:
            reason = "post-deploy watch breach: " + "; ".join(c.summary() for c in failed)
            await self._rollback(
                tenant_id, deployment_id, reason=reason, label=RollbackReason.WATCH_BREACH, actor_id=None,
                expect_stage=Stage.WATCH, checks=checks,
            )
            return None
        watch_until = as_utc(dep.watch_until)
        if now < watch_until:
            if checks:
                async with self.db.session(tenant_id) as s:
                    locked = await _lock_deployment(s, tenant_id, deployment_id)
                    if not _still_in(locked, Stage.WATCH):
                        return None
                    _add_checks(s, locked, Stage.WATCH, checks, now)
                    await s.commit()
            return max(1.0, min(float(self.settings.health_check_interval_s), (watch_until - now).total_seconds()))
        await self._succeed(dep, checks, stats.as_detail(), enough, now)
        return None

    async def _succeed(
        self, dep: Deployment, checks: list[CheckResult], stats: dict[str, object], enough: bool, now: datetime
    ) -> None:
        async with self.db.session(dep.tenant_id) as s:
            locked = await _lock_deployment(s, dep.tenant_id, dep.id)
            if not _still_in(locked, Stage.WATCH):
                return
            _add_checks(s, locked, Stage.WATCH, checks, now)
            detail: dict[str, object] = {"window": stats}
            if not enough:
                detail["note"] = (
                    f"fewer than {self.settings.canary_min_events} production events during the watch window; "
                    "SLOs not evaluated"
                )
            _set_stage(await _stage(s, locked.id, Stage.WATCH), StageStatus.PASSED, now, detail)
            locked.status = DeploymentStatus.SUCCEEDED
            locked.finished_at = now
            record_audit(
                s, tenant_id=dep.tenant_id, actor_id=None, action="deployment.succeeded",
                target_type="deployment", target_id=locked.id, detail={"version_id": locked.version_id},
            )
            await s.commit()
        DEPLOYMENTS.labels(outcome=DeploymentStatus.SUCCEEDED).inc()
        log.info("deployment succeeded", extra={"deployment_id": dep.id, "version_id": dep.version_id})

    # --- rollback / abort --------------------------------------------------------------

    async def rollback(self, *, tenant_id: str, deployment_id: str, actor_id: str | None, reason: str) -> Deployment:
        """Manual rollback (API). Allowed while in flight, or after success if the
        deployed version is still the active one."""
        result = await self._rollback(
            tenant_id, deployment_id, reason=f"manual rollback: {reason}" if reason else "manual rollback",
            label=RollbackReason.MANUAL, actor_id=actor_id, expect_stage=None, checks=[],
        )
        if result is None:  # pragma: no cover - expect_stage=None never goes stale
            raise Conflict("deployment changed concurrently; retry", code="deployment_changed")
        return result

    async def abort(self, tenant_id: str, deployment_id: str, reason: str) -> None:
        """Health could not be evaluated (job dead-lettered): fail before canary,
        roll back during canary/watch (fail safe)."""
        dep = (await self._snapshot(tenant_id, deployment_id)).deployment
        if dep.status in DeploymentStatus.TERMINAL:
            return
        if dep.current_stage in (Stage.CANARY, Stage.WATCH):
            await self._rollback(
                tenant_id, deployment_id, reason=reason, label=RollbackReason.HEALTH_UNVERIFIABLE, actor_id=None,
                expect_stage=dep.current_stage, checks=[],
            )
            return
        now = self.now()
        async with self.db.session(tenant_id) as s:
            locked = await _lock_deployment(s, tenant_id, deployment_id)
            if not _still_in(locked, dep.current_stage):
                return
            await self._fail(s, locked, locked.current_stage, reason, now)
            await s.commit()
        self._after_fail(locked, reason)

    async def _rollback(
        self,
        tenant_id: str,
        deployment_id: str,
        *,
        reason: str,
        label: str,
        actor_id: str | None,
        expect_stage: str | None,
        checks: list[CheckResult],
    ) -> Deployment | None:
        now = self.now()
        automatic = label != RollbackReason.MANUAL
        async with self.db.session(tenant_id) as s:
            dep = await _lock_deployment(s, tenant_id, deployment_id)
            if expect_stage is not None and not _still_in(dep, expect_stage):
                return None
            if dep.status in (DeploymentStatus.FAILED, DeploymentStatus.ROLLED_BACK):
                raise Conflict(f"deployment is already {dep.status}", code="deployment_finished")
            integration = await _lock_integration(s, tenant_id, dep.integration_id)
            if dep.status == DeploymentStatus.SUCCEEDED:
                if integration.active_version_id != dep.version_id:
                    raise Conflict(
                        "a different version is now active; start a new deployment instead",
                        code="not_active_version",
                    )
                newer = await TenantRepository(s, tenant_id, Deployment).list(
                    Deployment.integration_id == dep.integration_id,
                    Deployment.status.in_(DeploymentStatus.ACTIVE),
                    Deployment.id != dep.id,
                )
                if newer:
                    raise Conflict(
                        "another deployment is in progress for this integration", code="deployment_in_progress"
                    )
            versions = TenantRepository(s, tenant_id, IntegrationVersion)
            version = await versions.require(dep.version_id)
            stage_at_rollback = dep.current_stage
            _add_checks(s, dep, stage_at_rollback, checks, now)
            # Traffic back to the previous version (or none on a first deploy).
            integration.active_version_id = dep.previous_version_id
            if dep.previous_version_id is not None:
                previous = await versions.get(dep.previous_version_id)
                if previous is not None:
                    previous.status = VersionStatus.LIVE
            version.status = VersionStatus.ROLLED_BACK
            was_in_flight = dep.status in DeploymentStatus.ACTIVE
            if was_in_flight:
                for rec in await _stages(s, dep.id):
                    if rec.status == StageStatus.RUNNING:
                        _set_stage(rec, StageStatus.FAILED, now, {"reason": reason})
                    elif rec.status == StageStatus.PENDING:
                        _set_stage(rec, StageStatus.SKIPPED, now)
            dep.status = DeploymentStatus.ROLLED_BACK
            dep.failure_reason = reason
            dep.finished_at = now
            record_audit(
                s, tenant_id=tenant_id, actor_id=actor_id, action="deployment.rolled_back",
                target_type="deployment", target_id=dep.id,
                detail={"automatic": automatic, "reason": reason, "stage": stage_at_rollback,
                        "version_id": dep.version_id, "restored_version_id": dep.previous_version_id},
            )
            self._enqueue_hook(s, dep, "on_rolled_back", reason, now)
            await s.commit()
        ROLLBACKS.labels(reason=label).inc()
        if was_in_flight:
            DEPLOYMENTS.labels(outcome=DeploymentStatus.ROLLED_BACK).inc()
        # Alerting hook for log-based alerts (in addition to pmaip_rollbacks_total).
        log.error(
            "deployment rolled back",
            extra={
                "alert": "deployment_rollback",
                "automatic": automatic,
                "rollback_reason": label,
                "reason": reason,
                "deployment_id": dep.id,
                "integration_id": dep.integration_id,
                "version_id": dep.version_id,
                "restored_version_id": dep.previous_version_id,
                "stage": stage_at_rollback,
                "tenant_id": tenant_id,
            },
        )
        return dep

    async def _fail(self, s: AsyncSession, dep: Deployment, stage: str, reason: str, now: datetime) -> None:
        version = await TenantRepository(s, dep.tenant_id, IntegrationVersion).require(dep.version_id)
        version.status = VersionStatus.FAILED
        for rec in await _stages(s, dep.id):
            if rec.stage == stage:
                _set_stage(rec, StageStatus.FAILED, now, {**rec.detail, "reason": reason})
            elif rec.status in (StageStatus.PENDING, StageStatus.RUNNING):
                _set_stage(rec, StageStatus.SKIPPED, now)
        dep.status = DeploymentStatus.FAILED
        dep.failure_reason = reason
        dep.finished_at = now
        record_audit(
            s, tenant_id=dep.tenant_id, actor_id=None, action="deployment.failed",
            target_type="deployment", target_id=dep.id,
            detail={"stage": stage, "reason": reason, "version_id": dep.version_id},
        )

    def _after_fail(self, dep: Deployment, reason: str) -> None:
        DEPLOYMENTS.labels(outcome=DeploymentStatus.FAILED).inc()
        log.error(
            "deployment failed",
            extra={"alert": "deployment_failed", "deployment_id": dep.id, "integration_id": dep.integration_id,
                   "version_id": dep.version_id, "reason": reason, "tenant_id": dep.tenant_id},
        )

    def _enqueue_hook(
        self, s: AsyncSession, dep: Deployment, kind: HookKind, reason: str | None, now: datetime
    ) -> None:
        event = DeploymentEvent(
            kind=kind, tenant_id=dep.tenant_id, deployment_id=dep.id, integration_id=dep.integration_id,
            version_id=dep.version_id, previous_version_id=dep.previous_version_id, reason=reason,
        )
        jobs.enqueue(s, kind=jobs.JOB_HOOK, payload=dict(event.to_payload()), tenant_id=dep.tenant_id, run_at=now)

    # --- helpers -------------------------------------------------------------------------

    async def _snapshot(self, tenant_id: str, deployment_id: str) -> _Snapshot:
        async with self.db.session(tenant_id) as s:
            dep = await TenantRepository(s, tenant_id, Deployment).require(deployment_id)
            integration = await TenantRepository(s, tenant_id, Integration).require(dep.integration_id)
            version = await TenantRepository(s, tenant_id, IntegrationVersion).require(dep.version_id)
        return _Snapshot(dep, integration, version)

    async def _mark_running(self, tenant_id: str, deployment_id: str, stage: str) -> None:
        now = self.now()
        async with self.db.session(tenant_id) as s:
            dep = await _lock_deployment(s, tenant_id, deployment_id)
            if not _still_in(dep, stage):
                return
            rec = await _stage(s, dep.id, stage)
            if rec.status == StageStatus.PENDING:
                _set_stage(rec, StageStatus.RUNNING, now)
            dep.status = DeploymentStatus.RUNNING
            await s.commit()


def _still_in(dep: Deployment, stage: str) -> bool:
    """Is the deployment still active and in ``stage``? (Guards against concurrent changes.)"""
    if dep.status in DeploymentStatus.ACTIVE and dep.current_stage == stage:
        return True
    log.info(
        "deployment changed concurrently; skipping transition",
        extra={"deployment_id": dep.id, "expected_stage": stage, "stage": dep.current_stage, "status": dep.status},
    )
    return False


async def _lock_deployment(s: AsyncSession, tenant_id: str, deployment_id: str) -> Deployment:
    dep = (
        await s.execute(
            select(Deployment)
            .where(Deployment.id == deployment_id, Deployment.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if dep is None:
        raise NotFound(f"Deployment {deployment_id} not found")
    return dep


async def _lock_integration(s: AsyncSession, tenant_id: str, integration_id: str) -> Integration:
    integration = (
        await s.execute(
            select(Integration)
            .where(Integration.id == integration_id, Integration.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if integration is None:
        raise NotFound(f"Integration {integration_id} not found")
    return integration


async def _stages(s: AsyncSession, deployment_id: str) -> list[DeploymentStageRecord]:
    rows = await s.execute(
        select(DeploymentStageRecord)
        .where(DeploymentStageRecord.deployment_id == deployment_id)
        .order_by(DeploymentStageRecord.position)
    )
    return list(rows.scalars().all())


async def _stage(s: AsyncSession, deployment_id: str, stage: str) -> DeploymentStageRecord:
    rec = (
        await s.execute(
            select(DeploymentStageRecord).where(
                DeploymentStageRecord.deployment_id == deployment_id, DeploymentStageRecord.stage == stage
            )
        )
    ).scalar_one_or_none()
    if rec is None:
        raise RuntimeError(f"deployment {deployment_id} has no {stage} stage record")
    return rec


def _set_stage(
    rec: DeploymentStageRecord, status: str, now: datetime, detail: dict[str, object] | None = None
) -> None:
    rec.status = status
    if status == StageStatus.RUNNING and rec.started_at is None:
        rec.started_at = now
    if status in (StageStatus.PASSED, StageStatus.FAILED, StageStatus.SKIPPED):
        rec.finished_at = now
    if detail is not None:
        rec.detail = dict(detail)


def _add_checks(s: AsyncSession, dep: Deployment, stage: str, checks: list[CheckResult], now: datetime) -> None:
    for c in checks:
        s.add(
            HealthCheckRecord(
                tenant_id=dep.tenant_id,
                deployment_id=dep.id,
                stage=stage,
                check=c.name,
                ok=c.ok,
                observed=c.observed,
                threshold=c.threshold,
                detail=c.detail,
                created_at=now,
            )
        )
