"""Tables for the auth area. OWNED BY: auth area (A).

- ``User`` is global (one identity, many orgs); it is NOT tenant-scoped.
- ``Membership`` grants a user one role in one tenant (several rows = several roles).
- ``RefreshToken`` is an opaque, hashed, single-use session credential bound to
  one (user, tenant). Rotated on every use; ``family_id`` groups a login session
  so reuse of a rotated token can revoke the whole chain.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

# Registers the PostgreSQL row-level-security session listener (see rls.py).
from app.platform.auth import rls as _rls  # noqa: F401
from app.platform.db import Base, TenantScoped, TimestampMixin, new_id, utcnow


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    #: Always stored lowercase (normalised by the service layer).
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    #: argon2id PHC string; never returned or logged.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Membership(TenantScoped, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "role", name="uq_membership_tenant_user_role"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: One of app.platform.principal.Role values.
    role: Mapped[str] = mapped_column(String(16), nullable=False)


class RefreshToken(TenantScoped, Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: sha256 hex of the opaque token; the token itself is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    family_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set when this token was rotated; presenting it again is token reuse (theft).
    replaced_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
