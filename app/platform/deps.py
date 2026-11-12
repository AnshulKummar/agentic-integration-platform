"""Common FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.config import PlatformSettings
from app.platform.db import Database, bind_tenant
from app.platform.observability.context import set_log_tenant
from app.platform.principal import Principal, current_principal


def get_settings(request: Request) -> PlatformSettings:
    settings: PlatformSettings = request.app.state.settings
    return settings


def get_database(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


async def get_session(db: Database = Depends(get_database)) -> AsyncIterator[AsyncSession]:
    """Unscoped session: only for auth flows that run before a tenant is known."""
    async with db.session() as session:
        yield session


async def get_tenant_session(
    principal: Principal = Depends(current_principal), db: Database = Depends(get_database)
) -> AsyncIterator[AsyncSession]:
    """Session bound to the caller's tenant (auto-filtered reads, guarded writes)."""
    set_log_tenant(principal.tenant_id, principal.user_id)
    async with db.session() as session:
        bind_tenant(session, principal.tenant_id)
        yield session
