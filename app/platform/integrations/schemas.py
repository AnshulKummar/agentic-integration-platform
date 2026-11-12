"""Request/response models (camelCase JSON, like ``app.demo.models``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.demo.models import ApiChange, KGEdge, KGNode, MappingRow, RiskCheck, RoadmapColumn, RoadmapStatus
from app.platform.core_models import ApiReleaseRecord, Approval, Integration, IntegrationVersion, RoadmapItemRecord
from app.platform.integrations.service import ReleaseOutcome, VersionBundle


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class StrictApiModel(ApiModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


# ---------------------------------------------------------------------- KG
class BootstrapOut(ApiModel):
    nodes_added: int
    edges_added: int
    roadmap_items_added: int
    context_integrations_added: int
    baseline_releases_added: int
    counts: dict[str, int]


class KGOut(ApiModel):
    nodes: list[KGNode]
    edges: list[KGEdge]
    counts: dict[str, int]


# ----------------------------------------------------------------- roadmap
class RoadmapCreate(StrictApiModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    intent: str = ""
    column: RoadmapColumn = "later"
    status: RoadmapStatus = "planned"
    quarter: str = Field(default="", max_length=16)
    owner: str = Field(default="", max_length=200)
    requires: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None


class RoadmapPatch(StrictApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    intent: str | None = None
    column: RoadmapColumn | None = None
    status: RoadmapStatus | None = None
    quarter: str | None = Field(default=None, max_length=16)
    owner: str | None = Field(default=None, max_length=200)
    requires: list[str] | None = None
    blocked_reason: str | None = None


class RoadmapOut(ApiModel):
    id: str
    title: str
    description: str
    intent: str
    column: str
    status: str
    quarter: str
    owner: str
    requires: list[str]
    blocked_reason: str | None
    fit_score: float | None
    fit_rationale: str | None
    integration_id: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, rec: RoadmapItemRecord) -> RoadmapOut:
        return cls.model_validate(rec, from_attributes=True)


# ------------------------------------------------------------- integrations
class IntegrationCreate(StrictApiModel):
    intent: str = Field(min_length=1, max_length=4000)
    source_system: str | None = None
    target_system: str | None = None
    trigger: str | None = None
    name: str | None = Field(default=None, max_length=200)
    roadmap_item_id: str | None = None


class EditIn(StrictApiModel):
    target: str = Field(min_length=1)
    source: str | None = None
    transform: str | None = None


class EditsIn(StrictApiModel):
    edits: list[EditIn] = Field(min_length=1)


class ApprovalIn(StrictApiModel):
    role: Literal["pm", "eng"]
    comment: str = Field(default="", max_length=2000)


class ApprovalOut(ApiModel):
    role: str
    approver_id: str
    comment: str
    created_at: datetime

    @classmethod
    def of(cls, a: Approval) -> ApprovalOut:
        return cls.model_validate(a, from_attributes=True)


class VersionSummary(ApiModel):
    id: str
    number: int
    status: str
    confidence: float
    human_edits: int
    created_at: datetime

    @classmethod
    def of(cls, v: IntegrationVersion) -> VersionSummary:
        return cls.model_validate(v, from_attributes=True)


class PlanOut(ApiModel):
    operations: list[str]
    source_systems: list[str]
    target_systems: list[str]
    trigger_node: str | None
    endpoints: list[str]
    service_principal: str | None
    patterns_applied: list[str]
    rules_applied: list[str]
    migrations: list[str]
    pattern_id: str | None
    pattern_label: str | None


class VersionOut(ApiModel):
    id: str
    integration_id: str
    number: int
    status: str
    mappings: list[MappingRow]
    code: str
    allowed_tools: list[str]
    confidence: float
    human_edits: int
    pattern_reused: str | None
    created_by: str | None
    created_at: datetime
    approvals: list[ApprovalOut]
    risks: list[RiskCheck]
    edit_log: list[dict[str, Any]]
    plan: PlanOut
    learned_at: datetime | None

    @classmethod
    def of(cls, b: VersionBundle) -> VersionOut:
        v, p = b.version, b.plan
        return cls(
            id=v.id, integration_id=v.integration_id, number=v.number, status=v.status,
            mappings=[MappingRow.model_validate(m) for m in v.mappings], code=v.code,
            allowed_tools=list(v.allowed_tools), confidence=v.confidence, human_edits=v.human_edits,
            pattern_reused=v.pattern_reused, created_by=v.created_by, created_at=v.created_at,
            approvals=[ApprovalOut.of(a) for a in b.approvals],
            risks=[RiskCheck.model_validate(r) for r in b.plan_record.risks],
            edit_log=list(b.plan_record.edit_log),
            plan=PlanOut.model_validate(p.model_dump()),
            learned_at=b.plan_record.learned_at,
        )


class IntegrationOut(ApiModel):
    id: str
    name: str
    source_system: str
    target_system: str
    trigger: str
    initiated_by: str
    intent: str
    roadmap_item_id: str | None
    author_id: str | None
    active_version_id: str | None
    created_at: datetime
    updated_at: datetime
    latest_version: VersionSummary | None = None
    versions: list[VersionSummary] | None = None

    @classmethod
    def of(
        cls,
        i: Integration,
        latest: IntegrationVersion | None = None,
        versions: list[IntegrationVersion] | None = None,
    ) -> IntegrationOut:
        out = cls.model_validate(i, from_attributes=True)
        out.latest_version = VersionSummary.of(latest) if latest else None
        out.versions = [VersionSummary.of(v) for v in versions] if versions is not None else None
        return out


class DraftOut(ApiModel):
    integration: IntegrationOut
    version: VersionOut

    @classmethod
    def of(cls, b: VersionBundle) -> DraftOut:
        return cls(integration=IntegrationOut.of(b.integration, b.version), version=VersionOut.of(b))


# ----------------------------------------------------------------- releases
class ReleaseIn(StrictApiModel):
    system_id: str = Field(min_length=1, max_length=100)
    app: str | None = Field(default=None, max_length=200)
    version: str | None = Field(default=None, max_length=50)
    spec: dict[str, Any]


class ReleaseOut(ApiModel):
    id: str
    system_id: str
    app: str
    version: str
    previous_version: str | None
    status: str
    changes: list[ApiChange]
    mcp_tools: list[str]
    impact: list[str]
    created_at: datetime

    @classmethod
    def of(cls, r: ApiReleaseRecord) -> ReleaseOut:
        return cls(
            id=r.id, system_id=r.system_id, app=r.app, version=r.version, previous_version=r.previous_version,
            status=r.status, changes=[ApiChange.model_validate(c) for c in r.changes], mcp_tools=list(r.mcp_tools),
            impact=list(r.impact), created_at=r.created_at,
        )


class AlignmentOut(ApiModel):
    source: str
    target: str
    edge_type: str
    confidence: float
    accepted: bool
    reason: str


class RoadmapMatchOut(ApiModel):
    item_id: str
    title: str
    fit_score: float | None
    rationale: str
    unblocked: bool
    partial: bool
    impacted: bool


class DeprecatedFieldOut(ApiModel):
    field: str
    replaced_by: str | None
    used_by: list[str]


class ReleaseResultOut(ApiModel):
    release: ReleaseOut
    alignments: list[AlignmentOut]
    roadmap_matches: list[RoadmapMatchOut]
    deprecated_fields: list[DeprecatedFieldOut]
    drafted: DraftOut | None
    agent_error: str | None

    @classmethod
    def of(cls, o: ReleaseOutcome) -> ReleaseResultOut:
        return cls(
            release=ReleaseOut.of(o.release),
            alignments=[AlignmentOut.model_validate(a.model_dump()) for a in o.alignments],
            roadmap_matches=[RoadmapMatchOut.model_validate(m.model_dump()) for m in o.matches],
            deprecated_fields=[DeprecatedFieldOut(field=d.field, replaced_by=d.replaced_by, used_by=d.used_by)
                               for d in o.deprecated],
            drafted=DraftOut.of(o.drafted) if o.drafted else None,
            agent_error=o.agent_error,
        )
