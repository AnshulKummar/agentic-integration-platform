"""Identity business logic: signup, login, refresh rotation, logout, password
change and org membership administration.

Session conventions:
* Identity flows (signup/login/refresh/logout/password) run on an UNBOUND session
  marked ``allow_cross_tenant`` because the tenant is not known yet, or the flow
  legitimately spans tenants (a user's memberships, revoking all their sessions).
* Membership administration runs on the caller's tenant-bound session.

Every state change is audited (``record_audit``) in the same transaction and
counted in ``AUTH_EVENTS``. Passwords and tokens never reach logs or audit rows.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import CursorResult, distinct, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.audit import record_audit
from app.platform.auth.models import Membership, RefreshToken, User
from app.platform.auth.passwords import check_password_policy, dummy_verify, hash_password, verify_password
from app.platform.auth.ratelimit import SlidingWindowLimiter
from app.platform.auth.schemas import (
    MemberCreate,
    MemberView,
    SessionView,
    SignupRequest,
    TenantView,
    TokenResponse,
    UserView,
)
from app.platform.auth.tokens import hash_refresh_token, issue_access_token, new_refresh_token
from app.platform.config import PlatformSettings
from app.platform.core_models import Tenant
from app.platform.db import new_id, utcnow
from app.platform.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PlatformError,
    RateLimited,
    Unauthorized,
    ValidationFailed,
)
from app.platform.observability.metrics import AUTH_EVENTS
from app.platform.principal import AuthenticatedCaller, Principal, Role

log = logging.getLogger(__name__)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,62})[a-z0-9]$")
_BAD_CREDENTIALS = "invalid email or password"


class TenantSelectionRequired(PlatformError):
    status = 400
    code = "tenant_required"


def _count(event: str, outcome: str) -> None:
    AUTH_EVENTS.labels(event=event, outcome=outcome).inc()


def normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if not _EMAIL.match(email):
        raise ValidationFailed("invalid email address", code="invalid_email")
    return email


def normalize_slug(raw: str) -> str:
    slug = raw.strip().lower()
    if not _SLUG.match(slug) or "--" in slug:
        raise ValidationFailed(
            "org_slug must be 3-64 chars of a-z, 0-9 and single hyphens, starting/ending alphanumeric",
            code="invalid_slug",
        )
    return slug


def _aware(dt: datetime) -> datetime:
    # SQLite hands back naive datetimes; everything we store is UTC.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _sorted_roles(roles: set[str] | list[str]) -> list[Role]:
    return sorted({Role(r) for r in roles}, key=lambda r: list(Role).index(r))


def _rate_limit(limiter: SlidingWindowLimiter, event: str, email: str, client_ip: str) -> None:
    try:
        limiter.hit(f"{email}|{client_ip}")
    except RateLimited:
        _count(event, "rate_limited")
        log.warning("auth rate limit hit: event=%s ip=%s", event, client_ip)
        raise


# --- shared helpers -------------------------------------------------------------


async def _user_by_email(session: AsyncSession, email: str) -> User | None:
    return (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def _roles_in_tenant(session: AsyncSession, user_id: str, tenant_id: str) -> set[str]:
    stmt = select(Membership.role).where(Membership.user_id == user_id, Membership.tenant_id == tenant_id)
    return set((await session.execute(stmt)).scalars().all())


async def _require_tenant(session: AsyncSession, tenant_id: str) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise Unauthorized("organization no longer exists")
    return tenant


async def _issue(
    session: AsyncSession,
    settings: PlatformSettings,
    *,
    user: User,
    tenant: Tenant,
    roles: set[str],
    family_id: str | None = None,
    token_id: str | None = None,
) -> TokenResponse:
    """Stage a new refresh token row and mint the access token. Caller commits."""
    refresh = new_refresh_token()
    session.add(
        RefreshToken(
            id=token_id or new_id(),
            tenant_id=tenant.id,
            user_id=user.id,
            token_hash=hash_refresh_token(refresh),
            family_id=family_id or new_id(),
            expires_at=utcnow() + timedelta(seconds=settings.refresh_token_ttl_s),
        )
    )
    access = issue_access_token(
        settings,
        user_id=user.id,
        tenant_id=tenant.id,
        email=user.email,
        roles=roles,
        password_change_required=user.must_change_password,
    )
    return TokenResponse(
        user=UserView(id=user.id, email=user.email, display_name=user.display_name),
        tenant=TenantView(id=tenant.id, slug=tenant.slug, name=tenant.name),
        roles=_sorted_roles(roles),
        password_change_required=user.must_change_password,
        access_token=access.token,
        expires_in=access.expires_in,
        refresh_token=refresh,
        refresh_expires_in=settings.refresh_token_ttl_s,
    )


async def _revoke_family(session: AsyncSession, family_id: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
        .execution_options(synchronize_session=False)
    )


async def _revoke_user_tokens(session: AsyncSession, user_id: str, tenant_id: str | None = None) -> None:
    stmt = update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
    if tenant_id is not None:
        stmt = stmt.where(RefreshToken.tenant_id == tenant_id)
    await session.execute(stmt.values(revoked_at=utcnow()).execution_options(synchronize_session=False))


# --- identity flows (unbound, cross-tenant session) ------------------------------


async def signup(
    session: AsyncSession,
    settings: PlatformSettings,
    body: SignupRequest,
    *,
    limiter: SlidingWindowLimiter,
    client_ip: str,
) -> TokenResponse:
    """Self-serve org creation: new Tenant + the caller as its admin.

    An existing account may create another org by proving its password (that is
    how one identity ends up in several tenants); a wrong password is a 409 and
    counts toward the same rate limit as login.
    """
    email = normalize_email(body.email)
    slug = normalize_slug(body.org_slug)
    _rate_limit(limiter, "signup", email, client_ip)

    user = await _user_by_email(session, email)
    if user is not None:
        result = await verify_password(user.password_hash, body.password)
        if not result.ok or not user.is_active:
            _count("signup", "failure")
            raise Conflict(
                "email already registered; sign up with your existing password to create another organization",
                code="email_taken",
            )
        if result.new_hash is not None:
            user.password_hash = result.new_hash
    else:
        check_password_policy(body.password, email=email)

    if (await session.execute(select(Tenant.id).where(Tenant.slug == slug))).first() is not None:
        _count("signup", "failure")
        raise Conflict("organization slug is taken", code="slug_taken")

    existing_user = user is not None
    tenant = Tenant(id=new_id(), name=body.org_name.strip(), slug=slug)
    session.add(tenant)
    if user is None:
        user = User(
            id=new_id(),
            email=email,
            password_hash=await hash_password(body.password),
            display_name=body.display_name.strip(),
        )
        session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        _count("signup", "failure")
        raise Conflict("organization slug or email already exists", code="signup_conflict") from exc

    session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=Role.ADMIN.value))
    record_audit(
        session,
        tenant_id=tenant.id,
        actor_id=user.id,
        action="auth.signup",
        target_type="tenant",
        target_id=tenant.id,
        detail={"org_slug": slug, "existing_user": existing_user, "client_ip": client_ip},
    )
    response = await _issue(session, settings, user=user, tenant=tenant, roles={Role.ADMIN.value})
    await session.commit()
    _count("signup", "success")
    return response


async def login(
    session: AsyncSession,
    settings: PlatformSettings,
    *,
    email: str,
    password: str,
    tenant_slug: str | None,
    limiter: SlidingWindowLimiter,
    client_ip: str,
) -> TokenResponse:
    email = email.strip().lower()
    _rate_limit(limiter, "login", email, client_ip)

    user = await _user_by_email(session, email)
    if user is None:
        await dummy_verify(password)
        _count("login", "failure")
        raise Unauthorized(_BAD_CREDENTIALS, code="invalid_credentials")

    memberships = (await session.execute(select(Membership).where(Membership.user_id == user.id))).scalars().all()
    roles_by_tenant: dict[str, set[str]] = defaultdict(set)
    for m in memberships:
        roles_by_tenant[m.tenant_id].add(m.role)

    result = await verify_password(user.password_hash, password)
    if not result.ok or not user.is_active:
        reason = "bad_password" if not result.ok else "inactive"
        for tenant_id in roles_by_tenant:  # let every org the user belongs to see the attempt
            record_audit(
                session,
                tenant_id=tenant_id,
                actor_id=user.id,
                action="auth.login_failed",
                target_type="user",
                target_id=user.id,
                detail={"reason": reason, "client_ip": client_ip},
            )
        await session.commit()
        _count("login", "failure")
        raise Unauthorized(_BAD_CREDENTIALS, code="invalid_credentials")
    if result.new_hash is not None:
        user.password_hash = result.new_hash
        log.info("rehashed password for user %s with current argon2 parameters", user.id)

    tenants = {
        t.id: t
        for t in (await session.execute(select(Tenant).where(Tenant.id.in_(roles_by_tenant)))).scalars().all()
    }
    if tenant_slug is not None:
        wanted = tenant_slug.strip().lower()
        chosen = next((t for t in tenants.values() if t.slug == wanted), None)
        if chosen is None:
            await session.commit()  # keep a possible rehash
            _count("login", "failure")
            raise Forbidden("you are not a member of that organization", code="not_a_member")
    elif not tenants:
        await session.commit()
        _count("login", "failure")
        raise Forbidden("account has no organization membership", code="no_membership")
    elif len(tenants) > 1:
        await session.commit()
        _count("login", "tenant_required")
        slugs = ", ".join(sorted(t.slug for t in tenants.values()))
        raise TenantSelectionRequired(f"account belongs to several organizations; pass tenant_slug, one of: {slugs}")
    else:
        chosen = next(iter(tenants.values()))

    response = await _issue(session, settings, user=user, tenant=chosen, roles=roles_by_tenant[chosen.id])
    record_audit(
        session,
        tenant_id=chosen.id,
        actor_id=user.id,
        action="auth.login",
        target_type="user",
        target_id=user.id,
        detail={"client_ip": client_ip},
    )
    await session.commit()
    _count("login", "success")
    return response


async def refresh(session: AsyncSession, settings: PlatformSettings, token: str) -> TokenResponse:
    """Rotate a refresh token. Presenting an already-rotated token revokes the family."""
    stored = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token)))
    ).scalar_one_or_none()
    if stored is None:
        _count("refresh", "failure")
        raise Unauthorized("invalid refresh token", code="invalid_refresh_token")
    if stored.revoked_at is not None:
        if stored.replaced_by_id is not None:
            await _reuse_detected(session, stored)
        _count("refresh", "failure")
        raise Unauthorized("refresh token revoked", code="invalid_refresh_token")
    if _aware(stored.expires_at) <= utcnow():
        _count("refresh", "failure")
        raise Unauthorized("refresh token expired", code="refresh_token_expired")

    user = await session.get(User, stored.user_id)
    roles = await _roles_in_tenant(session, stored.user_id, stored.tenant_id)
    if user is None or not user.is_active or not roles:
        await _revoke_family(session, stored.family_id)
        await session.commit()
        _count("refresh", "failure")
        raise Unauthorized("session is no longer valid", code="invalid_refresh_token")
    tenant = await _require_tenant(session, stored.tenant_id)

    # Atomic single-use claim: only one concurrent caller can rotate this token.
    successor_id = new_id()
    claim = cast(
        CursorResult[tuple[()]],
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == stored.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow(), replaced_by_id=successor_id)
            .execution_options(synchronize_session=False)
        ),
    )
    if claim.rowcount != 1:
        await _reuse_detected(session, stored)
        _count("refresh", "failure")
        raise Unauthorized("refresh token revoked", code="invalid_refresh_token")

    response = await _issue(
        session, settings, user=user, tenant=tenant, roles=roles, family_id=stored.family_id, token_id=successor_id
    )
    record_audit(
        session,
        tenant_id=tenant.id,
        actor_id=user.id,
        action="auth.refresh",
        target_type="session",
        target_id=stored.family_id,
    )
    await session.commit()
    _count("refresh", "success")
    return response


async def _reuse_detected(session: AsyncSession, stored: RefreshToken) -> None:
    await _revoke_family(session, stored.family_id)
    record_audit(
        session,
        tenant_id=stored.tenant_id,
        actor_id=stored.user_id,
        action="auth.refresh_reuse_detected",
        target_type="session",
        target_id=stored.family_id,
    )
    await session.commit()
    _count("refresh", "reuse_detected")
    log.warning("refresh token reuse detected; revoked session family %s", stored.family_id)


async def logout(session: AsyncSession, token: str) -> None:
    """Revoke the token's whole family. Idempotent; unknown tokens are ignored (no oracle)."""
    stored = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token)))
    ).scalar_one_or_none()
    if stored is None:
        _count("logout", "unknown_token")
        return
    await _revoke_family(session, stored.family_id)
    record_audit(
        session,
        tenant_id=stored.tenant_id,
        actor_id=stored.user_id,
        action="auth.logout",
        target_type="session",
        target_id=stored.family_id,
    )
    await session.commit()
    _count("logout", "success")


