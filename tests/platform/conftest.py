"""Shared platform fixtures.

- ``settings``: test settings on a fresh SQLite file (or PLATFORM_TEST_DATABASE_URL).
- ``db`` / ``session``: database with all tables created.
- ``tenant`` / ``other_tenant``: two customer orgs, for isolation tests.
- ``app`` / ``client``: the full API (lifespan run) with an httpx client.
- ``act_as(principal)``: bypass login in non-auth tests; ``principal_for(tenant, *roles)``.
- ``new_tenant(slug)``: create another tenant.

Tests use --import-mode=importlib: share helpers via fixtures, not imports.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.app import create_app
from app.platform.config import PlatformSettings, generate_master_key
from app.platform.core_models import Tenant
from app.platform.db import Database
from app.platform.principal import Principal, Role, current_principal


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic: tests never reach the real LLM unless marked live_llm."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return os.environ.get("PLATFORM_TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def settings(database_url: str) -> PlatformSettings:
    return PlatformSettings(
        env="test",
        database_url=database_url,
        jwt_secret="test-jwt-secret-" + "x" * 32,  # type: ignore[arg-type]
        master_key=generate_master_key(),  # type: ignore[arg-type]
        sandbox_backend="subprocess",
        log_json=False,
        login_rate_limit_per_minute=1000,
    )


@pytest.fixture
async def db(settings: PlatformSettings) -> AsyncIterator[Database]:
    database = Database(settings.database_url)
    if database.is_postgres:
        await database.drop_all()
    await database.create_all()
    try:
        yield database
    finally:
        await database.dispose()


@pytest.fixture
async def session(db: Database) -> AsyncIterator[AsyncSession]:
    async with db.session() as s:
        yield s


async def make_tenant(session: AsyncSession, slug: str) -> Tenant:
    tenant = Tenant(name=slug.title(), slug=slug)
    session.add(tenant)
    await session.commit()
    return tenant


@pytest.fixture
async def tenant(session: AsyncSession) -> Tenant:
    return await make_tenant(session, "acme")


@pytest.fixture
async def other_tenant(session: AsyncSession) -> Tenant:
    return await make_tenant(session, "globex")


@pytest.fixture
def app(settings: PlatformSettings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI, db: Database) -> AsyncIterator[httpx.AsyncClient]:
    # Point the app at the already-created test database.
    app.state.db = db
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://platform.test") as c:
            yield c


def _principal_for(tenant: Tenant, *roles: Role, user_id: str = "user-1", email: str = "u@example.com") -> Principal:
    return Principal(user_id=user_id, tenant_id=tenant.id, email=email, roles=frozenset(roles))


@pytest.fixture
def principal_for() -> Callable[..., Principal]:
    """principal_for(tenant, Role.PM, user_id=..., email=...) -> Principal"""
    return _principal_for


@pytest.fixture
def new_tenant(session: AsyncSession) -> Callable[[str], object]:
    """await new_tenant("slug") -> Tenant"""

    async def _make(slug: str) -> Tenant:
        return await make_tenant(session, slug)

    return _make


@pytest.fixture
def act_as(app: FastAPI) -> Callable[[Principal], None]:
    def _set(principal: Principal) -> None:
        async def _override() -> Principal:
            return principal

        app.dependency_overrides[current_principal] = _override

    return _set
