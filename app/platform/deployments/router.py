"""HTTP routes for the deployments area. OWNED BY: deployments/observability area (D).

    POST /v1/integrations/{id}/deployments   {version_id}      engineer   -> 202 + deployment
    GET  /v1/deployments[?integration_id=&status=]             any role
    GET  /v1/deployments/{id}                                  any role (stages + health checks)
    POST /v1/deployments/{id}/rollback       {reason}          engineer
    POST /v1/integrations/{id}/events        {event}           engineer or admin -> dispatch

Progression after start is done by the worker (``python -m app.platform.worker``).
Other tenants' ids return 404.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.config import PlatformSettings
from app.platform.db import Database
from app.platform.deployments.deps import DeploymentDeps, get_deployment_deps
from app.platform.deployments.models import Deployment, DeploymentStageRecord, HealthCheckRecord
from app.platform.deployments.routing import dispatch_event
from app.platform.deployments.service import DeploymentService
from app.platform.deps import get_database, get_settings, get_tenant_session
from app.platform.observability.context import bind_principal_to_log_context
from app.platform.principal import Principal, Role, current_principal, require_roles
from app.platform.tenancy import TenantRepository

router = APIRouter(tags=["deployments"], dependencies=[Depends(bind_principal_to_log_context)])

require_engineer = require_roles(Role.ENGINEER)

Engineer = Annotated[Principal, Depends(require_engineer)]
Caller = Annotated[Principal, Depends(current_principal)]
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
Db = Annotated[Database, Depends(get_database)]
Settings = Annotated[PlatformSettings, Depends(get_settings)]
Deps = Annotated[DeploymentDeps, Depends(get_deployment_deps)]


# --- schemas ------------------------------------------------------------------------


class StartDeploymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(min_length=1, max_length=32)


class RollbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Stable id (webhook delivery id). Determines canary routing; generated if absent.
    event_id: str | None = Field(default=None, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class DeploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    integration_id: str
    version_id: str
    previous_version_id: str | None
    status: str
    current_stage: str
    canary_percent: int
    failure_reason: str | None
    initiated_by: str | None
    started_at: datetime | None
    canary_started_at: datetime | None
    promoted_at: datetime | None
    watch_until: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    stage: str
    status: str
    detail: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None


class HealthCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    stage: str
    check: str
    ok: bool
    observed: float | None
    threshold: float | None
    detail: dict[str, Any]
    created_at: datetime


class DeploymentDetailOut(DeploymentOut):
    stages: list[StageOut]
    health_checks: list[HealthCheckOut]


class DispatchOut(BaseModel):
    event_id: str
    integration_id: str
    version_id: str
    stage: str
    deployment_id: str | None
    ok: bool
    error: str | None
    duration_ms: float
    output: dict[str, Any] | None


def _service(db: Db, settings: Settings, deps: Deps) -> DeploymentService:
    return DeploymentService(db, settings, deps)


Service = Annotated[DeploymentService, Depends(_service)]


async def _detail(session: AsyncSession, tenant_id: str, deployment_id: str) -> DeploymentDetailOut:
    dep = await TenantRepository(session, tenant_id, Deployment).require(deployment_id)
    stages = (
        await session.execute(
            select(DeploymentStageRecord)
            .where(DeploymentStageRecord.deployment_id == dep.id)
            .order_by(DeploymentStageRecord.position)
        )
    ).scalars()
    checks = (
        await session.execute(
            select(HealthCheckRecord)
            .where(HealthCheckRecord.deployment_id == dep.id)
            .order_by(HealthCheckRecord.created_at, HealthCheckRecord.id)
        )
    ).scalars()
    return DeploymentDetailOut(
        **DeploymentOut.model_validate(dep).model_dump(),
        stages=[StageOut.model_validate(s) for s in stages],
        health_checks=[HealthCheckOut.model_validate(c) for c in checks],
    )


# --- routes ---------------------------------------------------------------------------


@router.post("/v1/integrations/{integration_id}/deployments", status_code=202, response_model=DeploymentOut)
async def start_deployment(
    integration_id: str,
    body: StartDeploymentIn,
    principal: Engineer,
    service: Service,
) -> DeploymentOut:
    dep = await service.start(
        tenant_id=principal.tenant_id, actor_id=principal.user_id,
        integration_id=integration_id, version_id=body.version_id,
    )
    return DeploymentOut.model_validate(dep)


@router.get("/v1/deployments", response_model=list[DeploymentOut])
async def list_deployments(
    principal: Caller,
    session: TenantSession,
    integration_id: Annotated[str | None, Query(max_length=32)] = None,
    status: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DeploymentOut]:
    where = []
    if integration_id is not None:
        where.append(Deployment.integration_id == integration_id)
    if status is not None:
        where.append(Deployment.status == status)
    rows = await TenantRepository(session, principal.tenant_id, Deployment).list(
        *where, order_by=Deployment.created_at.desc(), limit=limit, offset=offset
    )
    return [DeploymentOut.model_validate(r) for r in rows]


@router.get("/v1/deployments/{deployment_id}", response_model=DeploymentDetailOut)
async def get_deployment(
    deployment_id: str,
    principal: Caller,
    session: TenantSession,
) -> DeploymentDetailOut:
    return await _detail(session, principal.tenant_id, deployment_id)


@router.post("/v1/deployments/{deployment_id}/rollback", response_model=DeploymentDetailOut)
async def rollback_deployment(
    deployment_id: str,
    body: RollbackIn,
    principal: Engineer,
    service: Service,
    session: TenantSession,
) -> DeploymentDetailOut:
    await service.rollback(
        tenant_id=principal.tenant_id, deployment_id=deployment_id, actor_id=principal.user_id, reason=body.reason
    )
    return await _detail(session, principal.tenant_id, deployment_id)


@router.post("/v1/integrations/{integration_id}/events", response_model=DispatchOut)
async def ingest_event(
    integration_id: str,
    body: EventIn,
    principal: Engineer,
    db: Db,
    deps: Deps,
) -> DispatchOut:
    event = dict(body.payload)
    if body.event_id is not None:
        event["id"] = body.event_id
    result = await dispatch_event(db, deps, tenant_id=principal.tenant_id, integration_id=integration_id, event=event)
    return DispatchOut(**asdict(result))
