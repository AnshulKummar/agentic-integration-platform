"""Unit tests: password policy/hashing and access-token verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from argon2 import PasswordHasher

from app.platform.auth import passwords
from app.platform.auth.tokens import (
    AUDIENCE,
    decode_access_token,
    hash_refresh_token,
    issue_access_token,
    new_refresh_token,
)
from app.platform.config import PlatformSettings
from app.platform.errors import Unauthorized, ValidationFailed


@pytest.mark.parametrize(
    "candidate",
    ["short", "elevenchars", " " * 20, "x" * 257, "Admin@Example.com"],
)
def test_password_policy_rejects(candidate: str) -> None:
    with pytest.raises(ValidationFailed):
        passwords.check_password_policy(candidate, email="admin@example.com")


def test_password_policy_accepts_twelve_chars() -> None:
    passwords.check_password_policy("twelve-chars", email="a@b.co")


async def test_hash_and_verify_roundtrip() -> None:
    stored = await passwords.hash_password("a long enough password")
    assert stored.startswith("$argon2id$")
    assert "a long enough password" not in stored
    assert (await passwords.verify_password(stored, "a long enough password")).ok
    assert not (await passwords.verify_password(stored, "wrong password!!")).ok


async def test_corrupt_hash_never_verifies() -> None:
    assert not (await passwords.verify_password("not-a-hash", "whatever password")).ok


async def test_rehash_when_parameters_change(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = await passwords.hash_password("a long enough password")
    assert (await passwords.verify_password(stored, "a long enough password")).new_hash is None
    monkeypatch.setattr(passwords, "_hasher", PasswordHasher(time_cost=2, memory_cost=2048, parallelism=1))
    result = await passwords.verify_password(stored, "a long enough password")
    assert result.ok and result.new_hash is not None
    assert "m=2048,t=2" in result.new_hash


async def test_dummy_verify_completes() -> None:
    await passwords.dummy_verify("anything at all")


def _token(settings: PlatformSettings, **overrides: object) -> str:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "iss": settings.jwt_issuer,
        "aud": AUDIENCE,
        "sub": "user-1",
        "tid": "tenant-1",
        "email": "u@example.com",
        "roles": ["pm"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "jti": "j1",
    }
    payload.update(overrides)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, settings.jwt_key(), algorithm="HS256")


def test_issue_and_decode(settings: PlatformSettings) -> None:
    issued = issue_access_token(settings, user_id="u1", tenant_id="t1", email="e@x.io", roles=["pm", "admin"])
    assert issued.expires_in == settings.access_token_ttl_s
    claims = decode_access_token(settings, issued.token)
    assert (claims.user_id, claims.tenant_id, claims.roles) == ("u1", "t1", ("admin", "pm"))
    assert not claims.password_change_required
    raw = jwt.decode(issued.token, options={"verify_signature": False})
    assert raw["aud"] == "pmaip-api" and raw["iss"] == settings.jwt_issuer and raw["jti"]
    assert raw["exp"] - raw["iat"] == settings.access_token_ttl_s


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-else"},
        {"iss": "evil-issuer"},
        {"exp": int((datetime.now(UTC) - timedelta(seconds=5)).timestamp())},
        {"exp": None},
        {"jti": None},
        {"tid": None},
        {"roles": []},
        {"roles": "admin"},
    ],
)
def test_decode_rejects_bad_claims(settings: PlatformSettings, overrides: dict[str, object]) -> None:
    with pytest.raises(Unauthorized):
        decode_access_token(settings, _token(settings, **overrides))


def test_decode_rejects_wrong_key_tamper_and_alg_none(settings: PlatformSettings) -> None:
    good = _token(settings)
    forged = jwt.encode(jwt.decode(good, options={"verify_signature": False}), "another-key-" + "y" * 32)
    unsigned = jwt.encode(jwt.decode(good, options={"verify_signature": False}), None, algorithm="none")
    header, payload, sig = good.split(".")
    tampered = ".".join([header, payload[:-2] + ("A" if payload[-2] != "A" else "B") + payload[-1], sig])
    for bad in (forged, unsigned, tampered, "not.a.jwt", ""):
        with pytest.raises(Unauthorized):
            decode_access_token(settings, bad)


def test_refresh_tokens_are_random_and_hashed() -> None:
    a, b = new_refresh_token(), new_refresh_token()
    assert a != b and len(a) >= 43
    assert hash_refresh_token(a) == hash_refresh_token(a)
    assert len(hash_refresh_token(a)) == 64 and a not in hash_refresh_token(a)
