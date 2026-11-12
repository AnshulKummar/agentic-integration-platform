"""Tables owned by the integrations area.

``core_models.IntegrationVersion`` holds what other areas consume (mappings, code,
allowed tools, confidence). The drafting *plan* the domain logic needs to
re-render, re-assess and learn from a version (operations, systems, trigger,
patterns applied, edit log, risks) lives here, one row per version.

``ContextIntegrationRecord`` is the per-tenant "context store" of integrations
that run outside the platform (imported at bootstrap). The roadmap matcher and
risk checks read them to flag deprecated-field impact.

NOTE (contract): ``app/platform/models_registry.py`` should import this module so
Alembic sees these tables. Until then, ``app.platform.integrations.__init__``
imports it, which is enough for ``create_all`` (the app factory imports the router).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, TenantScoped, TimestampMixin, new_id


class VersionPlanRecord(TenantScoped, TimestampMixin, Base):
    __tablename__ = "integration_version_plans"
    __table_args__ = (UniqueConstraint("version_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("integration_versions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: camelCase plan: operations, sourceSystems, targetSystems, triggerNode, sourceObjects,
    #: triggerObject, endpoints, servicePrincipal, patternsApplied, rulesApplied, migrations,
    #: patternId, patternLabel, roadmapNode.
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: list of app.demo.models.RiskCheck dicts.
    risks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    #: list of app.demo.models.HumanEdit dicts (who changed what during review).
    edit_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    #: Set once the version has been learned from (learning is not repeated).
    learned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    learning_report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class ContextIntegrationRecord(TenantScoped, TimestampMixin, Base):
    __tablename__ = "context_integrations"
    __table_args__ = (UniqueConstraint("tenant_id", "external_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    roadmap_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pattern_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="live")
    #: KG node ids (fields, endpoints) the integration uses.
    uses: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    target_system: Mapped[str] = mapped_column(String(100), nullable=False, default="")
