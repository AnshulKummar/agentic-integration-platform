"""Who is calling: the authenticated principal and role checks.

OWNED BY THE AUTH AREA: ``current_principal`` is implemented by app.platform.auth
(bearer JWT -> Principal). Other areas only import ``Principal``, ``Role``,
``current_principal`` and ``require_roles``; in their tests they override
``current_principal`` via ``app.dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Depends, Request

from app.platform.errors import Forbidden, Unauthorized


class Role(StrEnum):
    ADMIN = "admin"
    PM = "pm"
    ENGINEER = "engineer"
    VIEWER = "viewer"


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    email: str
    roles: frozenset[Role]

    def has(self, *roles: Role) -> bool:
        return Role.ADMIN in self.roles or any(r in self.roles for r in roles)


async def current_principal(request: Request) -> Principal:
    """Resolve the caller from the ``Authorization: Bearer`` header."""
    raise Unauthorized("authentication is not configured")


def require_roles(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    """Dependency: caller must hold one of ``roles`` (admin always passes)."""

    async def _check(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.has(*roles):
            raise Forbidden(f"requires role: {' or '.join(r.value for r in roles)}")
        return principal

    return _check
