"""Tables for the deployments area. OWNED BY: deployments/observability area (D).

- ``Deployment``: one attempt to move an approved IntegrationVersion to production.
- ``DeploymentStageRecord``: one row per stage (sandbox, staging, canary, production, watch).
- ``HealthCheckRecord``: every health check evaluated, with observed value vs threshold.
- ``Job``: the durable work queue the worker drains (not tenant-scoped: system jobs have no tenant).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, TenantScoped, TimestampMixin, new_id, utcnow


class DeploymentStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    ACTIVE = (PENDING, RUNNING)
    TERMINAL = (SUCCEEDED, FAILED, ROLLED_BACK)


class Stage:
    SANDBOX = "sandbox"
    STAGING = "staging"
    CANARY = "canary"
    PRODUCTION = "production"
    WATCH = "watch"
    ORDER = (SANDBOX, STAGING, CANARY, PRODUCTION, WATCH)


class StageStatus:
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Deployment(TenantScoped, TimestampMixin, Base):
    __tablename__ = "deployments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    integration_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("integrations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("integration_versions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: Version that was active when the deployment started (rollback target; None on first deploy).
    previous_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=DeploymentStatus.PENDING)
    current_stage: Mapped[str] = mapped_column(String(16), nullable=False, default=Stage.SANDBOX)
    canary_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    initiated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    canary_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    watch_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeploymentStageRecord(TenantScoped, Base):
    __tablename__ = "deployment_stages"
    __table_args__ = (UniqueConstraint("deployment_id", "stage"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    deployment_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deployments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Position in Stage.ORDER, for stable ordering.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=StageStatus.PENDING)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HealthCheckRecord(TenantScoped, Base):
    __tablename__ = "deployment_health_checks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    deployment_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("deployments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    check: Mapped[str] = mapped_column(String(64), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Job(TimestampMixin, Base):
    """Durable work item. ``tenant_id`` is None for system jobs."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "run_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    tenant_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=True
    )
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
