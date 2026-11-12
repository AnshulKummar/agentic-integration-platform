"""Row-level security: SQL generation (everywhere) and behaviour (PostgreSQL only)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.auth.rls import (
    POLICY_NAME,
    allow_cross_tenant,
    rls_statements,
    rls_teardown_statements,
    tenant_scoped_tables,
)
from app.platform.core_models import Integration, Tenant
from app.platform.db import Base, Database, TenantScoped


def test_covers_every_tenant_scoped_table_and_nothing_else() -> None:
    names = {t.name for t in tenant_scoped_tables()}
    assert {"integrations", "audit_events", "memberships", "refresh_tokens", "executions"} <= names
    assert "tenants" not in names and "users" not in names
    expected = {
        m.local_table.name for m in Base.registry.mappers if issubclass(m.class_, TenantScoped)
    }
    assert names == expected


def test_statements_enable_force_and_create_policy() -> None:
    stmts = rls_statements()
    for name in (t.name for t in tenant_scoped_tables()):
        assert f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY" in stmts
        assert f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY" in stmts
        create = next(s for s in stmts if s.startswith(f"CREATE POLICY {POLICY_NAME} ON {name} "))
        assert "tenant_id = current_setting('app.tenant_id', true)" in create
        assert "USING (" in create and "WITH CHECK (" in create
    # Idempotent: each CREATE is preceded by a DROP IF EXISTS.
    drops = sum(s.startswith("DROP POLICY IF EXISTS") for s in stmts)
    assert drops == sum(s.startswith("CREATE POLICY") for s in stmts)
    assert all(s.startswith(("DROP POLICY", "ALTER TABLE")) for s in rls_teardown_statements())


async def test_cross_tenant_flag_refused_on_bound_session(db: Database) -> None:
    async with db.session("t1") as session:
        with pytest.raises(ValueError):
            allow_cross_tenant(session)
    async with db.session() as session:
        assert allow_cross_tenant(session).info["rls_bypass"] is True


async def test_listener_is_a_noop_on_sqlite(db: Database, tenant: Tenant) -> None:
    async with db.session(tenant.id) as s:
        assert (await s.execute(text("SELECT 1"))).scalar_one() == 1


# --- PostgreSQL behaviour -------------------------------------------------------------

_PG = (os.environ.get("PLATFORM_TEST_DATABASE_URL") or "").startswith("postgresql")
_PROBE_ROLE = "pmaip_rls_probe"


@pytest.fixture
async def rls_db(db: Database) -> AsyncIterator[tuple[Database, str | None]]:
    """Apply RLS. Superusers bypass RLS, so in that case queries run under a probe role."""
    async with db.engine.begin() as conn:
        for stmt in rls_statements():
            await conn.execute(text(stmt))
        privileged = (
            await conn.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"))
        ).scalar_one()
        role: str | None = None
        if privileged:
            exists = (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": _PROBE_ROLE})).first()
            if exists is None:
                await conn.execute(text(f"CREATE ROLE {_PROBE_ROLE} NOLOGIN"))
            await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {_PROBE_ROLE}"))
            grant = f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {_PROBE_ROLE}"
            await conn.execute(text(grant))
            role = _PROBE_ROLE
    yield db, role


async def _as_probe(session: AsyncSession, role: str | None) -> None:
    # The first execute begins the transaction (after_begin sets the GUC); then drop privileges.
    if role is not None:
        await session.execute(text(f"SET LOCAL ROLE {role}"))


async def _names(session: AsyncSession) -> list[str]:
    return sorted((await session.execute(text("SELECT name FROM integrations"))).scalars().all())


@pytest.mark.postgres
@pytest.mark.skipif(not _PG, reason="needs PLATFORM_TEST_DATABASE_URL=postgresql...")
async def test_pg_raw_sql_is_tenant_filtered(
    rls_db: tuple[Database, str | None], tenant: Tenant, other_tenant: Tenant
) -> None:
    db, role = rls_db
    async with db.session() as s:
        allow_cross_tenant(s)
        await _as_probe(s, role)
        s.add_all([
            Integration(tenant_id=tenant.id, name="mine", source_system="a", target_system="b"),
            Integration(tenant_id=other_tenant.id, name="theirs", source_system="a", target_system="b"),
        ])
        await s.commit()

    async with db.session(tenant.id) as s:  # raw SQL bypasses the ORM filter; RLS still applies
        await _as_probe(s, role)
        assert await _names(s) == ["mine"]
    async with db.session() as s:  # unbound and not opted in: fail closed
        await _as_probe(s, role)
        assert await _names(s) == []
    async with db.session() as s:
        allow_cross_tenant(s)
        await _as_probe(s, role)
        assert await _names(s) == ["mine", "theirs"]


@pytest.mark.postgres
@pytest.mark.skipif(not _PG, reason="needs PLATFORM_TEST_DATABASE_URL=postgresql...")
async def test_pg_blocks_cross_tenant_raw_insert(
    rls_db: tuple[Database, str | None], tenant: Tenant, other_tenant: Tenant
) -> None:
    db, role = rls_db
    async with db.session(tenant.id) as s:
        await _as_probe(s, role)
        with pytest.raises(DBAPIError):
            await s.execute(
                text(
                    "INSERT INTO integrations (id, tenant_id, name, source_system, target_system, trigger, "
                    "initiated_by, intent, created_at, updated_at) VALUES ('x1', :t, 'smuggled', 'a', 'b', '', "
                    "'user_intent', '', now(), now())"
                ),
                {"t": other_tenant.id},
            )
