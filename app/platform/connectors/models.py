"""Tables for the connectors area. OWNED BY: secrets/connectors area (B)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, TenantScoped, TimestampMixin, new_id


class ConnectionRecord(TenantScoped, TimestampMixin, Base):
    """How one tenant reaches one external system.

    ``auth`` holds secret NAMES only (``AuthConfig`` as JSON); values are revealed
    from the SecretStore at call time and never stored here.
    """

    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("tenant_id", "system_id", name="uq_connections_tenant_system"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    system_id: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    auth: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    tool_prefix: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    health_path: Mapped[str] = mapped_column(String(200), nullable=False, default="/health")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
