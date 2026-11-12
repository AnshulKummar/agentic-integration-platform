"""End-to-end identity flows over HTTP: signup, login, refresh rotation, reuse
detection, logout, /me, password change, bearer validation and role checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Annotated, Any

import httpx
import jwt
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import select, update

from app.platform.auth.models import Membership, RefreshToken, User
from app.platform.config import PlatformSettings
from app.platform.core_models import AuditEvent
from app.platform.db import Database, utcnow
from app.platform.observability.metrics import REGISTRY
from app.platform.principal import Principal, Role, current_principal, require_roles

Json = dict[str, Any]
Signup = Callable[..., Awaitable[Json]]
Login = Callable[..., Awaitable[httpx.Response]]
Header = Callable[[str], dict[str, str]]


def _metric(event: str, outcome: str) -> float:
    return REGISTRY.get_sample_value("pmaip_auth_events_total", {"event": event, "outcome": outcome}) or 0.0


async def test_signup_creates_org_admin_and_tokens(
    client: httpx.AsyncClient, signup: Signup, db: Database, settings: PlatformSettings, password: str
) -> None:
    body = await signup("acme", "  Alice@Acme.IO ", display_name="Alice")
    assert body["user"]["email"] == "alice@acme.io"
    assert body["tenant"]["slug"] == "acme"
    assert body["roles"] == ["admin"]
    assert body["token_type"] == "bearer" and body["expires_in"] == settings.access_token_ttl_s
    claims = jwt.decode(
        body["access_token"], settings.jwt_key(), algorithms=["HS256"], audience="pmaip-api", issuer=settings.jwt_issuer
    )
    assert claims["sub"] == body["user"]["id"] and claims["tid"] == body["tenant"]["id"]
    assert claims["roles"] == ["admin"]
    async with db.session() as s:
        user = (await s.execute(select(User))).scalar_one()
        assert user.email == "alice@acme.io"
        assert user.password_hash.startswith("$argon2id$") and password not in user.password_hash
        token_row = (await s.execute(select(RefreshToken))).scalar_one()
        assert token_row.token_hash != body["refresh_token"] and len(token_row.token_hash) == 64


async def test_signup_validation_and_conflicts(client: httpx.AsyncClient, signup: Signup, password: str) -> None:
    await signup("acme", "a@acme.io")
    base = {"org_name": "X", "org_slug": "xorg", "email": "x@x.io", "password": password}
    cases: list[tuple[Json, int, str]] = [
        ({**base, "password": "short"}, 422, "weak_password"),
        ({**base, "org_slug": "Bad Slug!"}, 422, "invalid_slug"),
        ({**base, "email": "not-an-email"}, 422, "invalid_email"),
        ({**base, "org_slug": "acme"}, 409, "slug_taken"),
        ({**base, "email": "A@ACME.IO", "password": "wrong password!!"}, 409, "email_taken"),
    ]
    for payload, status, code in cases:
        r = await client.post("/v1/auth/signup", json=payload)
        assert (r.status_code, r.json()["error"]["code"]) == (status, code), payload
    r = await client.post("/v1/auth/signup", json={**base, "unexpected": 1})
    assert r.status_code == 422


async def test_login_success_and_failures(
    client: httpx.AsyncClient, signup: Signup, login: Login, audit_actions: Callable[..., Any], password: str
) -> None:
    org = await signup("acme", "a@acme.io")
    before_ok, before_fail = _metric("login", "success"), _metric("login", "failure")

    r = await login("A@ACME.io")
    assert r.status_code == 200 and r.json()["tenant"]["slug"] == "acme"

    bad = await login("a@acme.io", "wrong password!!")
    unknown = await login("nobody@acme.io")
    for resp in (bad, unknown):  # identical answers: no user enumeration
        assert resp.status_code == 401
        assert resp.json()["error"] == {"code": "invalid_credentials", "message": "invalid email or password"}
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    assert _metric("login", "success") == before_ok + 1
    assert _metric("login", "failure") == before_fail + 2
    rows = await audit_actions(org["tenant"]["id"])
    actions = [e.action for e in rows]
    assert actions == ["auth.signup", "auth.login", "auth.login_failed"]
    for e in rows:  # audit never carries credentials
        assert password not in repr(e.detail) and "wrong password" not in repr(e.detail)
        assert "token" not in repr(e.detail)


async def test_inactive_user_cannot_login_or_refresh(
    client: httpx.AsyncClient, signup: Signup, login: Login, db: Database
) -> None:
    org = await signup("acme", "a@acme.io")
    async with db.session() as s:
        await s.execute(update(User).values(is_active=False))
        await s.commit()
    assert (await login("a@acme.io")).status_code == 401
    r = await client.post("/v1/auth/refresh", json={"refresh_token": org["refresh_token"]})
    assert r.status_code == 401


async def test_refresh_rotates_and_reuse_revokes_family(
    client: httpx.AsyncClient, signup: Signup, audit_actions: Callable[..., Any], auth_header: Header
) -> None:
    org = await signup("acme", "a@acme.io")
    t0 = org["refresh_token"]

    r1 = await client.post("/v1/auth/refresh", json={"refresh_token": t0})
    assert r1.status_code == 200
    t1 = r1.json()["refresh_token"]
    assert t1 != t0 and r1.json()["access_token"] != org["access_token"]
    assert (await client.get("/v1/auth/me", headers=auth_header(r1.json()["access_token"]))).status_code == 200

    r2 = await client.post("/v1/auth/refresh", json={"refresh_token": t1})
    assert r2.status_code == 200
    t2 = r2.json()["refresh_token"]

    before = _metric("refresh", "reuse_detected")
    # Attacker replays the stolen, already-rotated t0.
    replay = await client.post("/v1/auth/refresh", json={"refresh_token": t0})
    assert replay.status_code == 401
    assert _metric("refresh", "reuse_detected") == before + 1
    # The legitimate holder's newest token is now dead too: whole family revoked.
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": t2})).status_code == 401
    actions = [e.action for e in await audit_actions(org["tenant"]["id"])]
    assert actions.count("auth.refresh") == 2 and "auth.refresh_reuse_detected" in actions


async def test_refresh_rejects_unknown_and_expired(
    client: httpx.AsyncClient, signup: Signup, db: Database
) -> None:
    org = await signup("acme", "a@acme.io")
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": "bogus"})).status_code == 401
    async with db.session() as s:
        await s.execute(update(RefreshToken).values(expires_at=utcnow() - timedelta(seconds=1)))
        await s.commit()
    r = await client.post("/v1/auth/refresh", json={"refresh_token": org["refresh_token"]})
    assert r.status_code == 401 and r.json()["error"]["code"] == "refresh_token_expired"


async def test_logout_revokes_family(
    client: httpx.AsyncClient, signup: Signup, login: Login, audit_actions: Callable[..., Any]
) -> None:
    org = await signup("acme", "a@acme.io")
    other_session = (await login("a@acme.io")).json()["refresh_token"]
    rotated = (await client.post("/v1/auth/refresh", json={"refresh_token": org["refresh_token"]})).json()
    r = await client.post("/v1/auth/logout", json={"refresh_token": rotated["refresh_token"]})
    assert r.status_code == 204
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]})).status_code == 401
    # Logging out one session leaves the user's other sessions alone.
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": other_session})).status_code == 200
    # Idempotent and no oracle for unknown tokens.
    assert (await client.post("/v1/auth/logout", json={"refresh_token": "unknown"})).status_code == 204
    assert "auth.logout" in [e.action for e in await audit_actions(org["tenant"]["id"])]


async def test_me_and_bearer_validation(
    client: httpx.AsyncClient, signup: Signup, settings: PlatformSettings, auth_header: Header
) -> None:
    org = await signup("acme", "a@acme.io", display_name="Alice")
    r = await client.get("/v1/auth/me", headers=auth_header(org["access_token"]))
    assert r.status_code == 200
    assert r.json() == {
        "user": {"id": org["user"]["id"], "email": "a@acme.io", "display_name": "Alice"},
        "tenant": org["tenant"],
        "roles": ["admin"],
        "password_change_required": False,
    }
    claims = jwt.decode(org["access_token"], options={"verify_signature": False})
    expired = jwt.encode({**claims, "exp": claims["iat"] - 10}, settings.jwt_key(), algorithm="HS256")
    wrong_aud = jwt.encode({**claims, "aud": "other-api"}, settings.jwt_key(), algorithm="HS256")
    escalated = jwt.encode({**claims, "tid": "someone-else"}, "attacker-key-" + "z" * 32, algorithm="HS256")
    bad_role = jwt.encode({**claims, "roles": ["superuser"]}, settings.jwt_key(), algorithm="HS256")
    for headers in (
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer"},
        auth_header(expired),
        auth_header(wrong_aud),
        auth_header(escalated),
        auth_header(bad_role),
        auth_header(org["access_token"][:-3] + "abc"),
    ):
        resp = await client.get("/v1/members", headers=headers)
        assert resp.status_code == 401, headers
    assert (await client.get("/v1/auth/me", headers=auth_header(expired))).json()["error"]["code"] == "token_expired"


async def test_require_roles_with_real_tokens(
    app: FastAPI, client: httpx.AsyncClient, signup: Signup, login: Login, add_member: Any, auth_header: Header,
    password: str,
) -> None:
    @app.get("/_test/pm-only")
    async def pm_only(p: Annotated[Principal, Depends(require_roles(Role.PM))]) -> dict[str, str]:
        return {"user": p.user_id}

    @app.get("/_test/whoami")
    async def whoami(p: Annotated[Principal, Depends(current_principal)]) -> dict[str, object]:
        return {"tenant": p.tenant_id, "roles": sorted(p.roles)}

    admin = await signup("acme", "admin@acme.io")
    for email, roles in (("pm@acme.io", ["pm"]), ("viewer@acme.io", ["viewer"])):
        assert (await add_member(admin["access_token"], email, roles, initial_password=password)).status_code == 201
    tokens = {}
    for email in ("pm@acme.io", "viewer@acme.io"):
        first = (await login(email)).json()
        changed = await client.post(
            "/v1/auth/password",
            json={"current_password": password, "new_password": password + " v2"},
            headers=auth_header(first["access_token"]),
        )
        tokens[email] = changed.json()["access_token"]

    assert (await client.get("/_test/pm-only", headers=auth_header(admin["access_token"]))).status_code == 200
    assert (await client.get("/_test/pm-only", headers=auth_header(tokens["pm@acme.io"]))).status_code == 200
    denied = await client.get("/_test/pm-only", headers=auth_header(tokens["viewer@acme.io"]))
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "forbidden"
    who = (await client.get("/_test/whoami", headers=auth_header(tokens["viewer@acme.io"]))).json()
    assert who == {"tenant": admin["tenant"]["id"], "roles": ["viewer"]}


async def test_provisioned_member_must_change_password(
    client: httpx.AsyncClient, signup: Signup, login: Login, add_member: Any, auth_header: Header, db: Database,
    audit_actions: Callable[..., Any],
) -> None:
    admin = await signup("acme", "admin@acme.io")
    initial = "initial password 1"
    added = await add_member(admin["access_token"], "new@acme.io", ["engineer"], initial_password=initial)
    assert added.status_code == 201

    first = (await login("new@acme.io", initial)).json()
    assert first["password_change_required"] is True
    token = first["access_token"]
    # Locked to /me and /password until the password is changed.
    assert (await client.get("/v1/auth/me", headers=auth_header(token))).json()["password_change_required"] is True
    blocked = await client.get("/v1/members", headers=auth_header(token))
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "password_change_required"

    wrong = await client.post(
        "/v1/auth/password",
        json={"current_password": "not the password", "new_password": "a brand new password"},
        headers=auth_header(token),
    )
    assert wrong.status_code == 403
    weak = await client.post(
        "/v1/auth/password", json={"current_password": initial, "new_password": "short"}, headers=auth_header(token)
    )
    assert weak.status_code == 422
    same = await client.post(
        "/v1/auth/password", json={"current_password": initial, "new_password": initial}, headers=auth_header(token)
    )
    assert same.status_code == 422

    changed = await client.post(
        "/v1/auth/password",
        json={"current_password": initial, "new_password": "a brand new password"},
        headers=auth_header(token),
    )
    assert changed.status_code == 200 and changed.json()["password_change_required"] is False
    # Old sessions die with the old password; the new pair works.
    old = await client.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    new = await client.post("/v1/auth/refresh", json={"refresh_token": changed.json()["refresh_token"]})
    assert (old.status_code, new.status_code) == (401, 200)
    assert (await login("new@acme.io", initial)).status_code == 401
    assert (await login("new@acme.io", "a brand new password")).status_code == 200
    async with db.session() as s:
        user = (await s.execute(select(User).where(User.email == "new@acme.io"))).scalar_one()
        assert user.must_change_password is False
    actions = [e.action for e in await audit_actions(admin["tenant"]["id"])]
    assert "auth.password_changed" in actions and "auth.password_change_failed" in actions


async def test_login_rehashes_outdated_hash(
    client: httpx.AsyncClient, signup: Signup, login: Login, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from argon2 import PasswordHasher

    from app.platform.auth import passwords

    await signup("acme", "a@acme.io")
    monkeypatch.setattr(passwords, "_hasher", PasswordHasher(time_cost=2, memory_cost=2048, parallelism=1))
    assert (await login("a@acme.io")).status_code == 200
    async with db.session() as s:
        stored = (await s.execute(select(User.password_hash))).scalar_one()
    assert "m=2048,t=2" in stored


async def test_user_in_two_tenants_gets_tenant_scoped_tokens(
    client: httpx.AsyncClient, signup: Signup, login: Login, add_member: Any, auth_header: Header, db: Database,
    settings: PlatformSettings,
) -> None:
    acme = await signup("acme", "shared@corp.io")
    globex = await signup("globex", "shared@corp.io")  # same identity proves its password, gets a 2nd org
    assert acme["user"]["id"] == globex["user"]["id"]
    await add_member(globex["access_token"], "bob@globex.io", ["viewer"])

    ambiguous = await login("shared@corp.io")
    assert ambiguous.status_code == 400
    assert ambiguous.json()["error"]["code"] == "tenant_required"
    assert "acme" in ambiguous.json()["error"]["message"] and "globex" in ambiguous.json()["error"]["message"]

    a = (await login("shared@corp.io", tenant_slug="acme")).json()
    g = (await login("shared@corp.io", tenant_slug="GLOBEX")).json()
    assert jwt.decode(a["access_token"], options={"verify_signature": False})["tid"] == acme["tenant"]["id"]
    assert jwt.decode(g["access_token"], options={"verify_signature": False})["tid"] == globex["tenant"]["id"]

    acme_members = (await client.get("/v1/members", headers=auth_header(a["access_token"]))).json()
    assert [m["email"] for m in acme_members] == ["shared@corp.io"]  # bob (globex) is invisible
    bob_id = next(
        m["user_id"]
        for m in (await client.get("/v1/members", headers=auth_header(g["access_token"]))).json()
        if m["email"] == "bob@globex.io"
    )
    # Acme token cannot touch a Globex member: 404, not 403 (no existence leak).
    for call in (
        client.patch(f"/v1/members/{bob_id}", json={"roles": ["pm"]}, headers=auth_header(a["access_token"])),
        client.delete(f"/v1/members/{bob_id}", headers=auth_header(a["access_token"])),
    ):
        assert (await call).status_code == 404
    # Refresh keeps the session pinned to its tenant.
    refreshed = (await client.post("/v1/auth/refresh", json={"refresh_token": a["refresh_token"]})).json()
    assert refreshed["tenant"]["slug"] == "acme"

    not_member = await login("bob@globex.io", "correct horse battery staple!", tenant_slug="acme")
    assert not_member.status_code == 403
    async with db.session() as s:
        roles = (await s.execute(select(Membership.tenant_id, Membership.role))).all()
    assert len(roles) == 3


async def test_audit_rows_for_signup_have_no_secrets(
    client: httpx.AsyncClient, signup: Signup, db: Database, password: str
) -> None:
    org = await signup("acme", "a@acme.io")
    async with db.session() as s:
        rows = (await s.execute(select(AuditEvent))).scalars().all()
    assert [r.action for r in rows] == ["auth.signup"]
    assert rows[0].tenant_id == org["tenant"]["id"] and rows[0].actor_id == org["user"]["id"]
    blob = repr([(r.detail, r.target_id) for r in rows])
    assert password not in blob and org["refresh_token"] not in blob and org["access_token"] not in blob
