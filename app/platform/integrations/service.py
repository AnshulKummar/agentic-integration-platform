"""Integration lifecycle service: the PMAIP loops, persisted and multi-tenant.

Used by the router and by other areas (deployments calls ``learn_from_version``
when a version goes live). Every function takes a tenant-bound ``AsyncSession``
and a ``tenant_id``, stages its changes and audit rows, and **flushes but does
not commit**: the caller owns the transaction.

Loop 1 (intent-centric):  ``draft_from_intent`` -> ``edit_mappings`` -> ``approve`` (pm + eng)
                           -> [deployments area takes it live] -> ``learn_from_version``
Loop 2 (new API release):  ``ingest_api_release`` -> diff, MCP tools, capability registration,
                           alignment, roadmap matching -> agent-drafted integration (in review)

The domain logic is ``app.demo`` (drafter, learning, aligner, matcher, governance
risk checks, codegen); this module maps it onto the platform tables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.governance import REQUIRED_ROLES, assess_risks
from app.demo.kg_store import KGError, KnowledgeGraph
from app.demo.learning import LearningReport, LearningService, integration_confidence, pattern_identity
from app.demo.mapping_drafter import MappingDrafter
from app.demo.mcp_tools import tools_for_release
from app.demo.models import CamelModel, HumanEdit, LearnedIn, MappingRow, RoadmapItem
from app.demo.models import Integration as DomainIntegration
from app.demo.openapi_diff import OpenApiError, diff_specs
from app.demo.orchestrator import HUMAN_EDIT_CONFIDENCE
from app.demo.roadmap_matcher import RoadmapMatch, match_roadmap
from app.demo.schema_aligner import Alignment, apply_alignments, compute_alignments, register_capabilities
from app.platform.audit import record_audit
from app.platform.core_models import (
    ApiReleaseRecord,
    Approval,
    Integration,
    IntegrationVersion,
    RoadmapItemRecord,
    Tenant,
    VersionStatus,
)
from app.platform.db import new_id, utcnow
from app.platform.errors import Conflict, Forbidden, NotFound, PlatformError, ValidationFailed
from app.platform.integrations import planner
from app.platform.integrations.codegen_hook import render_code
from app.platform.integrations.kg_repo import (
    BootstrapResult,
    KGRepository,
    bootstrap_tenant_kg,
    link_roadmap_node,
    roadmap_node_index,
)
from app.platform.integrations.models import ContextIntegrationRecord, VersionPlanRecord
from app.platform.principal import Principal, Role

log = logging.getLogger(__name__)

__all__ = [
    "AGENT_DRAFT_MIN_FIT", "BootstrapResult", "EditRequest", "LearnOutcome", "ReleaseOutcome", "VersionBundle",
    "approve", "bootstrap_tenant_kg", "draft_from_intent", "edit_mappings", "get_version_bundle",
    "ingest_api_release", "learn_from_version", "list_integrations",
]

InitiatedBy = Literal["user_intent", "agent_from_roadmap"]
ApprovalRole = Literal["pm", "eng"]

#: Minimum roadmap fit for the agent to draft an integration on its own after a release.
AGENT_DRAFT_MIN_FIT = 0.8
#: Who may give each approval (admin may act as either, via ``Principal.has``).
APPROVER_ROLE: dict[str, Role] = {"pm": Role.PM, "eng": Role.ENGINEER}
REVIEWABLE = VersionStatus.IN_REVIEW

_DOMAIN_STATUS: dict[str, str] = {
    VersionStatus.DRAFT: "planned",
    VersionStatus.IN_REVIEW: "in_review",
    VersionStatus.APPROVED: "approved",
    VersionStatus.DEPLOYING: "deploying",
    VersionStatus.LIVE: "live",
    VersionStatus.ROLLED_BACK: "rolled_back",
    VersionStatus.SUPERSEDED: "rolled_back",
    VersionStatus.FAILED: "rolled_back",
}


class StoredDataError(PlatformError):
    """A stored row no longer validates against the domain model. Server-side fault."""

    status = 500
    code = "stored_data_invalid"


class VersionPlan(CamelModel):
    """What the domain logic needs to re-render, re-assess and learn from a version."""

    operations: list[str] = Field(default_factory=list)
    source_systems: list[str] = Field(default_factory=list)
    target_systems: list[str] = Field(default_factory=list)
    trigger_node: str | None = None
    source_objects: list[str] = Field(default_factory=list)
    trigger_object: str | None = None
    endpoints: list[str] = Field(default_factory=list)
    service_principal: str | None = None
    patterns_applied: list[str] = Field(default_factory=list)
    rules_applied: list[str] = Field(default_factory=list)
    migrations: list[str] = Field(default_factory=list)
    pattern_id: str | None = None
    pattern_label: str | None = None
    roadmap_node: str | None = None
    source_label: str = ""
    target_label: str = ""

    def dump(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


@dataclass
class VersionBundle:
    integration: Integration
    version: IntegrationVersion
    plan_record: VersionPlanRecord
    approvals: list[Approval] = field(default_factory=list)

    @property
    def plan(self) -> VersionPlan:
        return _load_plan(self.plan_record)


@dataclass(frozen=True)
class EditRequest:
    target: str
    source: str | None = None
    transform: str | None = None


@dataclass(frozen=True)
class LearnOutcome:
    learned: bool
    skipped_reason: str | None
    report: dict[str, Any]
    roadmap_shipped: str | None
    migrated: list[str]


@dataclass(frozen=True)
class DeprecatedFieldImpact:
    field: str
    replaced_by: str | None
    used_by: list[str]


@dataclass
class ReleaseOutcome:
    release: ApiReleaseRecord
    alignments: list[Alignment]
    matches: list[RoadmapMatch]
    deprecated: list[DeprecatedFieldImpact]
    drafted: VersionBundle | None = None
    agent_error: str | None = None


# =================================================================== helpers
async def get_tenant(session: AsyncSession, tenant_id: str) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFound("tenant not found")
    return tenant


def _load_plan(rec: VersionPlanRecord) -> VersionPlan:
    try:
        return VersionPlan.model_validate(rec.plan)
    except ValidationError as exc:
        raise StoredDataError(f"stored plan of version {rec.version_id} is invalid") from exc


def roadmap_to_domain(rec: RoadmapItemRecord) -> RoadmapItem:
    try:
        return RoadmapItem.model_validate(
            {
                "id": rec.id, "title": rec.title, "description": rec.description, "intent": rec.intent,
                "column": rec.column, "status": rec.status, "quarter": rec.quarter, "owner": rec.owner,
                "requires": list(rec.requires), "blocked_reason": rec.blocked_reason, "fit_score": rec.fit_score,
                "fit_rationale": rec.fit_rationale, "integration_id": rec.integration_id,
            }
        )
    except ValidationError as exc:
        raise StoredDataError(f"stored roadmap item {rec.id} is invalid") from exc


async def list_roadmap_records(session: AsyncSession, tenant_id: str) -> list[RoadmapItemRecord]:
    result = await session.execute(
        select(RoadmapItemRecord).where(RoadmapItemRecord.tenant_id == tenant_id).order_by(RoadmapItemRecord.created_at)
    )
    return list(result.scalars())


async def get_roadmap_record(session: AsyncSession, tenant_id: str, item_id: str) -> RoadmapItemRecord:
    result = await session.execute(
        select(RoadmapItemRecord).where(RoadmapItemRecord.tenant_id == tenant_id, RoadmapItemRecord.id == item_id)
    )
    rec = result.scalar_one_or_none()
    if rec is None:
        raise NotFound(f"roadmap item {item_id} not found")
    return rec


def _field_ids(kg: KnowledgeGraph, mappings: list[dict[str, Any]]) -> list[str]:
    ids: set[str] = set()
    for m in mappings:
        for lbl in (m.get("source"), m.get("target")):
            node = kg.find_by_label(str(lbl), "data_field") if lbl else None
            if node is not None:
                ids.add(node.id)
    return sorted(ids)


async def context_integrations(session: AsyncSession, tenant_id: str, kg: KnowledgeGraph) -> list[dict[str, Any]]:
    """The tenant's context store: external integrations + platform integrations with a live version.

    Shaped like ``seed.json`` ``contextStore.integrations`` (what the demo's risk
    checks and roadmap matcher read).
    """
    out: list[dict[str, Any]] = []
    external = await session.execute(
        select(ContextIntegrationRecord).where(ContextIntegrationRecord.tenant_id == tenant_id)
    )
    for rec in external.scalars():
        out.append(
            {"id": rec.external_id, "name": rec.name, "roadmapId": rec.roadmap_item_id, "pattern": rec.pattern_id,
             "status": rec.status, "uses": list(rec.uses), "sourceSystem": rec.source_system,
             "targetSystem": rec.target_system}
        )
    live = await session.execute(
        select(Integration, IntegrationVersion, VersionPlanRecord)
        .join(IntegrationVersion, IntegrationVersion.integration_id == Integration.id)
        .join(VersionPlanRecord, VersionPlanRecord.version_id == IntegrationVersion.id)
        .where(
            Integration.tenant_id == tenant_id,
            IntegrationVersion.tenant_id == tenant_id,
            IntegrationVersion.status == VersionStatus.LIVE,
        )
    )
    for integ, version, plan_rec in live.all():
        plan = _load_plan(plan_rec)
        out.append(
            {"id": integ.id, "name": integ.name, "roadmapId": integ.roadmap_item_id, "pattern": plan.pattern_id,
             "status": "live", "uses": _field_ids(kg, version.mappings) + plan.endpoints,
             "sourceSystem": integ.source_system, "targetSystem": integ.target_system}
        )
    return out


def to_domain(integ: Integration, version: IntegrationVersion, plan_rec: VersionPlanRecord) -> DomainIntegration:
    """Project the stored integration version onto the demo's ``Integration`` model."""
    plan = _load_plan(plan_rec)
    try:
        return DomainIntegration.model_validate(
            {
                "id": integ.id, "name": integ.name, "loop": 1 if integ.initiated_by == "user_intent" else 2,
                "source": plan.source_label or integ.source_system, "target": plan.target_label or integ.target_system,
                "trigger": integ.trigger, "status": _DOMAIN_STATUS.get(version.status, "planned"),
                "initiated_by": integ.initiated_by, "mappings": list(version.mappings), "code": version.code,
                "risks": list(plan_rec.risks), "human_edits": version.human_edits, "confidence": version.confidence,
                "pattern_reused": version.pattern_reused, "roadmap_id": plan.roadmap_node, "intent": integ.intent,
                "operations": plan.operations, "endpoints": plan.endpoints, "trigger_node": plan.trigger_node,
                "source_systems": plan.source_systems, "target_systems": plan.target_systems,
                "patterns_applied": plan.patterns_applied, "rules_applied": plan.rules_applied,
                "service_principal": plan.service_principal, "tools_used": list(version.allowed_tools),
                "migrations": plan.migrations, "pattern_id": plan.pattern_id, "pattern_label": plan.pattern_label,
                "edit_log": list(plan_rec.edit_log),
            }
        )
    except ValidationError as exc:
        raise StoredDataError(f"stored version {version.id} does not validate") from exc


