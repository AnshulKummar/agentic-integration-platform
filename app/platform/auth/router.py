"""HTTP routes for the auth area. OWNED BY: auth area (A).

/v1/auth/*   identity: signup, login, refresh, logout, me, password change
/v1/members  org membership administration (admin only, tenant-scoped)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.auth import service
from app.platform.auth.ratelimit import SlidingWindowLimiter
from app.platform.auth.rls import allow_cross_tenant
from app.platform.auth.schemas import (
    LoginRequest,
    LogoutRequest,
    MemberCreate,
    MemberUpdate,
    MemberView,
    PasswordChangeRequest,
    RefreshRequest,
    SessionView,
    SignupRequest,
    TokenResponse,
)
from app.platform.config import PlatformSettings
from app.platform.db import Database
from app.platform.deps import get_database, get_session, get_settings, get_tenant_session
from app.platform.principal import AuthenticatedCaller, Principal, Role, authenticated_caller, require_roles

router = APIRouter(tags=["auth"])

_LIMITER_STATE = "auth_login_limiter"


def get_login_limiter(request: Request) -> SlidingWindowLimiter:
    """One limiter per app instance (see ratelimit.py for the multi-instance caveat)."""
    limiter: SlidingWindowLimiter | None = getattr(request.app.state, _LIMITER_STATE, None)
    if limiter is None:
        settings: PlatformSettings = request.app.state.settings
        limiter = SlidingWindowLimiter(settings.login_rate_limit_per_minute, window_s=60.0)
        setattr(request.app.state, _LIMITER_STATE, limiter)
    return limiter


def client_ip(request: Request) -> str:
    # Direct peer address. Behind a proxy, configure uvicorn --proxy-headers with
    # trusted --forwarded-allow-ips so this reflects the real client.
    return request.client.host if request.client else "unknown"


async def get_identity_session(db: Annotated[Database, Depends(get_database)]) -> AsyncIterator[AsyncSession]:
    """Unbound session explicitly allowed to span tenants (identity flows only)."""
    async with db.session() as session:
        allow_cross_tenant(session)
        yield session


IdentitySession = Annotated[AsyncSession, Depends(get_identity_session)]
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
Settings = Annotated[PlatformSettings, Depends(get_settings)]
Limiter = Annotated[SlidingWindowLimiter, Depends(get_login_limiter)]
Caller = Annotated[AuthenticatedCaller, Depends(authenticated_caller)]
Admin = Annotated[Principal, Depends(require_roles(Role.ADMIN))]


# --- identity --------------------------------------------------------------------


@router.post("/v1/auth/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    request: Request,
    session: IdentitySession,
    settings: Settings,
    limiter: Limiter,
) -> TokenResponse:
    return await service.signup(session, settings, body, limiter=limiter, client_ip=client_ip(request))


@router.post("/v1/auth/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: IdentitySession,
    settings: Settings,
    limiter: Limiter,
) -> TokenResponse:
    return await service.login(
        session,
        settings,
        email=body.email,
        password=body.password,
        tenant_slug=body.tenant_slug,
        limiter=limiter,
        client_ip=client_ip(request),
    )


@router.post("/v1/auth/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    session: IdentitySession,
    settings: Settings,
) -> TokenResponse:
    return await service.refresh(session, settings, body.refresh_token)


@router.post("/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: LogoutRequest, session: IdentitySession) -> Response:
    await service.logout(session, body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/v1/auth/me", response_model=SessionView)
async def me(
    caller: Caller,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SessionView:
    return await service.me(session, caller)


@router.post("/v1/auth/password", response_model=TokenResponse)
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    caller: Caller,
    session: IdentitySession,
    settings: Settings,
    limiter: Limiter,
) -> TokenResponse:
    return await service.change_password(
        session,
        settings,
        caller,
        current_password=body.current_password,
        new_password=body.new_password,
        limiter=limiter,
        client_ip=client_ip(request),
    )


# --- org membership administration -----------------------------------------------

@router.get("/v1/members", response_model=list[MemberView])
async def list_members(
    principal: Admin, session: TenantSession
) -> list[MemberView]:
    return await service.list_members(session, principal)


@router.post("/v1/members", response_model=MemberView, status_code=status.HTTP_201_CREATED)
async def add_member(
    body: MemberCreate, principal: Admin, session: TenantSession
) -> MemberView:
    return await service.add_member(session, principal, body)


@router.patch("/v1/members/{user_id}", response_model=MemberView)
async def update_member(
    user_id: str,
    body: MemberUpdate,
    principal: Admin,
    session: TenantSession,
) -> MemberView:
    return await service.update_member_roles(session, principal, user_id, body.roles)


@router.delete("/v1/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: str, principal: Admin, session: TenantSession
) -> Response:
    await service.remove_member(session, principal, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
