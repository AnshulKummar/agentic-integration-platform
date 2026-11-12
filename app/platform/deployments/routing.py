"""Event dispatch: split live traffic between the canary and the active version.

The split is deterministic: ``sha256(integration_id:event_id)`` picks a bucket
in [0, 10000); buckets below ``canary_percent * 100`` go to the canary. The
same event id (e.g. a webhook retry) therefore always hits the same version.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.platform.core_models import Integration
from app.platform.db import Database, new_id
from app.platform.deployments.deps import DeploymentDeps
from app.platform.deployments.execution import execute_run
from app.platform.deployments.models import Deployment, DeploymentStatus, Stage
from app.platform.errors import Conflict
from app.platform.tenancy import TenantRepository

BUCKETS = 10_000


@dataclass(frozen=True)
class DispatchResult:
    event_id: str
    integration_id: str
    version_id: str
    stage: str
    deployment_id: str | None
    ok: bool
    error: str | None
    duration_ms: float
    output: dict[str, Any] | None


def canary_bucket(integration_id: str, event_id: str) -> int:
    digest = hashlib.sha256(f"{integration_id}:{event_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % BUCKETS


def routes_to_canary(integration_id: str, event_id: str, canary_percent: int) -> bool:
    return canary_bucket(integration_id, event_id) < max(0, min(100, canary_percent)) * (BUCKETS // 100)


def event_id_of(event: dict[str, Any]) -> str | None:
    value = event.get("id", event.get("event_id"))
    return None if value is None or value == "" else str(value)


async def dispatch_event(
    db: Database,
    deps: DeploymentDeps,
    *,
    tenant_id: str,
    integration_id: str,
    event: dict[str, Any],
) -> DispatchResult:
    event_id = event_id_of(event)
    if event_id is None:
        event_id = new_id()
        event = {**event, "id": event_id}
    async with db.session(tenant_id) as s:
        integration = await TenantRepository(s, tenant_id, Integration).require(integration_id)
        in_flight = (
            await s.execute(
                select(Deployment)
                .where(
                    Deployment.tenant_id == tenant_id,
                    Deployment.integration_id == integration_id,
                    Deployment.status == DeploymentStatus.RUNNING,
                )
                .order_by(Deployment.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    deployment_id: str | None = None
    if (
        in_flight is not None
        and in_flight.current_stage == Stage.CANARY
        and routes_to_canary(integration_id, event_id, in_flight.canary_percent)
    ):
        version_id, stage, deployment_id = in_flight.version_id, Stage.CANARY, in_flight.id
    elif integration.active_version_id is not None:
        version_id, stage = integration.active_version_id, Stage.PRODUCTION
        if in_flight is not None and in_flight.current_stage == Stage.WATCH and in_flight.version_id == version_id:
            deployment_id = in_flight.id
    else:
        raise Conflict(f"integration {integration_id} has no live version", code="no_live_version")

    result = await execute_run(
        db,
        deps,
        tenant_id=tenant_id,
        integration_id=integration_id,
        version_id=version_id,
        event=event,
        stage=stage,
        deployment_id=deployment_id,
    )
    return DispatchResult(
        event_id=event_id,
        integration_id=integration_id,
        version_id=version_id,
        stage=stage,
        deployment_id=deployment_id,
        ok=result.ok,
        error=result.error,
        duration_ms=result.duration_ms,
        output=result.output,
    )