def _refresh(domain: DomainIntegration, kg: KnowledgeGraph, ctx: list[dict[str, Any]]) -> None:
    """Recompute derived fields (demo ``_refresh`` + risk assessment)."""
    domain.confidence = integration_confidence(domain.mappings, domain.human_edits)
    domain.tools_used, domain.endpoints = planner.tools_and_endpoints(domain)
    domain.code = render_code(domain)
    domain.risks = assess_risks(domain, kg, planner.field_catalog(), ctx)


def _store(domain: DomainIntegration, version: IntegrationVersion, plan_rec: VersionPlanRecord) -> None:
    version.mappings = [m.to_json() for m in domain.mappings]
    version.code = domain.code
    version.allowed_tools = list(domain.tools_used)
    version.confidence = domain.confidence
    version.human_edits = domain.human_edits
    version.pattern_reused = domain.pattern_reused
    plan = _load_plan(plan_rec) if plan_rec.plan else VersionPlan()
    plan = plan.model_copy(
        update={
            "operations": domain.operations, "source_systems": domain.source_systems,
            "target_systems": domain.target_systems, "trigger_node": domain.trigger_node,
            "endpoints": domain.endpoints, "service_principal": domain.service_principal,
            "patterns_applied": domain.patterns_applied, "rules_applied": domain.rules_applied,
            "migrations": domain.migrations, "pattern_id": domain.pattern_id, "pattern_label": domain.pattern_label,
            "roadmap_node": domain.roadmap_id, "source_label": domain.source, "target_label": domain.target,
        }
    )
    plan_rec.plan = plan.dump()
    plan_rec.risks = [r.to_json() for r in domain.risks]
    plan_rec.edit_log = [e.to_json() for e in domain.edit_log]


