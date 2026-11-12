"""PostgreSQL row-level security: the third tenant-isolation layer.

Layers 1 and 2 (repositories, the tenant-bound session guard in ``db.py``) only
protect ORM statements. RLS also covers raw SQL, Core statements and
``skip_tenant_filter`` queries issued from a tenant-bound session.

How it works
------------
* ``rls_statements()`` returns DDL that ENABLEs + FORCEs RLS and creates a
  ``tenant_isolation`` policy on every table whose model uses ``TenantScoped``.
  The Alembic migration (or an operator) runs it; ``create_all`` does not.
* An ``after_begin`` listener runs, on PostgreSQL only, at the start of every
  transaction:
    - tenant-bound session (``session.info['tenant_id']``) ->
      ``set_config('app.tenant_id', <tid>, true)`` (transaction-local);
    - session explicitly marked cross-tenant via ``allow_cross_tenant`` ->
      ``set_config('app.rls_bypass', 'on', true)``;
    - anything else sets nothing, so it sees NO tenant-scoped rows (fail closed).

Deviation from the literal brief policy (``tenant_id = current_setting(...)``):
the policy also admits ``app.rls_bypass = 'on'``. Without it, identity flows that
run before a tenant is known (login listing a user's memberships, refresh-token
lookup by hash, signup) and system jobs that iterate all tenants could not work
under FORCE RLS. The bypass is opt-in per session and never set for a
tenant-bound session. Like ``app.tenant_id`` it is a session GUC, so RLS defends
against application bugs, not against SQL injection.

Note: superusers and roles with BYPASSRLS ignore RLS entirely; the application
must connect as an ordinary role (table owner is fine because of FORCE).
"""

from __future__ import annotations

import re

from sqlalchemy import Connection, Table, event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from app.platform.db import TENANT_KEY, Base, TenantScoped

RLS_BYPASS_KEY = "rls_bypass"
POLICY_NAME = "tenant_isolation"
TENANT_GUC = "app.tenant_id"
BYPASS_GUC = "app.rls_bypass"

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_CONDITION = (
    f"(tenant_id = current_setting('{TENANT_GUC}', true)) OR (current_setting('{BYPASS_GUC}', true) = 'on')"
)


def allow_cross_tenant(session: AsyncSession | Session) -> AsyncSession | Session:
    """Mark an UNBOUND session as a deliberate cross-tenant (system/identity) session.

    Raises if the session is already tenant-bound: a bound session never bypasses RLS.
    """
    if isinstance(session.info.get(TENANT_KEY), str):
        raise ValueError("a tenant-bound session cannot be marked cross-tenant")
    session.info[RLS_BYPASS_KEY] = True
    return session


def tenant_scoped_tables() -> list[Table]:
    """Every mapped table whose model uses the TenantScoped mixin, sorted by name."""
    from app.platform import models_registry  # noqa: F401  (registers every table)

    tables: dict[str, Table] = {}
    for mapper in Base.registry.mappers:
        if issubclass(mapper.class_, TenantScoped) and isinstance(mapper.local_table, Table):
            tables[mapper.local_table.name] = mapper.local_table
    return [tables[name] for name in sorted(tables)]


def _checked(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unexpected table name for RLS DDL: {name!r}")
    return name


def rls_statements() -> list[str]:
    """DDL enabling + forcing RLS with the tenant policy on all tenant-scoped tables (idempotent)."""
    statements: list[str] = []
    for table in tenant_scoped_tables():
        name = _checked(table.name)
        statements += [
            f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS {POLICY_NAME} ON {name}",
            f"CREATE POLICY {POLICY_NAME} ON {name} USING ({_CONDITION}) WITH CHECK ({_CONDITION})",
        ]
    return statements


def rls_teardown_statements() -> list[str]:
    """Inverse of ``rls_statements`` (for a migration downgrade)."""
    statements: list[str] = []
    for table in tenant_scoped_tables():
        name = _checked(table.name)
        statements += [
            f"DROP POLICY IF EXISTS {POLICY_NAME} ON {name}",
            f"ALTER TABLE {name} NO FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY",
        ]
    return statements


@event.listens_for(Session, "after_begin")
def _set_rls_context(session: Session, _transaction: SessionTransaction, connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    tenant_id = session.info.get(TENANT_KEY)
    if isinstance(tenant_id, str):
        connection.execute(text("SELECT set_config(:guc, :tid, true)"), {"guc": TENANT_GUC, "tid": tenant_id})
    elif session.info.get(RLS_BYPASS_KEY) is True:
        connection.execute(text("SELECT set_config(:guc, 'on', true)"), {"guc": BYPASS_GUC})
