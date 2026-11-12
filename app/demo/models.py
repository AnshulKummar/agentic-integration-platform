"""Shared data models for the PMAIP demo backend.

These mirror the TypeScript contract in ``demo/src/scenario/types.ts``. Python
attributes are snake_case; JSON is camelCase (``learnedIn``, ``fitScore`` ...) so
the click-through frontend and this backend speak the same shapes.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

AgentKind = Literal["user", "llm", "kg", "mcp", "governance", "context", "external"]
TraceTone = Literal["call", "return", "info", "warn", "success"]
NodeType = Literal[
    "system", "business_object", "data_field", "api_endpoint", "pattern", "rule", "process", "roadmap"
]
EdgeType = Literal[
    "BELONGS_TO",
    "HAS_FIELD",
    "MAPS_TO",
    "EQUIVALENT_TO",
    "CONNECTS_TO",
    "TRIGGERS",
    "REQUIRES",
    "DERIVED_FROM",
    "APPLIES_TO",
    "IMPLEMENTS",
    "DEPRECATES",
]
LearnedIn = Literal["seed", "loop1", "loop2"]
RoadmapColumn = Literal["now", "next", "later", "shipped"]
RoadmapStatus = Literal["planned", "blocked", "in_progress", "in_review", "shipped"]
ApiChangeKind = Literal["added", "deprecated", "changed"]
ApiMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "WEBHOOK", "FIELD"]
MappingOrigin = Literal["kg_reused", "kg_inferred", "llm_new", "human_edit"]
IntegrationStatus = Literal["planned", "in_review", "approved", "deploying", "live", "rolled_back"]
StageName = Literal["sandbox", "staging", "canary", "production"]
StageStatus = Literal["pending", "running", "passed", "failed", "rolled_back"]
Role = Literal["pm", "eng"]


class CamelModel(BaseModel):
    """Base model: snake_case in Python, camelCase on the wire."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")


class KGNode(CamelModel):
    id: str
    label: str
    type: NodeType
    system: str | None = None
    learned_in: LearnedIn = "seed"
    confidence: float | None = None
    note: str | None = None
    # Backend-only structured payload (pattern mappings, rule definitions ...).
    props: dict[str, Any] | None = None


class KGEdge(CamelModel):
    id: str
    source: str
    target: str
    type: EdgeType
    learned_in: LearnedIn = "seed"
    confidence: float | None = None

    @staticmethod
    def make_id(source: str, edge_type: str, target: str) -> str:
        return f"{source}--{edge_type}--{target}"


class TraceEvent(CamelModel):
    id: str
    agent: AgentKind
    to: AgentKind | None = None
    lane: int | None = None
    text: str
    detail: str | None = None
    tone: TraceTone | None = None
    step: str | None = None


class RoadmapItem(CamelModel):
    id: str
    title: str
    description: str
    intent: str
    column: RoadmapColumn
    status: RoadmapStatus
    quarter: str
    owner: str
    requires: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None
    fit_score: float | None = None
    fit_rationale: str | None = None
    integration_id: str | None = None
    impacted: bool | None = None


class ApiChange(CamelModel):
    kind: ApiChangeKind
    method: ApiMethod
    path: str
    summary: str
    replaced_by: str | None = None


class ApiRelease(CamelModel):
    id: str
    app: str
    system: str
    version: str
    previous_version: str
    published_at: str
    status: Literal["new", "analyzing", "analyzed"] = "new"
    changes: list[ApiChange] = Field(default_factory=list)
    mcp_tools: list[str] | None = None
    impact: list[str] | None = None
    affected_operations: list[str] | None = None


class MappingRow(CamelModel):
    id: str
    source: str
    target: str
    transform: str | None = None
    origin: MappingOrigin
    confidence: float
    flag: str | None = None
    rule_id: str | None = None
    pattern_id: str | None = None


class TestResult(CamelModel):
    __test__ = False  # not a pytest class

    name: str
    passed: bool
    detail: str | None = None
    target_field: str | None = None


class RiskCheck(CamelModel):
    label: str
    level: Literal["low", "medium", "high"]
    note: str


class Approval(CamelModel):
    role: Role
    by: str
    comment: str


class Approvals(CamelModel):
    pm: Approval | None = None
    eng: Approval | None = None


class PipelineStage(CamelModel):
    name: StageName
    status: StageStatus = "pending"
    note: str = ""


class ChatMessage(CamelModel):
    id: str
    role: Literal["user", "agent"]
    text: str
    options: list[str] | None = None


class HumanEdit(CamelModel):
    """A reviewer's correction to one mapping row (keyed by target field)."""

    target: str
    source: str | None = None
    transform: str | None = None
    by: str = "Marco (Eng)"


class Integration(CamelModel):
    id: str
    name: str
    loop: Literal[1, 2]
    source: str
    target: str
    trigger: str
    status: IntegrationStatus = "planned"
    initiated_by: Literal["user_intent", "agent_from_roadmap"] = "user_intent"
    mappings: list[MappingRow] = Field(default_factory=list)
    code: str = ""
    tests: list[TestResult] = Field(default_factory=list)
    risks: list[RiskCheck] = Field(default_factory=list)
    approvals: Approvals = Field(default_factory=Approvals)
    human_edits: int = 0
    turns: int = 0
    confidence: float = 0.0
    pattern_reused: str | None = None
    # Backend-only fields.
    roadmap_id: str | None = None
    intent: str | None = None
    operations: list[str] = Field(default_factory=list)
    endpoints: list[str] = Field(default_factory=list)
    trigger_node: str | None = None
    source_systems: list[str] = Field(default_factory=list)
    target_systems: list[str] = Field(default_factory=list)
    patterns_applied: list[str] = Field(default_factory=list)
    rules_applied: list[str] = Field(default_factory=list)
    service_principal: str | None = None
    tools_used: list[str] = Field(default_factory=list)
    max_calls_per_event: int = 20
    migrations: list[str] = Field(default_factory=list)
    pattern_id: str | None = None
    pattern_label: str | None = None
    pipeline: list[PipelineStage] = Field(default_factory=list)
    edit_log: list[HumanEdit] = Field(default_factory=list)
