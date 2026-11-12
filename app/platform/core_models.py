"""Shared tables used by more than one area. Area-specific tables live in
``<area>/models.py`` (auth, secrets, deployments, evals).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, TenantScoped, TimestampMixin, new_id, utcnow


class Tenant(TimestampMixin, Base):
    """A customer org. Everything else hangs off one of these."""

    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    #: Opt-in to KG learning (the ablation switch, per customer).
    learning_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class VersionStatus:
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    DEPLOYING = "deploying"
    LIVE = "live"
    ROLLED_BACK = "rolled_back"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    ALL = (DRAFT, IN_REVIEW, APPROVED, DEPLOYING, LIVE, ROLLED_BACK, SUPERSEDED, FAILED)


class Integration(TenantScoped, TimestampMixin, Base):
    __tablename__ = "integrations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    target_system: Mapped[str] = mapped_column(String(100), nullable=False)
    trigger: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    #: 'user_intent' | 'agent_from_roadmap'
    initiated_by: Mapped[str] = mapped_column(String(32), nullable=False, default="user_intent")
    intent: Mapped[str] = mapped_column(Text, nullable=False, default="")
    roadmap_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    author_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The version currently serving production traffic (None until first deploy).
    active_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class IntegrationVersion(TenantScoped, Base):
    __tablename__ = "integration_versions"
    __table_args__ = (UniqueConstraint("integration_id", "number"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    integration_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("integrations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=VersionStatus.DRAFT)
    #: list of app.demo.models.MappingRow dicts (camelCase as produced by to_json()).
    mappings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    #: Generated, sandbox-runnable code (see sandbox codegen contract in interfaces.py).
    code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: MCP tool names this version may call; the broker rejects anything else.
    allowed_tools: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    human_edits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pattern_reused: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Approval(TenantScoped, Base):
    __tablename__ = "approvals"
    __table_args__ = (UniqueConstraint("version_id", "role"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("integration_versions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: 'pm' | 'eng'
    role: Mapped[str] = mapped_column(String(8), nullable=False)
    approver_id: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class KGNodeRecord(TenantScoped, Base):
    __tablename__ = "kg_nodes"
    __table_args__ = (UniqueConstraint("tenant_id", "node_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    system: Mapped[str | None] = mapped_column(String(100), nullable=True)
    learned_in: Mapped[str] = mapped_column(String(32), nullable=False, default="seed")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class KGEdgeRecord(TenantScoped, Base):
    __tablename__ = "kg_edges"
    __table_args__ = (UniqueConstraint("tenant_id", "edge_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    edge_id: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    target: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    learned_in: Mapped[str] = mapped_column(String(32), nullable=False, default="seed")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class RoadmapItemRecord(TenantScoped, TimestampMixin, Base):
    __tablename__ = "roadmap_items"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    intent: Mapped[str] = mapped_column(Text, nullable=False, default="")
    column: Mapped[str] = mapped_column(String(16), nullable=False, default="later")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="planned")
    quarter: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    owner: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    requires: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    fit_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    integration_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ApiReleaseRecord(TenantScoped, Base):
    __tablename__ = "api_releases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    system_id: Mapped[str] = mapped_column(String(100), nullable=False)
    app: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    previous_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    mcp_tools: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    impact: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ExecutionRecord(TenantScoped, Base):
    """One run of an integration version on one event. Health checks read these."""

    __tablename__ = "executions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    integration_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    version_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    deployment_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    #: 'sandbox' | 'staging' | 'canary' | 'production' | 'synthetic'
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    event_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)


class AuditEvent(TenantScoped, Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    target_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