def _deprecated_in_use(kg: KnowledgeGraph, ctx: list[dict[str, Any]]) -> list[str]:
    deprecated = {e.target for e in kg.edges("DEPRECATES")}
    return sorted({planner.label(kg, u).split(".")[-1] for c in ctx if c.get("status") == "live"
                   for u in c.get("uses", []) if u in deprecated})


# ================================================================ drafting
async def _draft(
    session: AsyncSession,
    tenant: Tenant,
    kg: KnowledgeGraph,
    *,
    intent: str,
    author_id: str | None,
    source_system: str | None,
    target_system: str | None,
    trigger: str | None,
    name: str | None,
    roadmap_item_id: str | None,
    initiated_by: InitiatedBy,
) -> VersionBundle:
    plan = planner.plan_intent(kg, intent, trigger=trigger, source_system=source_system, target_system=target_system)
    roadmap_records = await list_roadmap_records(session, tenant.id)
    if roadmap_item_id is not None:
        roadmap_rec: RoadmapItemRecord | None = next((r for r in roadmap_records if r.id == roadmap_item_id), None)
        if roadmap_rec is None:
            raise NotFound(f"roadmap item {roadmap_item_id} not found")
    else:
        matched = planner.match_roadmap_item([roadmap_to_domain(r) for r in roadmap_records], intent)
        roadmap_rec = next((r for r in roadmap_records if r.id == matched), None)
    rm_node = roadmap_node_index(kg).get(roadmap_rec.id) if roadmap_rec else None

    learning = LearningService(kg, enabled=tenant.learning_enabled)
    patterns = learning.find_applicable(set(plan.source_systems + plan.target_systems))
    drafter = MappingDrafter(kg, planner.field_catalog())
    rows: list[MappingRow] = planner.trigger_rows(kg, plan.trigger_node) + drafter.draft(
        planner.slots(plan.operations), plan.source_objects, plan.trigger_object, patterns, id_prefix="m"
    )
    ctx = await context_integrations(session, tenant.id, kg)
    used_patterns = sorted({r.pattern_id for r in rows if r.pattern_id})
    primary = planner.primary_target(plan)
    integ_id = new_id()
    domain = DomainIntegration.model_validate(
        {
            "id": integ_id, "name": name or planner.default_name(kg, plan),
            "loop": 1 if initiated_by == "user_intent" else 2,
            "source": planner.short_system(kg, plan.source_systems[0]),
            "target": planner.short_system(kg, primary), "trigger": planner.label(kg, plan.trigger_node),
            "status": "in_review", "initiated_by": initiated_by, "mappings": rows,
            "turns": 1 if initiated_by == "user_intent" else 0, "roadmap_id": rm_node, "intent": intent,
            "operations": plan.operations, "trigger_node": plan.trigger_node,
            "source_systems": plan.source_systems, "target_systems": plan.target_systems,
            "patterns_applied": used_patterns, "rules_applied": sorted({r.rule_id for r in rows if r.rule_id}),
            "migrations": _deprecated_in_use(kg, ctx),
            "service_principal": f"{(rm_node or 'integration').removeprefix('rm-')}-agent",
        }
    )
    if used_patterns:
        main = max(used_patterns, key=lambda p: sum(r.pattern_id == p for r in rows))
        domain.pattern_reused = planner.label(kg, main).removeprefix("Pattern: ")
    domain.pattern_id, domain.pattern_label = pattern_identity(domain)
    _refresh(domain, kg, ctx)

    integ = Integration(
        id=integ_id, tenant_id=tenant.id, name=domain.name, source_system=plan.source_systems[0],
        target_system=primary, trigger=domain.trigger, initiated_by=initiated_by, intent=intent,
        roadmap_item_id=roadmap_rec.id if roadmap_rec else None, author_id=author_id,
    )
    version = IntegrationVersion(
        id=new_id(), tenant_id=tenant.id, integration_id=integ_id, number=1, status=VersionStatus.IN_REVIEW,
        created_by=author_id,
    )
    plan_rec = VersionPlanRecord(
        tenant_id=tenant.id, version_id=version.id,
        plan=VersionPlan(source_objects=plan.source_objects, trigger_object=plan.trigger_object).dump(),
    )
    _store(domain, version, plan_rec)
    session.add(integ)
    await session.flush()  # parent row first: FK from versions
    session.add(version)
    await session.flush()
    session.add(plan_rec)
    if roadmap_rec is not None and roadmap_rec.status != "shipped":
        roadmap_rec.status = "in_review"
        roadmap_rec.integration_id = integ_id
    record_audit(
        session, tenant_id=tenant.id, actor_id=author_id,
        action="integration.drafted" if initiated_by == "user_intent" else "integration.agent_drafted",
        target_type="integration", target_id=integ_id,
        detail={"versionId": version.id, "mappings": len(rows), "reused": sum(r.origin == "kg_reused" for r in rows),
                "confidence": domain.confidence, "roadmapItemId": integ.roadmap_item_id,
                "learningEnabled": tenant.learning_enabled},
    )
    await session.flush()
    log.info("integration.drafted", extra={"tenant": tenant.id, "integration": integ_id, "by": initiated_by})
    return VersionBundle(integ, version, plan_rec, [])


