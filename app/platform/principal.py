"""Who is calling: the authenticated principal and role checks.

OWNED BY THE AUTH AREA: ``current_principal`` is implemented by app.platform.auth
(bearer JWT -> Principal). Other areas only import ``Principal``, ``Role``,
``current_principal`` and ``require_roles``; in their tests they override
``current_principal`` via ``app.dependency_overrides``.

Access tokens are verified statelessly (signature, iss, aud, exp). Role or
membership changes therefore take effect when the caller next refreshes, i.e.
within ``access_token_ttl_s``; refresh always re-reads memberships.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Request

from app.platform.auth.tokens import decode_access_token
from app.platform.config import PlatformSettings
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


@dataclass(frozen=True)
class AuthenticatedCaller:
    """A verified token whose holder may still owe a password change."""

    principal: Principal
    password_change_required: bool


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if not header:
        raise Unauthorized("missing bearer token")
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("malformed Authorization header")
    return token


async def authenticated_caller(request: Request) -> AuthenticatedCaller:
    """Verify the bearer token. Used directly only by endpoints that a user who
    must change their password may still call (``/v1/auth/me``, ``/v1/auth/password``)."""
    settings: PlatformSettings = request.app.state.settings
    claims = decode_access_token(settings, _bearer_token(request))
    try:
        roles = frozenset(Role(r) for r in claims.roles)
    except ValueError as exc:
        raise Unauthorized("invalid access token", code="invalid_token") from exc
    principal = Principal(user_id=claims.user_id, tenant_id=claims.tenant_id, email=claims.email, roles=roles)
    return AuthenticatedCaller(principal=principal, password_change_required=claims.password_change_required)


async def current_principal(request: Request) -> Principal:
    """Resolve the caller from the ``Authorization: Bearer`` header.

    Invalid/expired/tampered tokens -> 401. A user who must change their
    password (admin-provisioned account) gets 403 until they do.
    """
    caller = await authenticated_caller(request)
    if caller.password_change_required:
        raise Forbidden("password change required", code="password_change_required")
    return caller.principal


def require_roles(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    """Dependency: caller must hold one of ``roles`` (admin always passes)."""

    async def _check(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if not principal.has(*roles):
            raise Forbidden(f"requires role: {' or '.join(r.value for r in roles)}")
        return principal

    return _check
