"""HTTP routes for the integrations area (F): KG, roadmap, integrations, API releases.

Every route is tenant-scoped through ``get_tenant_session``; ids of other
tenants' rows answer 404. Reads are open to any authenticated role (viewer
included); writes need the roles noted per route (admin always passes).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.models import NodeType
from app.platform.deps import get_tenant_session
from app.platform.integrations import service
from app.platform.integrations.schemas import (
    ApprovalIn,
    BootstrapOut,
    DraftOut,
    EditsIn,
    IntegrationCreate,
    IntegrationOut,
    KGOut,
    ReleaseIn,
    ReleaseOut,
    ReleaseResultOut,
    RoadmapCreate,
    RoadmapOut,
    RoadmapPatch,
    VersionOut,
)
from app.platform.principal import Principal, Role, current_principal, require_roles

router = APIRouter(tags=["integrations"])

Session = Annotated[AsyncSession, Depends(get_tenant_session)]
Reader = Annotated[Principal, Depends(current_principal)]
Admin = Annotated[Principal, Depends(require_roles(Role.ADMIN))]
PM = Annotated[Principal, Depends(require_roles(Role.PM))]
Engineer = Annotated[Principal, Depends(require_roles(Role.ENGINEER))]
Builder = Annotated[Principal, Depends(require_roles(Role.PM, Role.ENGINEER))]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


# ---------------------------------------------------------------------- KG
@router.post("/v1/kg/bootstrap", response_model=BootstrapOut)
async def bootstrap_kg(session: Session, principal: Admin) -> BootstrapOut:
    result = await service.bootstrap_tenant_kg(session, principal.tenant_id, principal.user_id)
    await session.commit()
    return BootstrapOut(
        nodes_added=result.nodes_added, edges_added=result.edges_added,
        roadmap_items_added=result.roadmap_items_added,
        context_integrations_added=result.context_integrations_added,
        baseline_releases_added=result.baseline_releases_added, counts=result.counts,
    )


@router.get("/v1/kg", response_model=KGOut)
async def get_kg(session: Session, principal: Reader, type: NodeType | None = None) -> KGOut:  # noqa: A002
    return KGOut.model_validate(await service.kg_snapshot(session, principal.tenant_id, type))


# ----------------------------------------------------------------- roadmap
@router.get("/v1/roadmap", response_model=list[RoadmapOut])
async def list_roadmap(session: Session, principal: Reader) -> list[RoadmapOut]:
    return [RoadmapOut.of(r) for r in await service.list_roadmap_records(session, principal.tenant_id)]


@router.post("/v1/roadmap", response_model=RoadmapOut, status_code=status.HTTP_201_CREATED)
async def create_roadmap(body: RoadmapCreate, session: Session, principal: PM) -> RoadmapOut:
    rec = await service.create_roadmap_item(session, principal.tenant_id, body.model_dump(), principal.user_id)
    await session.commit()
    return RoadmapOut.of(rec)


@router.get("/v1/roadmap/{item_id}", response_model=RoadmapOut)
async def get_roadmap(item_id: str, session: Session, principal: Reader) -> RoadmapOut:
    return RoadmapOut.of(await service.get_roadmap_record(session, principal.tenant_id, item_id))


@router.patch("/v1/roadmap/{item_id}", response_model=RoadmapOut)
async def patch_roadmap(item_id: str, body: RoadmapPatch, session: Session, principal: PM) -> RoadmapOut:
    rec = await service.update_roadmap_item(
        session, principal.tenant_id, item_id, body.model_dump(exclude_unset=True), principal.user_id
    )
    await session.commit()
    return RoadmapOut.of(rec)


# ------------------------------------------------------------- integrations
@router.post("/v1/integrations", response_model=DraftOut, status_code=status.HTTP_201_CREATED)
async def create_integration(body: IntegrationCreate, session: Session, principal: Builder) -> DraftOut:
    tenant = await service.get_tenant(session, principal.tenant_id)
    bundle = await service.draft_from_intent(
        session, tenant, intent=body.intent, author_id=principal.user_id, source_system=body.source_system,
        target_system=body.target_system, trigger=body.trigger, name=body.name, roadmap_item_id=body.roadmap_item_id,
    )
    await session.commit()
    return DraftOut.of(bundle)


@router.get("/v1/integrations", response_model=list[IntegrationOut])
async def list_integrations(
    session: Session, principal: Reader, limit: Limit = 100, offset: Offset = 0
) -> list[IntegrationOut]:
    rows = await service.list_integrations(session, principal.tenant_id, limit=limit, offset=offset)
    return [IntegrationOut.of(i, latest) for i, latest in rows]


@router.get("/v1/integrations/{integration_id}", response_model=IntegrationOut)
async def get_integration(integration_id: str, session: Session, principal: Reader) -> IntegrationOut:
    integ = await service.get_integration(session, principal.tenant_id, integration_id)
    versions = await service.list_versions(session, principal.tenant_id, integration_id)
    return IntegrationOut.of(integ, versions[-1] if versions else None, versions)


@router.get("/v1/integrations/{integration_id}/versions/{version_id}", response_model=VersionOut)
async def get_version(integration_id: str, version_id: str, session: Session, principal: Reader) -> VersionOut:
    return VersionOut.of(await service.get_version_bundle(session, principal.tenant_id, integration_id, version_id))


@router.patch("/v1/integrations/{integration_id}/versions/{version_id}/mappings", response_model=VersionOut)
async def edit_mappings(
    integration_id: str, version_id: str, body: EditsIn, session: Session, principal: Engineer
) -> VersionOut:
    bundle = await service.edit_mappings(
        session, principal.tenant_id, integration_id, version_id,
        [service.EditRequest(target=e.target, source=e.source, transform=e.transform) for e in body.edits],
        principal,
    )
    await session.commit()
    return VersionOut.of(bundle)


@router.post("/v1/integrations/{integration_id}/versions/{version_id}/approvals", response_model=VersionOut)
async def approve_version(
    integration_id: str, version_id: str, body: ApprovalIn, session: Session, principal: Builder
) -> VersionOut:
    bundle = await service.approve(
        session, principal.tenant_id, integration_id, version_id, body.role, principal, body.comment
    )
    await session.commit()
    return VersionOut.of(bundle)


# ----------------------------------------------------------------- releases
@router.post("/v1/api-releases", response_model=ReleaseResultOut, status_code=status.HTTP_201_CREATED)
async def ingest_release(body: ReleaseIn, session: Session, principal: Engineer) -> ReleaseResultOut:
    tenant = await service.get_tenant(session, principal.tenant_id)
    outcome = await service.ingest_api_release(
        session, tenant, system_id=body.system_id, app=body.app, version=body.version, spec=body.spec,
        actor_id=principal.user_id,
    )
    await session.commit()
    return ReleaseResultOut.of(outcome)


@router.get("/v1/api-releases", response_model=list[ReleaseOut])
async def list_releases(
    session: Session, principal: Reader, system_id: Annotated[str | None, Query(alias="systemId")] = None
) -> list[ReleaseOut]:
    return [ReleaseOut.of(r) for r in await service.list_releases(session, principal.tenant_id, system_id)]


@router.get("/v1/api-releases/{release_id}", response_model=ReleaseOut)
async def get_release(release_id: str, session: Session, principal: Reader) -> ReleaseOut:
    return ReleaseOut.of(await service.get_release(session, principal.tenant_id, release_id))