async def draft_from_intent(
    session: AsyncSession,
    tenant: Tenant,
    *,
    intent: str,
    author_id: str | None,
    source_system: str | None = None,
    target_system: str | None = None,
    trigger: str | None = None,
    name: str | None = None,
    roadmap_item_id: str | None = None,
    initiated_by: InitiatedBy = "user_intent",
) -> VersionBundle:
    """Loop 1 entry: intent -> Integration + version 1 (in_review) with drafted mappings.

    Mappings come from ``MappingDrafter`` with learned patterns from
    ``LearningService`` (none when ``tenant.learning_enabled`` is false).
    """
    if not intent.strip():
        raise ValidationFailed("intent must not be empty", code="unparsed_intent")
    kg = await KGRepository(session, tenant.id).load()
    return await _draft(
        session, tenant, kg, intent=intent, author_id=author_id, source_system=source_system,
        target_system=target_system, trigger=trigger, name=name, roadmap_item_id=roadmap_item_id,
        initiated_by=initiated_by,
    )


# ================================================================== reads
async def list_integrations(
    session: AsyncSession, tenant_id: str, *, limit: int = 100, offset: int = 0
) -> list[tuple[Integration, IntegrationVersion | None]]:
    result = await session.execute(
        select(Integration).where(Integration.tenant_id == tenant_id)
        .order_by(Integration.created_at.desc(), Integration.id).limit(limit).offset(offset)
    )
    integrations = list(result.scalars())
    if not integrations:
        return []
    versions = await session.execute(
        select(IntegrationVersion).where(
            IntegrationVersion.tenant_id == tenant_id,
            IntegrationVersion.integration_id.in_([i.id for i in integrations]),
        )
    )
    latest: dict[str, IntegrationVersion] = {}
    for v in versions.scalars():
        cur = latest.get(v.integration_id)
        if cur is None or v.number > cur.number:
            latest[v.integration_id] = v
    return [(i, latest.get(i.id)) for i in integrations]