async def me(session: AsyncSession, caller: AuthenticatedCaller) -> SessionView:
    principal = caller.principal
    user = await session.get(User, principal.user_id)
    if user is None or not user.is_active:
        raise Unauthorized("account is not active")
    tenant = await _require_tenant(session, principal.tenant_id)
    return SessionView(
        user=UserView(id=user.id, email=user.email, display_name=user.display_name),
        tenant=TenantView(id=tenant.id, slug=tenant.slug, name=tenant.name),
        roles=_sorted_roles([r.value for r in principal.roles]),
        password_change_required=user.must_change_password,
    )


async def change_password(
    session: AsyncSession,
    settings: PlatformSettings,
    caller: AuthenticatedCaller,
    *,
    current_password: str,
    new_password: str,
    limiter: SlidingWindowLimiter,
    client_ip: str,
) -> TokenResponse:
    """Change the caller's password, revoke ALL their sessions (every tenant) and
    return a fresh token pair for the current tenant."""
    principal = caller.principal
    user = await session.get(User, principal.user_id)
    if user is None or not user.is_active:
        raise Unauthorized("account is not active")
    _rate_limit(limiter, "password_change", user.email, client_ip)

    if not (await verify_password(user.password_hash, current_password)).ok:
        record_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_id=user.id,
            action="auth.password_change_failed",
            target_type="user",
            target_id=user.id,
            detail={"client_ip": client_ip},
        )
        await session.commit()
        _count("password_change", "failure")
        raise Forbidden("current password is incorrect", code="invalid_current_password")
    check_password_policy(new_password, email=user.email)
    if new_password == current_password:
        raise ValidationFailed("new password must differ from the current one", code="weak_password")

    roles = await _roles_in_tenant(session, user.id, principal.tenant_id)
    if not roles:
        raise Unauthorized("you are no longer a member of this organization")
    tenant = await _require_tenant(session, principal.tenant_id)

    user.password_hash = await hash_password(new_password)
    user.must_change_password = False
    await _revoke_user_tokens(session, user.id)
    response = await _issue(session, settings, user=user, tenant=tenant, roles=roles)
    record_audit(
        session,
        tenant_id=tenant.id,
        actor_id=user.id,
        action="auth.password_changed",
        target_type="user",
        target_id=user.id,
        detail={"client_ip": client_ip},
    )
    await session.commit()
    _count("password_change", "success")
    return response


