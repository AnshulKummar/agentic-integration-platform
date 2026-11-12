"""Tenant-scoped repository: the primary isolation guard for application code."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.db import TenantScoped, TenantViolation
from app.platform.errors import NotFound


class TenantRepository[M: TenantScoped]:
    """CRUD that can only ever see or write rows of one tenant.

    Cross-tenant reads return ``None`` / raise ``NotFound`` (never 403), so the
    API does not leak whether another tenant's id exists.
    """

    def __init__(self, session: AsyncSession, tenant_id: str, model: type[M]) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.model = model

    def _base(self) -> Any:
        return select(self.model).where(self.model.tenant_id == self.tenant_id)

    async def get(self, id_: str) -> M | None:
        stmt = self._base().where(self.model.id == id_)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def require(self, id_: str) -> M:
        obj = await self.get(id_)
        if obj is None:
            raise NotFound(f"{self.model.__name__} {id_} not found")
        return obj

    async def list(
        self, *where: ColumnElement[bool], order_by: Any = None, limit: int | None = None, offset: int = 0
    ) -> list[M]:
        stmt = self._base()
        for clause in where:
            stmt = stmt.where(clause)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def add(self, obj: M) -> M:
        current = getattr(obj, "tenant_id", None)
        if current is None:
            obj.tenant_id = self.tenant_id
        elif current != self.tenant_id:
            raise TenantViolation(f"{type(obj).__name__} belongs to another tenant")
        self.session.add(obj)
        return obj

    async def delete(self, obj: M) -> None:
        if getattr(obj, "tenant_id", None) != self.tenant_id:
            raise TenantViolation(f"cannot delete {type(obj).__name__} of another tenant")
        await self.session.delete(obj)
