"""Access tokens (JWT, HS256) and opaque refresh tokens.

This module must not import ``app.platform.principal`` (principal imports it).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.platform.config import PlatformSettings
from app.platform.errors import Unauthorized

log = logging.getLogger(__name__)

ALGORITHM = "HS256"
AUDIENCE = "pmaip-api"
#: Claim set on tokens of users who must change their password first.
PASSWORD_CHANGE_CLAIM = "pcr"
_REQUIRED = ["exp", "iat", "iss", "aud", "sub", "jti"]


@dataclass(frozen=True)
class AccessClaims:
    user_id: str
    tenant_id: str
    email: str
    roles: tuple[str, ...]
    password_change_required: bool
    jti: str
    expires_at: datetime


@dataclass(frozen=True)
class IssuedAccessToken:
    token: str
    expires_in: int


def issue_access_token(
    settings: PlatformSettings,
    *,
    user_id: str,
    tenant_id: str,
    email: str,
    roles: Iterable[str],
    password_change_required: bool = False,
    now: datetime | None = None,
) -> IssuedAccessToken:
    issued = now or datetime.now(UTC)
    ttl = settings.access_token_ttl_s
    payload: dict[str, object] = {
        "iss": settings.jwt_issuer,
        "aud": AUDIENCE,
        "sub": user_id,
        "tid": tenant_id,
        "email": email,
        "roles": sorted(set(roles)),
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(seconds=ttl)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if password_change_required:
        payload[PASSWORD_CHANGE_CLAIM] = True
    return IssuedAccessToken(token=jwt.encode(payload, settings.jwt_key(), algorithm=ALGORITHM), expires_in=ttl)


def decode_access_token(settings: PlatformSettings, token: str) -> AccessClaims:
    """Verify signature, algorithm, issuer, audience and expiry. Any failure -> Unauthorized."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_key(),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=settings.jwt_issuer,
            options={"require": _REQUIRED},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("access token expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        log.info("rejected access token: %s", type(exc).__name__)
        raise Unauthorized("invalid access token", code="invalid_token") from exc

    sub, tid, email, roles = payload.get("sub"), payload.get("tid"), payload.get("email"), payload.get("roles")
    if not (isinstance(sub, str) and sub and isinstance(tid, str) and tid and isinstance(email, str)):
        raise Unauthorized("invalid access token", code="invalid_token")
    if not (isinstance(roles, list) and roles and all(isinstance(r, str) for r in roles)):
        raise Unauthorized("invalid access token", code="invalid_token")
    return AccessClaims(
        user_id=sub,
        tenant_id=tid,
        email=email,
        roles=tuple(roles),
        password_change_required=payload.get(PASSWORD_CHANGE_CLAIM) is True,
        jti=str(payload["jti"]),
        expires_at=datetime.fromtimestamp(int(payload["exp"]), UTC),
    )


def new_refresh_token() -> str:
    """256 bits of randomness, urlsafe. Returned to the client once; only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
