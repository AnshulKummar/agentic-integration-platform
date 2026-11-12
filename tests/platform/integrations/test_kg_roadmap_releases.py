"""KG persistence and bootstrap, roadmap CRUD, drafting/release validation."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import httpx

from app.demo.models import KGNode
from app.platform.core_models import Tenant
from app.platform.db import Database
from app.platform.integrations.kg_repo import KGRepository
from app.platform.principal import Role

Actor = Callable[..., Any]


async def _bootstrap(client: httpx.AsyncClient, tenant: Tenant, as_user: Actor) -> dict[str, Any]:
    as_user(tenant, Role.ADMIN, user_id="admin-1")
    r = await client.post("/v1/kg/bootstrap")
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def test_bootstrap_is_idempotent(client: httpx.AsyncClient, tenant: Tenant, as_user: Actor) -> None:
    first = await _bootstrap(client, tenant, as_user)
    assert first["nodesAdded"] > 30 and first["roadmapItemsAdded"] == 6
    assert first["contextIntegrationsAdded"] == 1 and first["baselineReleasesAdded"] == 1
    second = await _bootstrap(client, tenant, as_user)
    assert second["nodesAdded"] == 0 and second["edgesAdded"] == 0 and second["roadmapItemsAdded"] == 0
    assert second["contextIntegrationsAdded"] == 0 and second["baselineReleasesAdded"] == 0
    assert second["counts"] == first["counts"]
    assert len((await client.get("/v1/roadmap")).json()) == 6
    releases = (await client.get("/v1/api-releases", params={"systemId": "sys-esign"})).json()
    assert [(r["version"], r["status"]) for r in releases] == [("2.4.0", "baseline")]


async def test_kg_type_filter(client: httpx.AsyncClient, tenant: Tenant, as_user: Actor) -> None:
    await _bootstrap(client, tenant, as_user)
    systems = (await client.get("/v1/kg", params={"type": "system"})).json()
    assert {n["id"] for n in systems["nodes"]} == {"sys-crm", "sys-billing", "sys-tax", "sys-esign"}
    assert all(e["source"].startswith("sys-") or e["target"].startswith("sys-") for e in systems["edges"])
    assert (await client.get("/v1/kg", params={"type": "spaceship"})).status_code == 422
    roadmap_nodes = (await client.get("/v1/kg", params={"type": "roadmap"})).json()["nodes"]
    assert all("roadmapItemId" in n["props"] for n in roadmap_nodes) and len(roadmap_nodes) == 6


async def test_kg_repository_round_trip(db: Database, tenant: Tenant) -> None:
    async with db.session(tenant.id) as s:
        repo = KGRepository(s, tenant.id)
        kg = await repo.load()
        kg.add_node(KGNode(id="a", label="A", type="business_object", props={"x": [1, 2]}))
        kg.add_node(KGNode(id="b", label="B", type="business_object", confidence=0.5, note="n"))
        kg.add_edge("a", "EQUIVALENT_TO", "b", "loop2", 0.87)
        stats = await repo.save(kg)
        assert (stats.nodes_created, stats.edges_created) == (2, 1)
        await s.commit()
    async with db.session(tenant.id) as s:
        repo = KGRepository(s, tenant.id)
        kg = await repo.load()
        edge = kg.get_edge("a", "EQUIVALENT_TO", "b")
        assert edge is not None and edge.confidence == 0.87 and edge.learned_in == "loop2"
        assert kg.require_node("a").props == {"x": [1, 2]} and kg.require_node("b").note == "n"
        assert await repo.save(kg) == type(stats)()  # nothing changed -> no writes
        kg.update_node("b", confidence=0.9)
        assert (await repo.save(kg)).nodes_updated == 1
        await s.commit()


async def test_roadmap_crud_links_kg(client: httpx.AsyncClient, tenant: Tenant, as_user: Actor) -> None:
    await _bootstrap(client, tenant, as_user)
    as_user(tenant, Role.PM, user_id="pm-1")
    r = await client.post("/v1/roadmap", json={"title": "Invoice reminders", "requires": ["bo-invoice"],
                                                "column": "next", "intent": "Remind customers of unpaid invoices."})
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["status"] == "planned" and item["column"] == "next"
    kg = (await client.get("/v1/kg", params={"type": "roadmap"})).json()
    node = next(n for n in kg["nodes"] if n["props"]["roadmapItemId"] == item["id"])
    assert node["label"] == "Roadmap: Invoice reminders"
    assert f"{node['id']}--REQUIRES--bo-invoice" in {e["id"] for e in kg["edges"]}

    r = await client.patch(f"/v1/roadmap/{item['id']}", json={"title": "Dunning", "status": "blocked",
                                                               "blockedReason": "no dunning API"})
    assert r.status_code == 200 and r.json()["title"] == "Dunning" and r.json()["blockedReason"] == "no dunning API"
    kg = (await client.get("/v1/kg", params={"type": "roadmap"})).json()
    assert any(n["label"] == "Roadmap: Dunning" for n in kg["nodes"])

    assert (await client.post("/v1/roadmap", json={"title": "x", "requires": ["nope"]})).status_code == 422
    assert (await client.patch(f"/v1/roadmap/{item['id']}", json={"status": "exploded"})).status_code == 422
    assert (await client.patch(f"/v1/roadmap/{item['id']}", json={"requires": ["nope"]})).status_code == 422
    assert (await client.get(f"/v1/roadmap/{item['id']}")).json()["requires"] == ["bo-invoice"]


async def test_draft_validation(client: httpx.AsyncClient, tenant: Tenant, as_user: Actor) -> None:
    await _bootstrap(client, tenant, as_user)
    as_user(tenant, Role.ENGINEER, user_id="eng-1")
    cases: list[tuple[dict[str, Any], int, str]] = [
        ({"intent": "Make everything better"}, 422, "unparsed_intent"),
        ({"intent": "When a customer signs the contract, create the billing customer"}, 409, "no_trigger"),
        ({"intent": "closed-won deals become billing customers", "targetSystem": "sys-esign"}, 422,
         "system_mismatch"),
        ({"intent": "closed-won deals become billing customers", "sourceSystem": "sys-mars"}, 422, "unknown_system"),
        ({"intent": "closed-won deals become billing customers", "trigger": "bo-account"}, 422, "bad_trigger"),
        ({"intent": "closed-won deals become billing customers", "roadmapItemId": "missing"}, 404, "not_found"),
    ]
    for body, code, err in cases:
        r = await client.post("/v1/integrations", json=body)
        assert r.status_code == code, (body, r.text)
        assert r.json()["error"]["code"] == err
    assert (await client.post("/v1/integrations", json={"intent": "x", "bogus": 1})).status_code == 422
    assert (await client.get("/v1/integrations")).json() == []


async def test_draft_with_explicit_trigger_label_and_name(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor
) -> None:
    await _bootstrap(client, tenant, as_user)
    as_user(tenant, Role.ENGINEER, user_id="eng-1")
    r = await client.post("/v1/integrations", json={
        "intent": "Create the billing customer and the first invoice", "trigger": "Deal closed-won",
        "name": "Won deal to invoice",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["integration"]["name"] == "Won deal to invoice"
    assert body["integration"]["sourceSystem"] == "sys-crm" and body["integration"]["targetSystem"] == "sys-billing"
    assert body["integration"]["authorId"] == "eng-1" and body["version"]["createdBy"] == "eng-1"
    assert body["version"]["number"] == 1 and body["version"]["status"] == "in_review"
    assert "billing.create_invoice" in body["version"]["plan"]["operations"]
    listed = (await client.get("/v1/integrations")).json()
    assert listed[0]["latestVersion"]["id"] == body["version"]["id"]
    detail = (await client.get(f"/v1/integrations/{body['integration']['id']}")).json()
    assert [v["number"] for v in detail["versions"]] == [1]


async def test_release_validation(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, spec_v3: dict[str, Any]
) -> None:
    await _bootstrap(client, tenant, as_user)
    as_user(tenant, Role.ENGINEER, user_id="eng-1")
    bad = copy.deepcopy(spec_v3)
    bad["openapi"] = "2.0"
    cases: list[tuple[dict[str, Any], int, str]] = [
        ({"systemId": "sys-mars", "spec": spec_v3}, 422, "unknown_system"),
        ({"systemId": "sys-esign", "spec": bad}, 422, "invalid_spec"),
        ({"systemId": "sys-esign", "spec": spec_v3, "version": "9.9.9"}, 422, "version_mismatch"),
        ({"systemId": "sys-esign", "spec": {"openapi": "3.0.0", "paths": {}}}, 422, "invalid_spec"),
    ]
    for body, code, err in cases:
        r = await client.post("/v1/api-releases", json=body)
        assert r.status_code == code, (body, r.text)
        assert r.json()["error"]["code"] == err
    assert (await client.post("/v1/api-releases", json={"systemId": "sys-esign", "spec": spec_v3})).status_code == 201
    r = await client.post("/v1/api-releases", json={"systemId": "sys-esign", "spec": spec_v3})
    assert r.status_code == 409 and r.json()["error"]["code"] == "release_exists"
    # After the release, "contract signed" intents have a trigger.
    r = await client.post("/v1/integrations", json={
        "intent": "When a customer signs the contract, create the billing customer"})
    assert r.status_code == 201, r.text
    assert r.json()["integration"]["sourceSystem"] == "sys-esign"


async def test_first_release_of_unwatched_system_diffs_against_nothing(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, spec_v3: dict[str, Any]
) -> None:
    await _bootstrap(client, tenant, as_user)
    as_user(tenant, Role.ADMIN, user_id="admin-1")
    spec = copy.deepcopy(spec_v3)
    r = await client.post("/v1/api-releases", json={"systemId": "sys-tax", "app": "Tax Service", "spec": spec})
    assert r.status_code == 201, r.text
    rel = r.json()["release"]
    assert rel["previousVersion"] is None and rel["app"] == "Tax Service"
    assert all(c["kind"] in ("added", "deprecated") for c in rel["changes"]) and rel["mcpTools"]