async def get_integration(session: AsyncSession, tenant_id: str, integration_id: str) -> Integration:
    result = await session.execute(
        select(Integration).where(Integration.tenant_id == tenant_id, Integration.id == integration_id)
    )
    integ = result.scalar_one_or_none()
    if integ is None:
        raise NotFound(f"integration {integration_id} not found")
    return integ


async def list_versions(session: AsyncSession, tenant_id: str, integration_id: str) -> list[IntegrationVersion]:
    result = await session.execute(
        select(IntegrationVersion)
        .where(IntegrationVersion.tenant_id == tenant_id, IntegrationVersion.integration_id == integration_id)
        .order_by(IntegrationVersion.number)
    )
    return list(result.scalars())


async def _approvals(session: AsyncSession, tenant_id: str, version_id: str) -> list[Approval]:
    result = await session.execute(
        select(Approval).where(Approval.tenant_id == tenant_id, Approval.version_id == version_id)
        .order_by(Approval.created_at)
    )
    return list(result.scalars())


async def _plan_record(session: AsyncSession, tenant_id: str, version_id: str) -> VersionPlanRecord:
    result = await session.execute(
        select(VersionPlanRecord).where(VersionPlanRecord.tenant_id == tenant_id,
                                        VersionPlanRecord.version_id == version_id)
    )
    rec = result.scalar_one_or_none()
    if rec is None:
        raise StoredDataError(f"version {version_id} has no stored plan")
    return rec


async def get_version_bundle(
    session: AsyncSession, tenant_id: str, integration_id: str | None, version_id: str
) -> VersionBundle:
    """Version + integration + plan + approvals. ``integration_id=None`` looks the version up alone."""
    stmt = select(IntegrationVersion).where(IntegrationVersion.tenant_id == tenant_id,
                                            IntegrationVersion.id == version_id)
    if integration_id is not None:
        stmt = stmt.where(IntegrationVersion.integration_id == integration_id)
    version = (await session.execute(stmt)).scalar_one_or_none()
    if version is None:
        raise NotFound(f"version {version_id} not found")
    integ = await get_integration(session, tenant_id, version.integration_id)
    plan_rec = await _plan_record(session, tenant_id, version.id)
    return VersionBundle(integ, version, plan_rec, await _approvals(session, tenant_id, version.id))


# ================================================================== review
async def edit_mappings(
    session: AsyncSession,
    tenant_id: str,
    integration_id: str,
    version_id: str,
    edits: list[EditRequest],
    editor: Principal,
) -> VersionBundle:
    """Record human corrections. Allowed only while in review; clears existing approvals."""
    if not edits:
        raise ValidationFailed("at least one edit is required", code="no_edits")
    bundle = await get_version_bundle(session, tenant_id, integration_id, version_id)
    if bundle.version.status != REVIEWABLE:
        raise Conflict(f"version is {bundle.version.status!r}; only versions in review can be edited",
                       code="not_editable")
    domain = to_domain(bundle.integration, bundle.version, bundle.plan_record)
    for edit in edits:
        if not (edit.source or edit.transform):
            raise ValidationFailed(f"edit for {edit.target!r} changes nothing (give source and/or transform)",
                                   code="empty_edit")
        row = next((r for r in domain.mappings if r.target == edit.target), None)
        if row is None:
            raise ValidationFailed(f"no mapping targets {edit.target!r}", code="unknown_mapping")
        if edit.source:
            row.source = edit.source
        if edit.transform:
            row.transform = edit.transform
        row.origin, row.confidence, row.flag = "human_edit", HUMAN_EDIT_CONFIDENCE, None
        domain.edit_log.append(HumanEdit(target=edit.target, source=edit.source, transform=edit.transform,
                                         by=editor.email or editor.user_id))
        domain.human_edits += 1
    kg = await KGRepository(session, tenant_id).load()
    _refresh(domain, kg, await context_integrations(session, tenant_id, kg))
    _store(domain, bundle.version, bundle.plan_record)
    cleared = [a.role for a in bundle.approvals]
    for approval in bundle.approvals:
        await session.delete(approval)
    record_audit(
        session, tenant_id=tenant_id, actor_id=editor.user_id, action="version.mappings_edited",
        target_type="integration_version", target_id=version_id,
        detail={"targets": [e.target for e in edits], "humanEdits": domain.human_edits,
                "approvalsCleared": cleared},
    )
    await session.flush()
    bundle.approvals = []
    return bundle