# --- membership administration (tenant-bound session) ----------------------------


async def _lock_tenant(session: AsyncSession, tenant_id: str) -> None:
    """Serialise membership changes per tenant (FOR UPDATE; a no-op on SQLite,
    whose single writer already serialises) so two admins cannot demote each
    other concurrently and leave the org without an admin."""
    await session.execute(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())


async def _admin_count(session: AsyncSession, tenant_id: str) -> int:
    stmt = select(func.count(distinct(Membership.user_id))).where(
        Membership.tenant_id == tenant_id, Membership.role == Role.ADMIN.value
    )
    return int((await session.execute(stmt)).scalar_one())


async def _member_view(session: AsyncSession, tenant_id: str, user_id: str) -> MemberView:
    user = await session.get(User, user_id)
    roles = await _roles_in_tenant(session, user_id, tenant_id)
    if user is None or not roles:
        raise NotFound(f"member {user_id} not found")
    return MemberView(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        roles=_sorted_roles(roles),
        is_active=user.is_active,
    )


async def list_members(session: AsyncSession, principal: Principal) -> list[MemberView]:
    stmt = (
        select(Membership.role, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.tenant_id == principal.tenant_id)
        .order_by(User.email)
    )
    users: dict[str, User] = {}
    roles: dict[str, set[str]] = defaultdict(set)
    for role, user in (await session.execute(stmt)).all():
        users[user.id] = user
        roles[user.id].add(role)
    return [
        MemberView(
            user_id=u.id,
            email=u.email,
            display_name=u.display_name,
            roles=_sorted_roles(roles[u.id]),
            is_active=u.is_active,
        )
        for u in users.values()
    ]


