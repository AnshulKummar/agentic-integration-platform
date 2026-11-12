"""Dual approval, separation of duties, edits resetting approvals, audit trail."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.platform.core_models import AuditEvent, Tenant
from app.platform.db import Database
from app.platform.errors import Conflict
from app.platform.integrations import service
from app.platform.principal import Role

Actor = Callable[..., Any]


@pytest.fixture
async def draft(client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, loop1_intent: str) -> str:
    """Bootstrapped tenant with one drafted integration by pm-author. Returns the version base path."""
    as_user(tenant, Role.ADMIN, user_id="admin-1")
    assert (await client.post("/v1/kg/bootstrap")).status_code == 200
    as_user(tenant, Role.PM, user_id="pm-author")
    r = await client.post("/v1/integrations", json={"intent": loop1_intent})
    assert r.status_code == 201, r.text
    body = r.json()
    return f"/v1/integrations/{body['integration']['id']}/versions/{body['version']['id']}"


async def _approve(client: httpx.AsyncClient, base: str, role: str) -> httpx.Response:
    return await client.post(f"{base}/approvals", json={"role": role, "comment": "ok"})


async def test_author_cannot_approve_own_version(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, draft: str
) -> None:
    as_user(tenant, Role.PM, user_id="pm-author")
    r = await _approve(client, draft, "pm")
    assert r.status_code == 403 and r.json()["error"]["code"] == "separation_of_duties"


async def test_same_user_cannot_give_both_approvals(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, draft: str
) -> None:
    as_user(tenant, Role.PM, Role.ENGINEER, user_id="both-hats")
    assert (await _approve(client, draft, "pm")).status_code == 200
    r = await _approve(client, draft, "eng")
    assert r.status_code == 403 and r.json()["error"]["code"] == "separation_of_duties"
    as_user(tenant, Role.ENGINEER, user_id="eng-1")
    r = await _approve(client, draft, "eng")
    assert r.status_code == 200 and r.json()["status"] == "approved"


async def test_role_must_match_approval_kind(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, draft: str
) -> None:
    as_user(tenant, Role.ENGINEER, user_id="eng-1")
    assert (await _approve(client, draft, "pm")).status_code == 403
    as_user(tenant, Role.PM, user_id="pm-2")
    assert (await _approve(client, draft, "eng")).status_code == 403
    assert (await client.post(f"{draft}/approvals", json={"role": "cto"})).status_code == 422
    r = (await client.get(draft)).json()
    assert r["approvals"] == [] and r["status"] == "in_review"


async def test_admin_may_act_as_either_role(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, draft: str
) -> None:
    as_user(tenant, Role.ADMIN, user_id="admin-1")
    r = await _approve(client, draft, "eng")
    assert r.status_code == 200 and r.json()["status"] == "in_review"
    as_user(tenant, Role.ADMIN, user_id="admin-2")
    r = await _approve(client, draft, "pm")
    assert r.status_code == 200 and r.json()["status"] == "approved"


async def test_duplicate_role_and_post_approval_changes_conflict(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, draft: str, engineer_edits: list[dict[str, str]]
) -> None:
    as_user(tenant, Role.PM, user_id="pm-2")
    assert (await _approve(client, draft, "pm")).status_code == 200
    as_user(tenant, Role.PM, user_id="pm-3")
    r = await _approve(client, draft, "pm")
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_approved"
    as_user(tenant, Role.ENGINEER, user_id="eng-1")
    assert (await _approve(client, draft, "eng")).status_code == 200
    # Approved versions are frozen: no more edits or approvals.
    r = await client.patch(f"{draft}/mappings", json={"edits": engineer_edits})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_editable"
    as_user(tenant, Role.ADMIN, user_id="admin-9")
    assert (await _approve(client, draft, "pm")).status_code == 409


async def test_edits_clear_approvals_and_count_as_human_edits(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, draft: str, engineer_edits: list[dict[str, str]]
) -> None:
    as_user(tenant, Role.PM, user_id="pm-2")
    assert (await _approve(client, draft, "pm")).status_code == 200
    as_user(tenant, Role.ENGINEER, user_id="eng-1")
    before = (await client.get(draft)).json()
    r = await client.patch(f"{draft}/mappings", json={"edits": engineer_edits[:1]})
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["approvals"] == [] and after["status"] == "in_review"
    assert after["humanEdits"] == 1
    row = next(m for m in after["mappings"] if m["target"] == "Customer.currency_code")
    assert row["origin"] == "human_edit" and row["transform"] == "to_iso4217()" and row.get("flag") is None
    assert after["editLog"][0]["by"] == "eng-1@example.com"
    assert after["confidence"] != before["confidence"]
    assert "to_iso4217" in after["code"]
    r = await client.patch(f"{draft}/mappings", json={"edits": engineer_edits[1:]})
    assert r.json()["humanEdits"] == 3
    # Fresh approvals are needed after the edit.
    as_user(tenant, Role.PM, user_id="pm-2")
    assert (await _approve(client, draft, "pm")).status_code == 200


async def test_invalid_edits_rejected(client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, draft: str) -> None:
    as_user(tenant, Role.ENGINEER, user_id="eng-1")
    r = await client.patch(f"{draft}/mappings", json={"edits": [{"target": "Nope.field", "transform": "x"}]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_mapping"
    r = await client.patch(f"{draft}/mappings", json={"edits": [{"target": "Customer.currency_code"}]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "empty_edit"
    assert (await client.patch(f"{draft}/mappings", json={"edits": []})).status_code == 422
    assert (await client.get(draft)).json()["humanEdits"] == 0


async def test_every_step_is_audited(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, draft: str, engineer_edits: list[dict[str, str]],
    db: Database,
) -> None:
    as_user(tenant, Role.ENGINEER, user_id="eng-1")
    await client.patch(f"{draft}/mappings", json={"edits": engineer_edits})
    as_user(tenant, Role.PM, user_id="pm-2")
    await _approve(client, draft, "pm")
    as_user(tenant, Role.ENGINEER, user_id="eng-1")
    await _approve(client, draft, "eng")
    async with db.session(tenant.id) as s:
        events = (await s.execute(select(AuditEvent).order_by(AuditEvent.created_at))).scalars().all()
    actions = [(e.action, e.actor_id) for e in events]
    assert ("kg.bootstrap", "admin-1") in actions
    assert ("integration.drafted", "pm-author") in actions
    assert ("version.mappings_edited", "eng-1") in actions
    assert ("version.approved", "pm-2") in actions and ("version.approved", "eng-1") in actions
    assert ("version.status_changed", "eng-1") in actions


async def test_learning_requires_live_version(
    client: httpx.AsyncClient, tenant: Tenant, draft: str, db: Database
) -> None:
    version_id = draft.rsplit("/", 1)[-1]
    async with db.session(tenant.id) as s:
        with pytest.raises(Conflict):
            await service.learn_from_version(s, tenant.id, version_id)
