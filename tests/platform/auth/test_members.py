"""Org membership administration: /v1/members."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from sqlalchemy import select

from app.platform.auth.models import RefreshToken
from app.platform.db import Database

Json = dict[str, Any]
Signup = Callable[..., Awaitable[Json]]
Login = Callable[..., Awaitable[httpx.Response]]
Header = Callable[[str], dict[str, str]]


async def _active_token(client: httpx.AsyncClient, login: Login, auth_header: Header, email: str, initial: str) -> Json:
    """Log in a provisioned member and complete the forced password change."""
    first = (await login(email, initial)).json()
    r = await client.post(
        "/v1/auth/password",
        json={"current_password": initial, "new_password": initial + " changed"},
        headers=auth_header(first["access_token"]),
    )
    assert r.status_code == 200, r.text
    body: Json = r.json()
    return body


async def test_add_list_update_remove(
    client: httpx.AsyncClient, signup: Signup, add_member: Any, auth_header: Header,
    audit_actions: Callable[..., Any],
) -> None:
    admin = await signup("acme", "admin@acme.io")
    h = auth_header(admin["access_token"])
    r = await add_member(admin["access_token"], "Eng@Acme.io", ["engineer", "pm"])
    assert r.status_code == 201
    eng = r.json()
    assert eng["email"] == "eng@acme.io" and eng["roles"] == ["pm", "engineer"] and eng["is_active"] is True

    dup = await add_member(admin["access_token"], "eng@acme.io", ["viewer"])
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "already_member"
    weak = await add_member(admin["access_token"], "x@acme.io", ["viewer"], initial_password="short")
    assert weak.status_code == 422
    for roles in ([], ["god"]):
        bad = {"email": "y@acme.io", "roles": roles, "initial_password": "x" * 12}
        assert (await client.post("/v1/members", json=bad, headers=h)).status_code == 422

    listed = (await client.get("/v1/members", headers=h)).json()
    assert [(m["email"], m["roles"]) for m in listed] == [
        ("admin@acme.io", ["admin"]),
        ("eng@acme.io", ["pm", "engineer"]),
    ]

    r = await client.patch(f"/v1/members/{eng['user_id']}", json={"roles": ["viewer", "admin"]}, headers=h)
    assert r.status_code == 200 and r.json()["roles"] == ["admin", "viewer"]

    assert (await client.delete(f"/v1/members/{eng['user_id']}", headers=h)).status_code == 204
    assert [m["email"] for m in (await client.get("/v1/members", headers=h)).json()] == ["admin@acme.io"]
    assert (await client.delete(f"/v1/members/{eng['user_id']}", headers=h)).status_code == 404
    assert (await client.patch("/v1/members/nope", json={"roles": ["pm"]}, headers=h)).status_code == 404

    rows = await audit_actions(admin["tenant"]["id"])
    assert [e.action for e in rows if e.action.startswith("member.")] == [
        "member.added",
        "member.roles_changed",
        "member.removed",
    ]
    changed = next(e for e in rows if e.action == "member.roles_changed")
    assert changed.detail == {"before": ["engineer", "pm"], "after": ["admin", "viewer"]}
    assert all("initial_password" not in repr(e.detail) and "password" not in repr(e.detail) for e in rows)


async def test_last_admin_protection(
    client: httpx.AsyncClient, signup: Signup, login: Login, add_member: Any, auth_header: Header
) -> None:
    admin = await signup("acme", "admin@acme.io")
    h = auth_header(admin["access_token"])
    me = admin["user"]["id"]

    demote = await client.patch(f"/v1/members/{me}", json={"roles": ["pm"]}, headers=h)
    assert demote.status_code == 409 and demote.json()["error"]["code"] == "last_admin"
    remove = await client.delete(f"/v1/members/{me}", headers=h)
    assert remove.status_code == 409 and remove.json()["error"]["code"] == "last_admin"
    # Keeping admin while adding roles is fine.
    assert (await client.patch(f"/v1/members/{me}", json={"roles": ["admin", "pm"]}, headers=h)).status_code == 200

    # With a second admin, the first may step down.
    second = (await add_member(admin["access_token"], "two@acme.io", ["admin"])).json()
    assert (await client.patch(f"/v1/members/{me}", json={"roles": ["pm"]}, headers=h)).status_code == 200
    # ...and now the second is the last one.
    two = await _active_token(client, login, auth_header, "two@acme.io", "correct horse battery staple!")
    h2 = auth_header(two["access_token"])
    assert (await client.delete(f"/v1/members/{second['user_id']}", headers=h2)).status_code == 409


async def test_non_admin_cannot_manage_members(
    client: httpx.AsyncClient, signup: Signup, login: Login, add_member: Any, auth_header: Header
) -> None:
    admin = await signup("acme", "admin@acme.io")
    await add_member(admin["access_token"], "pm@acme.io", ["pm"])
    pm = await _active_token(client, login, auth_header, "pm@acme.io", "correct horse battery staple!")
    h = auth_header(pm["access_token"])
    assert (await client.get("/v1/members", headers=h)).status_code == 403
    r = await client.post(
        "/v1/members", json={"email": "z@acme.io", "roles": ["admin"], "initial_password": "x" * 12}, headers=h
    )
    assert r.status_code == 403
    promote = await client.patch(f"/v1/members/{pm['user']['id']}", json={"roles": ["admin"]}, headers=h)
    assert promote.status_code == 403
    assert (await client.delete(f"/v1/members/{admin['user']['id']}", headers=h)).status_code == 403


async def test_existing_user_joins_without_password_reset(
    client: httpx.AsyncClient, signup: Signup, login: Login, add_member: Any, auth_header: Header, password: str
) -> None:
    globex = await signup("globex", "carol@corp.io")  # carol already has an account (and password)
    acme = await signup("acme", "admin@acme.io")
    r = await add_member(acme["access_token"], "carol@corp.io", ["pm"], initial_password="attacker chosen pw")
    assert r.status_code == 201 and r.json()["user_id"] == globex["user"]["id"]
    # Acme's admin did not get to set carol's password, nor force a change.
    assert (await login("carol@corp.io", "attacker chosen pw", tenant_slug="acme")).status_code == 401
    joined = (await login("carol@corp.io", password, tenant_slug="acme")).json()
    assert joined["roles"] == ["pm"] and joined["password_change_required"] is False


async def test_removed_member_loses_tenant_sessions_only(
    client: httpx.AsyncClient, signup: Signup, login: Login, add_member: Any, auth_header: Header, db: Database,
    password: str,
) -> None:
    acme = await signup("acme", "admin@acme.io")
    await signup("globex", "dave@corp.io")
    await add_member(acme["access_token"], "dave@corp.io", ["engineer"])
    dave_acme = (await login("dave@corp.io", password, tenant_slug="acme")).json()
    dave_globex = (await login("dave@corp.io", password, tenant_slug="globex")).json()

    h = auth_header(acme["access_token"])
    assert (await client.delete(f"/v1/members/{dave_acme['user']['id']}", headers=h)).status_code == 204

    acme_refresh = await client.post("/v1/auth/refresh", json={"refresh_token": dave_acme["refresh_token"]})
    globex_refresh = await client.post("/v1/auth/refresh", json={"refresh_token": dave_globex["refresh_token"]})
    assert (acme_refresh.status_code, globex_refresh.status_code) == (401, 200)
    assert (await login("dave@corp.io", password, tenant_slug="acme")).status_code == 403
    async with db.session() as s:
        live = (await s.execute(select(RefreshToken).where(RefreshToken.revoked_at.is_(None)))).scalars().all()
    assert {t.tenant_id for t in live if t.user_id == dave_acme["user"]["id"]} == {dave_globex["tenant"]["id"]}


async def test_role_change_applies_on_refresh(
    client: httpx.AsyncClient, signup: Signup, login: Login, add_member: Any, auth_header: Header
) -> None:
    admin = await signup("acme", "admin@acme.io")
    await add_member(admin["access_token"], "v@acme.io", ["viewer"])
    v = await _active_token(client, login, auth_header, "v@acme.io", "correct horse battery staple!")
    admin_h = auth_header(admin["access_token"])
    await client.patch(f"/v1/members/{v['user']['id']}", json={"roles": ["admin"]}, headers=admin_h)
    refreshed = (await client.post("/v1/auth/refresh", json={"refresh_token": v["refresh_token"]})).json()
    assert refreshed["roles"] == ["admin"]
    assert (await client.get("/v1/members", headers=auth_header(refreshed["access_token"]))).status_code == 200