async def approve(
    session: AsyncSession,
    tenant_id: str,
    integration_id: str,
    version_id: str,
    role: str,
    principal: Principal,
    comment: str = "",
) -> VersionBundle:
    """Dual approval with separation of duties.

    * ``pm`` needs Role.PM, ``eng`` needs Role.ENGINEER (admin may act as either);
    * the version's author cannot approve it; one user cannot give both approvals;
    * both approvals -> status ``approved``.
    """
    if role not in REQUIRED_ROLES:
        raise ValidationFailed(f"role {role!r} cannot approve; expected one of {REQUIRED_ROLES}", code="unknown_role")
    if not principal.has(APPROVER_ROLE[role]):
        raise Forbidden(f"{role} approval requires role {APPROVER_ROLE[role].value}")
    bundle = await get_version_bundle(session, tenant_id, integration_id, version_id)
    if bundle.version.status != REVIEWABLE:
        raise Conflict(f"version is {bundle.version.status!r}; only versions in review can be approved",
                       code="not_in_review")
    if bundle.version.created_by is not None and bundle.version.created_by == principal.user_id:
        raise Forbidden("the author of a version cannot approve it", code="separation_of_duties")
    if any(a.role == role for a in bundle.approvals):
        raise Conflict(f"{role} approval already given", code="already_approved")
    if any(a.approver_id == principal.user_id for a in bundle.approvals):
        raise Forbidden("the same user cannot give both approvals", code="separation_of_duties")
    approval = Approval(tenant_id=tenant_id, version_id=version_id, role=role, approver_id=principal.user_id,
                        comment=comment)
    session.add(approval)
    bundle.approvals.append(approval)
    record_audit(
        session, tenant_id=tenant_id, actor_id=principal.user_id, action="version.approved",
        target_type="integration_version", target_id=version_id, detail={"role": role, "comment": comment},
    )
    if {a.role for a in bundle.approvals} >= set(REQUIRED_ROLES):
        bundle.version.status = VersionStatus.APPROVED
        record_audit(
            session, tenant_id=tenant_id, actor_id=principal.user_id, action="version.status_changed",
            target_type="integration_version", target_id=version_id,
            detail={"from": VersionStatus.IN_REVIEW, "to": VersionStatus.APPROVED},
        )
    await session.flush()
    return bundle


# ================================================================= learning
async def learn_from_version(session: AsyncSession, tenant_id: str, version_id: str) -> LearnOutcome:
    """Go-live hook (called by the deployments area once a version is ``live``).

    * marks the linked roadmap item shipped;
    * applies bundled deprecation migrations to the context store;
    * runs ``LearningService.learn`` on the tenant KG and persists it
      (no KG write-back when ``Tenant.learning_enabled`` is false).

    Idempotent per version (``learned_at``). Flushes; the caller commits.
    """
    bundle = await get_version_bundle(session, tenant_id, None, version_id)
    if bundle.version.status != VersionStatus.LIVE:
        raise Conflict(f"version is {bundle.version.status!r}; only live versions are learned from", code="not_live")
    if bundle.plan_record.learned_at is not None:
        return LearnOutcome(False, "already_learned", dict(bundle.plan_record.learning_report), None, [])
    tenant = await get_tenant(session, tenant_id)
    repo = KGRepository(session, tenant_id)
    kg = await repo.load()
    domain = to_domain(bundle.integration, bundle.version, bundle.plan_record)
    learned_in: LearnedIn = "loop1" if bundle.integration.initiated_by == "user_intent" else "loop2"
    learning = LearningService(kg, enabled=tenant.learning_enabled)

    shipped: str | None = None
    if bundle.integration.roadmap_item_id:
        item = await get_roadmap_record(session, tenant_id, bundle.integration.roadmap_item_id)
        item.status, item.column = "shipped", "shipped"
        shipped = item.id

    migrated: list[str] = []
    if domain.migrations:
        deprecated = {e.target: e.source for e in kg.edges("DEPRECATES")}
        externals = await session.execute(
            select(ContextIntegrationRecord).where(ContextIntegrationRecord.tenant_id == tenant_id)
        )
        for ci in externals.scalars():
            for used in [u for u in ci.uses if u in deprecated]:
                ci.uses = [deprecated[used] if u == used else u for u in ci.uses]
                migrated.append(ci.name)
                if ci.pattern_id and kg.has_node(ci.pattern_id):
                    learning.apply_migration(ci.pattern_id, used, deprecated[used], learned_in)

    try:
        report: LearningReport = learning.learn(domain, learned_in)
    except (ValueError, KGError) as exc:
        raise Conflict(f"cannot learn from version {version_id}: {exc}", code="learning_failed") from exc
    await repo.save(kg)
    bundle.plan_record.learned_at = utcnow()
    bundle.plan_record.learning_report = report.to_json()
    record_audit(
        session, tenant_id=tenant_id, actor_id=None,
        action="kg.learning_skipped" if report.skipped else "kg.learned",
        target_type="integration_version", target_id=version_id,
        detail={"summary": report.summary(), "patternId": report.pattern_id, "roadmapShipped": shipped,
                "migrated": migrated},
    )
    await session.flush()
    log.info("kg.learned", extra={"tenant": tenant_id, "version": version_id, "summary": report.summary()})
    return LearnOutcome(not report.skipped, "learning_disabled" if report.skipped else None, report.to_json(),
                        shipped, migrated)


# ================================================================ releases
def _empty_spec(title: str) -> dict[str, Any]:
    return {"openapi": "3.0.0", "info": {"title": title, "version": "0.0.0"}, "paths": {}}


