"""Database layer: declarative base, mixins, engine/session, tenant guard.

Tenant isolation has two layers:
1. Repositories (``tenancy.TenantRepository``) always filter by tenant.
2. A session bound to a tenant (``bind_tenant``) automatically adds
   ``tenant_id = :tenant`` to every ORM SELECT on tenant-scoped models and
   rejects flushing rows that belong to another tenant. On PostgreSQL,
   row-level-security policies (migration) are a third layer.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    ORMExecuteState,
    Session,
    declared_attr,
    mapped_column,
    with_loader_criteria,
)

TENANT_KEY = "tenant_id"


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class TenantScoped:
    """Mixin for every row that belongs to one customer org."""

    @declared_attr
    def tenant_id(cls) -> Mapped[str]:  # noqa: N805
        return mapped_column(String(32), ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)


class TenantViolation(PermissionError):
    """A write tried to cross a tenant boundary."""


def bind_tenant(session: AsyncSession, tenant_id: str) -> AsyncSession:
    session.info[TENANT_KEY] = tenant_id
    return session


def _session_tenant(session: Session) -> str | None:
    value = session.info.get(TENANT_KEY)
    return value if isinstance(value, str) else None


@event.listens_for(Session, "do_orm_execute")
def _scope_selects(state: ORMExecuteState) -> None:
    tenant_id = _session_tenant(state.session)
    if tenant_id is None or not state.is_select or state.execution_options.get("skip_tenant_filter"):
        return
    state.statement = state.statement.options(
        with_loader_criteria(TenantScoped, lambda cls: cls.tenant_id == tenant_id, include_aliases=True)
    )


@event.listens_for(Session, "before_flush")
def _guard_writes(session: Session, _ctx: Any, _instances: Any) -> None:
    tenant_id = _session_tenant(session)
    if tenant_id is None:
        return
    for obj in [*session.new, *session.dirty]:
        if isinstance(obj, TenantScoped):
            row_tenant = getattr(obj, "tenant_id", None)
            if row_tenant is None:
                obj.tenant_id = tenant_id
            elif row_tenant != tenant_id:
                raise TenantViolation(f"{type(obj).__name__} belongs to another tenant")
    for obj in session.deleted:
        if isinstance(obj, TenantScoped) and getattr(obj, "tenant_id", None) != tenant_id:
            raise TenantViolation(f"cannot delete {type(obj).__name__} of another tenant")


@event.listens_for(Engine, "connect")
def _sqlite_fks(dbapi_conn: Any, _record: Any) -> None:
    # Enforce FKs on SQLite (no-op elsewhere).
    if type(dbapi_conn).__module__.startswith(("sqlite3", "aiosqlite")):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, echo=echo, pool_pre_ping=not url.startswith("sqlite"))
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @property
    def is_postgres(self) -> bool:
        return self.url.startswith("postgresql")

    async def create_all(self) -> None:
        from app.platform import models_registry  # noqa: F401  (registers every table)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        from app.platform import models_registry  # noqa: F401

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def ping(self) -> bool:
        from sqlalchemy import text

        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    @asynccontextmanager
    async def session(self, tenant_id: str | None = None) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            if tenant_id is not None:
                bind_tenant(session, tenant_id)
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
