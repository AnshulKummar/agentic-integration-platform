"""Password hashing (argon2id via argon2-cffi) and the password policy.

Hashing is CPU/memory heavy, so it runs in a worker thread to keep the event
loop responsive.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.platform.errors import ValidationFailed

log = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 12
#: Upper bound keeps hashing cost bounded for hostile input.
MAX_PASSWORD_LENGTH = 256

#: Module-level so tests can swap parameters (e.g. to exercise rehash-on-login).
_hasher = PasswordHasher()
_dummy_hash: tuple[PasswordHasher, str] | None = None


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    #: Set when the hash was made with outdated parameters and should be replaced.
    new_hash: str | None = None


def check_password_policy(password: str, *, email: str | None = None) -> None:
    """Raise ValidationFailed if ``password`` is unacceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationFailed(f"password must be at least {MIN_PASSWORD_LENGTH} characters", code="weak_password")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationFailed(f"password must be at most {MAX_PASSWORD_LENGTH} characters", code="weak_password")
    if not password.strip():
        raise ValidationFailed("password must not be blank", code="weak_password")
    if email is not None and password.strip().lower() == email.strip().lower():
        raise ValidationFailed("password must not equal the email address", code="weak_password")


def _hash_sync(password: str) -> str:
    return _hasher.hash(password)


def _verify_sync(stored_hash: str, password: str) -> VerifyResult:
    hasher = _hasher
    try:
        hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        return VerifyResult(ok=False)
    except InvalidHashError:
        # Corrupt/unknown hash format: a data problem worth an alert, but never a login.
        log.error("stored password hash is not a valid argon2 hash")
        return VerifyResult(ok=False)
    except VerificationError:
        return VerifyResult(ok=False)
    new_hash = hasher.hash(password) if hasher.check_needs_rehash(stored_hash) else None
    return VerifyResult(ok=True, new_hash=new_hash)


def _dummy_verify_sync(password: str) -> None:
    global _dummy_hash
    hasher = _hasher
    if _dummy_hash is None or _dummy_hash[0] is not hasher:
        _dummy_hash = (hasher, hasher.hash(secrets.token_urlsafe(16)))
    try:
        hasher.verify(_dummy_hash[1], password)
    except VerificationError:
        return  # expected: this exists only to spend the same time as a real verify


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hash_sync, password)


async def verify_password(stored_hash: str, password: str) -> VerifyResult:
    return await asyncio.to_thread(_verify_sync, stored_hash, password)


async def dummy_verify(password: str) -> None:
    """Constant-time stand-in for unknown emails (prevents user enumeration by timing)."""
    await asyncio.to_thread(_dummy_verify_sync, password)