async def _inflight_users(session: AsyncSession, tenant_id: str, label: str) -> list[str]:
    """Names of in-review/approved platform integrations whose mappings mention ``label``."""
    result = await session.execute(
        select(Integration.name, IntegrationVersion.mappings)
        .join(IntegrationVersion, IntegrationVersion.integration_id == Integration.id)
        .where(Integration.tenant_id == tenant_id,
               IntegrationVersion.status.in_([VersionStatus.IN_REVIEW, VersionStatus.APPROVED]))
    )
    return sorted({name for name, mappings in result.all()
                   if any(label in (m.get("source"), m.get("target")) for m in mappings)})


async def list_releases(session: AsyncSession, tenant_id: str, system_id: str | None = None) -> list[ApiReleaseRecord]:
    stmt = select(ApiReleaseRecord).where(ApiReleaseRecord.tenant_id == tenant_id)
    if system_id:
        stmt = stmt.where(ApiReleaseRecord.system_id == system_id)
    result = await session.execute(stmt.order_by(ApiReleaseRecord.created_at.desc(), ApiReleaseRecord.id))
    return list(result.scalars())


async def get_release(session: AsyncSession, tenant_id: str, release_id: str) -> ApiReleaseRecord:
    result = await session.execute(
        select(ApiReleaseRecord).where(ApiReleaseRecord.tenant_id == tenant_id, ApiReleaseRecord.id == release_id)
    )
    rec = result.scalar_one_or_none()
    if rec is None:
        raise NotFound(f"api release {release_id} not found")
    return rec


async def ingest_api_release(
    session: AsyncSession,
    tenant: Tenant,
    *,
    system_id: str,
    app: str | None,
    version: str | None,
    spec: dict[str, Any],
    actor_id: str | None,
) -> ReleaseOutcome:
    """Loop 2 entry: a system shipped a new API version.

    Diff vs the previous stored release of that system (an empty spec if none),
    generate MCP tools, register capabilities + align them into the KG, re-score
    the roadmap (fit scores, unblocked items), flag deprecated fields and who uses
    them, and let the agent draft an integration for the best unblocked roadmap
    item with fit >= ``AGENT_DRAFT_MIN_FIT``.
    """
    repo = KGRepository(session, tenant.id)
    kg = await repo.load()
    system = planner.require_system(kg, system_id)
    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    spec_version = str((info or {}).get("version", "")) or None
    version = version or spec_version
    if not version:
        raise ValidationFailed("release version missing (give version or spec.info.version)", code="invalid_spec")
    if spec_version and spec_version != version:
        raise ValidationFailed(f"version {version!r} does not match spec.info.version {spec_version!r}",
                               code="version_mismatch")
    previous = await list_releases(session, tenant.id, system_id)
    if any(r.version == version for r in previous):
        raise Conflict(f"{system_id} release {version} was already ingested", code="release_exists")
    prev = previous[0] if previous else None
    app_label = app or system.label
    prefix = system_id.removeprefix("sys-")

    try:
        diff = diff_specs(prev.spec if prev else _empty_spec(app_label), spec)
        tools = tools_for_release(spec, diff, prefix)
        reg = register_capabilities(kg, spec, diff, system_id, "loop2")
        alignments = compute_alignments(kg, reg, diff)
        new_edges = set(apply_alignments(kg, alignments, "loop2"))
    except (OpenApiError, KGError, KeyError, TypeError, AttributeError) as exc:
        raise ValidationFailed(f"cannot analyse the OpenAPI document: {exc}", code="invalid_spec") from exc

    # Roadmap matching.
    records = {r.id: r for r in await list_roadmap_records(session, tenant.id)}
    ctx = await context_integrations(session, tenant.id, kg)
    capability_text = " ".join(e.summary for e in diff.added_endpoints) + " " + " ".join(
        w.name + " " + w.summary for w in diff.added_webhooks)
    matches = match_roadmap(kg, [roadmap_to_domain(r) for r in records.values()], reg.all_nodes, new_edges,
                            app_label, capability_text, ctx)
    impact: list[str] = []
    for m in sorted((m for m in matches if not m.impacted), key=lambda m: -(m.fit_score or 0)):
        rec = records[m.item_id]
        rec.fit_score, rec.fit_rationale = m.fit_score, m.rationale
        if m.unblocked:
            rec.status, rec.blocked_reason = "in_progress", None
            impact.append(f"Unblocks roadmap: {rec.title} (fit {m.fit_score:.2f})")
        elif m.partial:
            impact.append(f"Partially enables: {rec.title} (fit {m.fit_score:.2f})")
    for m in matches:
        if m.impacted:
            records[m.item_id].fit_rationale = m.rationale
            impact.append(f'Breaking: "{m.title}" uses a deprecated field')

    # Deprecated fields: what replaces them and who still uses them.
    deprecated: list[DeprecatedFieldImpact] = []
    replaced_by = {e.target: e.source for e in kg.edges("DEPRECATES")}
    for dep in diff.deprecated_fields:
        lbl = f"{dep.schema_name}.{dep.field}"
        node = kg.find_by_label(lbl, "data_field")
        users = await _inflight_users(session, tenant.id, lbl)
        replacement: str | None = dep.replaced_by
        if node is not None:
            users = sorted(set(users) | {c["name"] for c in ctx if c.get("status") == "live"
                                         and node.id in c.get("uses", [])})
            if node.id in replaced_by:
                replacement = planner.label(kg, replaced_by[node.id])
        deprecated.append(DeprecatedFieldImpact(field=lbl, replaced_by=replacement, used_by=users))
        impact.append(f"Deprecated: {lbl}" + (f" → {replacement}" if replacement else "")
                      + (f" (used by {', '.join(users)})" if users else ""))

    await repo.save(kg)
    release = ApiReleaseRecord(
        tenant_id=tenant.id, system_id=system_id, app=app_label, version=version,
        previous_version=prev.version if prev else None, status="analyzed", spec=spec,
        changes=[c.to_json() for c in diff.to_api_changes()], mcp_tools=[t.name for t in tools], impact=impact,
    )
    session.add(release)
    await session.flush()
    record_audit(
        session, tenant_id=tenant.id, actor_id=actor_id, action="api_release.ingested",
        target_type="api_release", target_id=release.id,
        detail={"systemId": system_id, "version": version, "previousVersion": release.previous_version,
                "summary": diff.summary(), "nodesAdded": len(reg.nodes_added), "edgesAdded": len(new_edges)},
    )
    outcome = ReleaseOutcome(release=release, alignments=alignments, matches=matches, deprecated=deprecated)

    # The agent drafts the best unblocked roadmap item on its own.
    candidates = sorted((m for m in matches if m.unblocked and (m.fit_score or 0) >= AGENT_DRAFT_MIN_FIT
                         and records[m.item_id].integration_id is None), key=lambda m: -(m.fit_score or 0))
    if candidates:
        item = records[candidates[0].item_id]
        try:
            outcome.drafted = await _draft(
                session, tenant, kg, intent=item.intent, author_id=None, source_system=None, target_system=None,
                trigger=None, name=f"{item.title} (agent draft)", roadmap_item_id=item.id,
                initiated_by="agent_from_roadmap",
            )
        except (ValidationFailed, Conflict) as exc:
            # Reported to the caller and in the release impact; the release itself is still recorded.
            log.warning("integration.agent_draft_failed", extra={"tenant": tenant.id, "item": item.id,
                                                                 "error": exc.message})
            outcome.agent_error = exc.message
            release.impact = [*impact, f"Agent could not draft {item.title}: {exc.message}"]
    await session.flush()
    return outcome


