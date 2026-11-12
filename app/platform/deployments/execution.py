"""Run one version on one event through the IntegrationRunner and record it."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from app.platform.core_models import ExecutionRecord
from app.platform.db import Database
from app.platform.deployments.deps import DeploymentDeps
from app.platform.errors import UpstreamError
from app.platform.interfaces import SandboxResult
from app.platform.observability.metrics import EXECUTION_LATENCY, EXECUTIONS

log = logging.getLogger(__name__)

MAX_STORED_ERROR = 2000


async def execute_run(
    db: Database,
    deps: DeploymentDeps,
    *,
    tenant_id: str,
    integration_id: str,
    version_id: str,
    event: dict[str, Any],
    stage: str,
    deployment_id: str | None,
) -> SandboxResult:
    """Run and persist an ExecutionRecord (health checks read those rows).

    A ``SandboxResult(ok=False)`` is a normal, recorded failure of the version.
    An exception from the runner is an infrastructure failure: it is recorded
    as a failed execution, logged, and re-raised as ``UpstreamError``.
    """
    event_id = str(event.get("id") or "")
    started = deps.clock.now()
    t0 = time.perf_counter()
    try:
        result = await deps.runner.run(tenant_id, version_id, event, stage=stage)
    except Exception as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        log.exception(
            "integration runner raised",
            extra={"integration_id": integration_id, "version_id": version_id, "stage": stage, "event_id": event_id},
        )
        await _record(
            db, deps, tenant_id=tenant_id, integration_id=integration_id, version_id=version_id,
            deployment_id=deployment_id, stage=stage, event_id=event_id, ok=False,
            error=f"runner error: {type(exc).__name__}", duration_ms=duration_ms, tool_calls=0, started=started,
        )
        raise UpstreamError(f"integration runner failed ({type(exc).__name__})", code="runner_failed") from exc

    duration_ms = result.duration_ms if result.duration_ms > 0 else (time.perf_counter() - t0) * 1000
    await _record(
        db, deps, tenant_id=tenant_id, integration_id=integration_id, version_id=version_id,
        deployment_id=deployment_id, stage=stage, event_id=event_id, ok=result.ok, error=result.error,
        duration_ms=duration_ms, tool_calls=len(result.tool_calls), started=started,
    )
    return result


async def _record(
    db: Database,
    deps: DeploymentDeps,
    *,
    tenant_id: str,
    integration_id: str,
    version_id: str,
    deployment_id: str | None,
    stage: str,
    event_id: str,
    ok: bool,
    error: str | None,
    duration_ms: float,
    tool_calls: int,
    started: datetime,
) -> None:
    if not deps.record_executions:
        return
    async with db.session(tenant_id) as s:
        s.add(
            ExecutionRecord(
                tenant_id=tenant_id,
                integration_id=integration_id,
                version_id=version_id,
                deployment_id=deployment_id,
                stage=stage,
                event_id=event_id[:100],
                ok=ok,
                error=None if error is None else error[:MAX_STORED_ERROR],
                duration_ms=duration_ms,
                tool_calls=tool_calls,
                created_at=started,
            )
        )
        await s.commit()
    EXECUTIONS.labels(tenant=tenant_id, integration=integration_id, stage=stage, outcome="ok" if ok else "error").inc()
    EXECUTION_LATENCY.labels(stage=stage).observe(duration_ms / 1000)
