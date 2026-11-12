"""Tenant isolation on every endpoint, and read-only viewers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.platform.core_models import Tenant
from app.platform.principal import Role

Actor = Callable[..., Any]


@pytest.fixture
async def tenant_a_state(
    client: httpx.AsyncClient, tenant: Tenant, other_tenant: Tenant, as_user: Actor, loop1_intent: str,
    spec_v3: dict[str, Any],
) -> dict[str, str]:
    """Tenant A: bootstrapped, a custom roadmap item, an integration, a release. Tenant B: bootstrapped only."""
    as_user(other_tenant, Role.ADMIN, user_id="b-admin")
    assert (await client.post("/v1/kg/bootstrap")).status_code == 200
    as_user(tenant, Role.ADMIN, user_id="a-admin")
    assert (await client.post("/v1/kg/bootstrap")).status_code == 200
    r = await client.post("/v1/roadmap", json={"title": "Acme secret initiative", "requires": ["bo-invoice"]})
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]
    r = await client.post("/v1/integrations", json={"intent": loop1_intent})
    assert r.status_code == 201, r.text
    draft = r.json()
    r = await client.post("/v1/api-releases", json={"systemId": "sys-esign", "spec": spec_v3})
    assert r.status_code == 201, r.text
    return {
        "item": item_id, "integration": draft["integration"]["id"], "version": draft["version"]["id"],
        "release": r.json()["release"]["id"],
    }


async def test_other_tenant_gets_404_everywhere(
    client: httpx.AsyncClient, other_tenant: Tenant, as_user: Actor, tenant_a_state: dict[str, str],
) -> None:
    ids = tenant_a_state
    base = f"/v1/integrations/{ids['integration']}"
    vbase = f"{base}/versions/{ids['version']}"
    as_user(other_tenant, Role.ADMIN, user_id="b-admin")
    for method, path, body in [
        ("GET", base, None),
        ("GET", vbase, None),
        ("PATCH", f"{vbase}/mappings", {"edits": [{"target": "Customer.currency_code", "transform": "x"}]}),
        ("POST", f"{vbase}/approvals", {"role": "pm"}),
        ("GET", f"/v1/roadmap/{ids['item']}", None),
        ("PATCH", f"/v1/roadmap/{ids['item']}", {"title": "hijacked"}),
        ("GET", f"/v1/api-releases/{ids['release']}", None),
        ("POST", "/v1/integrations", {"intent": "Every closed-won deal becomes a billing customer",
                                      "roadmapItemId": ids["item"]}),
    ]:
        r = await client.request(method, path, json=body)
        assert r.status_code == 404, (method, path, r.text)
        assert r.json()["error"]["code"] == "not_found"


async def test_lists_and_kg_only_show_own_rows(
    client: httpx.AsyncClient, tenant: Tenant, other_tenant: Tenant, as_user: Actor,
    tenant_a_state: dict[str, str],
) -> None:
    as_user(other_tenant, Role.VIEWER, user_id="b-viewer")
    assert (await client.get("/v1/integrations")).json() == []
    releases_b = (await client.get("/v1/api-releases")).json()
    assert [r["status"] for r in releases_b] == ["baseline"]
    roadmap_b = (await client.get("/v1/roadmap")).json()
    assert tenant_a_state["item"] not in {r["id"] for r in roadmap_b}
    assert all(r["status"] != "in_progress" or r["title"] != "Contract-to-cash automation" for r in roadmap_b)
    kg_b = (await client.get("/v1/kg")).json()
    node_ids_b = {n["id"] for n in kg_b["nodes"]}
    assert f"rm-{tenant_a_state['item']}" not in node_ids_b
    assert "bo-agreement" not in node_ids_b  # A's release capabilities never leak into B's KG

    as_user(tenant, Role.VIEWER, user_id="a-viewer")
    kg_a = (await client.get("/v1/kg")).json()
    assert {f"rm-{tenant_a_state['item']}", "bo-agreement"} <= {n["id"] for n in kg_a["nodes"]}
    assert len((await client.get("/v1/integrations")).json()) == 2  # user draft + agent draft
    assert len((await client.get("/v1/api-releases")).json()) == 2


async def test_version_of_other_integration_is_404(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, tenant_a_state: dict[str, str],
) -> None:
    as_user(tenant, Role.VIEWER, user_id="a-viewer")
    others = [i for i in (await client.get("/v1/integrations")).json() if i["id"] != tenant_a_state["integration"]]
    r = await client.get(f"/v1/integrations/{others[0]['id']}/versions/{tenant_a_state['version']}")
    assert r.status_code == 404


async def test_kg_empty_until_bootstrapped(client: httpx.AsyncClient, tenant: Tenant, as_user: Actor) -> None:
    as_user(tenant, Role.PM, user_id="pm-1")
    assert (await client.get("/v1/kg")).json()["nodes"] == []
    r = await client.post("/v1/integrations", json={"intent": "closed-won deals become billing customers"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "kg_not_bootstrapped"


async def test_viewer_is_read_only(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, tenant_a_state: dict[str, str], spec_v3: dict[str, Any]
) -> None:
    ids = tenant_a_state
    vbase = f"/v1/integrations/{ids['integration']}/versions/{ids['version']}"
    as_user(tenant, Role.VIEWER, user_id="a-viewer")
    for method, path, body in [
        ("POST", "/v1/kg/bootstrap", None),
        ("POST", "/v1/roadmap", {"title": "x"}),
        ("PATCH", f"/v1/roadmap/{ids['item']}", {"title": "x"}),
        ("POST", "/v1/integrations", {"intent": "closed-won deals become billing customers"}),
        ("PATCH", f"{vbase}/mappings", {"edits": [{"target": "Customer.currency_code", "transform": "x"}]}),
        ("POST", f"{vbase}/approvals", {"role": "pm"}),
        ("POST", "/v1/api-releases", {"systemId": "sys-esign", "spec": spec_v3}),
    ]:
        r = await client.request(method, path, json=body)
        assert r.status_code == 403, (method, path, r.text)
    for path in ["/v1/kg", "/v1/roadmap", f"/v1/roadmap/{ids['item']}", "/v1/integrations",
                 f"/v1/integrations/{ids['integration']}", vbase, "/v1/api-releases",
                 f"/v1/api-releases/{ids['release']}"]:
        assert (await client.get(path)).status_code == 200, path


async def test_write_roles_are_specific(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, tenant_a_state: dict[str, str], spec_v3: dict[str, Any]
) -> None:
    ids = tenant_a_state
    vbase = f"/v1/integrations/{ids['integration']}/versions/{ids['version']}"
    as_user(tenant, Role.ENGINEER, user_id="eng-1")
    assert (await client.post("/v1/kg/bootstrap")).status_code == 403
    assert (await client.post("/v1/roadmap", json={"title": "x"})).status_code == 403
    as_user(tenant, Role.PM, user_id="pm-1")
    assert (await client.post("/v1/api-releases", json={"systemId": "sys-esign", "spec": spec_v3})).status_code == 403
    edit = {"target": "Customer.currency_code", "transform": "x"}
    r = await client.patch(f"{vbase}/mappings", json={"edits": [edit]})
    assert r.status_code == 403