# ================================================================= roadmap
async def create_roadmap_item(
    session: AsyncSession, tenant_id: str, values: dict[str, Any], actor_id: str
) -> RoadmapItemRecord:
    repo = KGRepository(session, tenant_id)
    kg = await repo.load()
    _require_nodes(kg, values.get("requires", []))
    rec = RoadmapItemRecord(id=new_id(), tenant_id=tenant_id, **values)
    session.add(rec)
    link_roadmap_node(kg, rec)
    await repo.save(kg)
    record_audit(session, tenant_id=tenant_id, actor_id=actor_id, action="roadmap.created",
                 target_type="roadmap_item", target_id=rec.id, detail={"title": rec.title})
    await session.flush()
    return rec


async def update_roadmap_item(
    session: AsyncSession, tenant_id: str, item_id: str, changes: dict[str, Any], actor_id: str
) -> RoadmapItemRecord:
    """Partial update. New ``requires`` get REQUIRES edges (the KG never deletes old ones;
    matching reads ``requires`` from the record)."""
    rec = await get_roadmap_record(session, tenant_id, item_id)
    repo = KGRepository(session, tenant_id)
    kg = await repo.load()
    if "requires" in changes:
        _require_nodes(kg, changes["requires"])
    for key, value in changes.items():
        setattr(rec, key, value)
    nid = link_roadmap_node(kg, rec)
    if "title" in changes:
        kg.update_node(nid, label=f"Roadmap: {rec.title}")
    await repo.save(kg)
    record_audit(session, tenant_id=tenant_id, actor_id=actor_id, action="roadmap.updated",
                 target_type="roadmap_item", target_id=rec.id, detail={"fields": sorted(changes)})
    await session.flush()
    return rec


def _require_nodes(kg: KnowledgeGraph, node_ids: list[str]) -> None:
    missing = [n for n in node_ids if not kg.has_node(n)]
    if missing:
        raise ValidationFailed(f"unknown KG node(s) in requires: {', '.join(missing)}", code="unknown_node")


# ===================================================================== kg
async def kg_snapshot(session: AsyncSession, tenant_id: str, node_type: str | None = None) -> dict[str, Any]:
    kg = await KGRepository(session, tenant_id).load()
    snap = kg.snapshot()
    if node_type is None:
        return snap
    nodes = [n for n in snap["nodes"] if n["type"] == node_type]
    ids = {n["id"] for n in nodes}
    edges = [e for e in snap["edges"] if e["source"] in ids or e["target"] in ids]
    return {"nodes": nodes, "edges": edges, "counts": snap["counts"]}
