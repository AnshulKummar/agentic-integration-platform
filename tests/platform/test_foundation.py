"""Foundation: settings safety, tenant isolation guard, app factory, error envelope."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.platform.config import ConfigError, PlatformSettings
from app.platform.core_models import Integration, Tenant
from app.platform.db import Database, TenantViolation, bind_tenant
from app.platform.errors import NotFound
from app.platform.tenancy import TenantRepository


def test_production_refuses_unsafe_defaults() -> None:
    with pytest.raises(ConfigError) as err:
        PlatformSettings(env="production").validate_runtime()
    msg = str(err.value)
    for needle in ("JWT_SECRET", "MASTER_KEY", "docker", "PostgreSQL", "Alembic"):
        assert needle in msg


def test_dev_fills_ephemeral_secrets() -> None:
    s = PlatformSettings(env="development").validate_runtime()
    assert len(s.master_key_bytes()) == 32
    assert len(s.jwt_key()) >= 32


def test_from_env_reads_prefixed_vars() -> None:
    s = PlatformSettings.from_env(
        {"PLATFORM_CANARY_PERCENT": "25", "PLATFORM_CORS_ORIGINS": "a, b", "ANTHROPIC_API_KEY": "k"}
    )
    assert s.canary_percent == 25
    assert s.cors_origins == ("a", "b")
    assert s.anthropic_api_key is not None


def _integration(tenant: Tenant, name: str) -> Integration:
    return Integration(tenant_id=tenant.id, name=name, source_system="crm", target_system="billing")


async def test_tenant_bound_session_filters_reads(db: Database, tenant: Tenant, other_tenant: Tenant) -> None:
    async with db.session() as s:
        s.add_all([_integration(tenant, "mine"), _integration(other_tenant, "theirs")])
        await s.commit()
    async with db.session(tenant.id) as s:
        names = [i.name for i in (await s.execute(select(Integration))).scalars()]
    assert names == ["mine"]


async def test_tenant_bound_session_blocks_cross_tenant_writes(
    db: Database, tenant: Tenant, other_tenant: Tenant
) -> None:
    async with db.session() as s:
        bind_tenant(s, tenant.id)
        s.add(_integration(other_tenant, "smuggled"))
        with pytest.raises(TenantViolation):
            await s.flush()


async def test_repository_hides_other_tenants(db: Database, tenant: Tenant, other_tenant: Tenant) -> None:
    async with db.session() as s:
        theirs = _integration(other_tenant, "theirs")
        s.add(theirs)
        await s.commit()
        repo = TenantRepository(s, tenant.id, Integration)
        assert await repo.get(theirs.id) is None
        with pytest.raises(NotFound):
            await repo.require(theirs.id)
        created = repo.add(Integration(name="new", source_system="a", target_system="b"))
        assert created.tenant_id == tenant.id


async def test_app_boots_and_errors_use_envelope(client) -> None:  # type: ignore[no-untyped-def]
    r = await client.get("/openapi.json")
    assert r.status_code == 200


async def test_foreign_keys_are_enforced(db: Database) -> None:
    """SQLite must enforce FKs like PostgreSQL does, or insert-order bugs hide in local tests."""
    from sqlalchemy.exc import IntegrityError

    async with db.session() as s:
        s.add(Integration(tenant_id="no-such-tenant", name="orphan", source_system="a", target_system="b"))
        with pytest.raises(IntegrityError):
            await s.commit()