async def add_member(session: AsyncSession, principal: Principal, body: MemberCreate) -> MemberView:
    email = normalize_email(body.email)
    check_password_policy(body.initial_password, email=email)
    wanted = {r.value for r in body.roles}

    user = await _user_by_email(session, email)
    new_user = user is None
    if user is None:
        user = User(
            id=new_id(),
            email=email,
            password_hash=await hash_password(body.initial_password),
            display_name=body.display_name.strip(),
            must_change_password=True,
        )
        session.add(user)
        # Insert the user before its memberships: there is no ORM relationship to
        # order them, and PostgreSQL (and SQLite with FKs on) enforce memberships.user_id.
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise Conflict("a user with this email already exists", code="email_taken") from exc
    elif await _roles_in_tenant(session, user.id, principal.tenant_id):
        raise Conflict("user is already a member; use PATCH to change roles", code="already_member")

    for role in sorted(wanted):
        session.add(Membership(tenant_id=principal.tenant_id, user_id=user.id, role=role))
    record_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        action="member.added",
        target_type="user",
        target_id=user.id,
        detail={"roles": sorted(wanted), "new_account": new_user},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("member already exists", code="already_member") from exc
    _count("member_add", "success")
    return await _member_view(session, principal.tenant_id, user.id)


async def update_member_roles(
    session: AsyncSession, principal: Principal, user_id: str, roles: list[Role]
) -> MemberView:
    await _lock_tenant(session, principal.tenant_id)
    rows = (
        (
            await session.execute(
                select(Membership).where(Membership.user_id == user_id, Membership.tenant_id == principal.tenant_id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise NotFound(f"member {user_id} not found")
    before = {m.role for m in rows}
    after = {r.value for r in roles}
    if Role.ADMIN.value in before and Role.ADMIN.value not in after:
        if await _admin_count(session, principal.tenant_id) <= 1:
            raise Conflict("cannot remove the last admin of the organization", code="last_admin")
    for m in rows:
        if m.role not in after:
            await session.delete(m)
    for role in sorted(after - before):
        session.add(Membership(tenant_id=principal.tenant_id, user_id=user_id, role=role))
    record_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        action="member.roles_changed",
        target_type="user",
        target_id=user_id,
        detail={"before": sorted(before), "after": sorted(after)},
    )
    await session.commit()
    _count("member_update", "success")
    return await _member_view(session, principal.tenant_id, user_id)


async def remove_member(session: AsyncSession, principal: Principal, user_id: str) -> None:
    await _lock_tenant(session, principal.tenant_id)
    rows = (
        (
            await session.execute(
                select(Membership).where(Membership.user_id == user_id, Membership.tenant_id == principal.tenant_id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise NotFound(f"member {user_id} not found")
    roles = {m.role for m in rows}
    if Role.ADMIN.value in roles and await _admin_count(session, principal.tenant_id) <= 1:
        raise Conflict("cannot remove the last admin of the organization", code="last_admin")
    for m in rows:
        await session.delete(m)
    # Their sessions in THIS org end now; sessions in other orgs are untouched.
    await _revoke_user_tokens(session, user_id, tenant_id=principal.tenant_id)
    record_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        action="member.removed",
        target_type="user",
        target_id=user_id,
        detail={"roles": sorted(roles)},
    )
    await session.commit()
    _count("member_remove", "success")
