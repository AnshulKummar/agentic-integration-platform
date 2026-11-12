"""Auth-area fixtures. Helpers are exposed as fixtures (importlib import mode)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

from app.platform.auth import passwords
from app.platform.core_models import AuditEvent
from app.platform.db import Database

PASSWORD = "correct horse battery staple"

JsonDict = dict[str, Any]


@pytest.fixture(autouse=True)
def _fast_hasher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cheap argon2 parameters keep the suite fast; production uses library defaults."""
    monkeypatch.setattr(passwords, "_hasher", PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1))


@pytest.fixture
def password() -> str:
    return PASSWORD


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_header() -> Callable[[str], dict[str, str]]:
    """auth_header(access_token) -> headers"""
    return bearer


@pytest.fixture
def signup(client: httpx.AsyncClient) -> Callable[..., Awaitable[JsonDict]]:
    """await signup(slug, email, password=PASSWORD) -> token response JSON (asserts 201)."""

    async def _signup(slug: str, email: str, password: str = PASSWORD, display_name: str = "") -> JsonDict:
        r = await client.post(
            "/v1/auth/signup",
            json={
                "org_name": slug.title(),
                "org_slug": slug,
                "email": email,
                "password": password,
                "display_name": display_name,
            },
        )
        assert r.status_code == 201, r.text
        body: JsonDict = r.json()
        return body

    return _signup


@pytest.fixture
def login(client: httpx.AsyncClient) -> Callable[..., Awaitable[httpx.Response]]:
    """await login(email, password=PASSWORD, tenant_slug=None) -> raw response."""

    async def _login(email: str, password: str = PASSWORD, tenant_slug: str | None = None) -> httpx.Response:
        payload: JsonDict = {"email": email, "password": password}
        if tenant_slug is not None:
            payload["tenant_slug"] = tenant_slug
        return await client.post("/v1/auth/login", json=payload)

    return _login


@pytest.fixture
def add_member(client: httpx.AsyncClient) -> Callable[..., Awaitable[httpx.Response]]:
    """await add_member(admin_token, email, roles, initial_password=...) -> raw response."""

    async def _add(token: str, email: str, roles: list[str], initial_password: str = PASSWORD + "!") -> httpx.Response:
        return await client.post(
            "/v1/members",
            json={"email": email, "roles": roles, "initial_password": initial_password},
            headers=bearer(token),
        )

    return _add


@pytest.fixture
def audit_actions(db: Database) -> Callable[[str], Awaitable[list[AuditEvent]]]:
    """await audit_actions(tenant_id) -> audit rows of that tenant, oldest first."""

    async def _rows(tenant_id: str) -> list[AuditEvent]:
        async with db.session() as s:
            stmt = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.created_at)
            return list((await s.execute(stmt)).scalars().all())

    return _rows
