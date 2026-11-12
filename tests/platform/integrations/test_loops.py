"""Loop 1 and loop 2 end to end through the HTTP API, on two tenants (learning on / off)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.platform.core_models import Tenant
from app.platform.integrations import service
from app.platform.principal import Role

Actor = Callable[..., Any]


async def _kg(client: httpx.AsyncClient, node_type: str | None = None) -> dict[str, Any]:
    r = await client.get("/v1/kg", params={"type": node_type} if node_type else None)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def test_loop1_drafts_reviews_approves_and_learns(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, run_loop1: Callable[[Tenant], Awaitable[dict[str, Any]]],
    go_live: Callable[[str, str], Awaitable[service.LearnOutcome]],
) -> None:
    version = await run_loop1(tenant)
    by_target = {m["target"]: m for m in version["mappings"]}
    assert set(by_target) >= {"Customer.legal_name", "Customer.currency_code", "Customer.terms_code",
                              "TaxQuote.jurisdiction"}
    assert version["humanEdits"] == 3
    assert by_target["TaxQuote.jurisdiction"]["source"] == "Account.shipping_address"
    assert by_target["Customer.currency_code"]["origin"] == "human_edit"
    assert {a["role"] for a in version["approvals"]} == {"pm", "eng"}
    assert "crm.get_account" in version["allowedTools"] and "billing.create_customer" in version["allowedTools"]
    assert "tax.calculate" in version["allowedTools"]
    assert "def run(event, tools)" in version["code"] and "to_iso4217" in version["code"]
    assert {r["label"] for r in version["risks"]} >= {"Data access boundary", "Tool budget", "Deprecated field"}
    assert version["plan"]["patternId"] == "pat-crm-billing"

    as_user(tenant, Role.VIEWER, user_id="viewer-1")
    roadmap = {r["title"]: r for r in (await client.get("/v1/roadmap")).json()}
    integ = (await client.get(f"/v1/integrations/{version['integrationId']}")).json()
    assert integ["roadmapItemId"] == roadmap["Closed-won accounts sync to billing"]["id"]
    assert roadmap["Closed-won accounts sync to billing"]["status"] == "in_review"
    assert "pat-crm-billing" not in {n["id"] for n in (await _kg(client, "pattern"))["nodes"]}

    outcome = await go_live(tenant.id, version["id"])
    assert outcome.learned and outcome.report["patternId"] == "pat-crm-billing"
    assert outcome.roadmap_shipped == integ["roadmapItemId"]
    again = await go_live(tenant.id, version["id"])
    assert not again.learned and again.skipped_reason == "already_learned"

    patterns = {n["id"]: n for n in (await _kg(client, "pattern"))["nodes"]}
    assert patterns["pat-crm-billing"]["learnedIn"] == "loop1"
    rules = {n["id"] for n in (await _kg(client, "rule"))["nodes"]}
    assert rules == {"rule-iso-currency", "rule-terms-map", "rule-tax-shipto"}
    full = await _kg(client)
    edge_ids = {e["id"] for e in full["edges"]}
    assert "f-acc-shipping--MAPS_TO--f-tax-jur" in edge_ids
    assert "pat-crm-billing--IMPLEMENTS--rm-billing-sync" in edge_ids
    shipped = next(r for r in (await client.get("/v1/roadmap")).json() if r["id"] == integ["roadmapItemId"])
    assert shipped["status"] == "shipped"
    got = (await client.get(f"/v1/integrations/{integ['id']}/versions/{version['id']}")).json()
    assert got["learnedAt"] is not None


async def _loop2(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, spec_v3: dict[str, Any]
) -> dict[str, Any]:
    as_user(tenant, Role.ENGINEER, user_id="eng-1")
    r = await client.post("/v1/api-releases", json={"systemId": "sys-esign", "spec": spec_v3})
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


async def test_loop2_reuses_loop1_pattern_only_when_learning_enabled(
    client: httpx.AsyncClient, tenant: Tenant, no_learning_tenant: Tenant, as_user: Actor, spec_v3: dict[str, Any],
    run_loop1: Callable[[Tenant], Awaitable[dict[str, Any]]],
    go_live: Callable[[str, str], Awaitable[service.LearnOutcome]],
) -> None:
    # Loop 1 on both tenants; only the learning tenant writes the pattern back.
    v_a = await run_loop1(tenant)
    v_b = await run_loop1(no_learning_tenant)
    assert (await go_live(tenant.id, v_a["id"])).learned
    off = await go_live(no_learning_tenant.id, v_b["id"])
    assert not off.learned and off.skipped_reason == "learning_disabled"

    # Loop 2: the e-signature app ships v3 on both tenants.
    res_a = await _loop2(client, tenant, as_user, spec_v3)
    res_b = await _loop2(client, no_learning_tenant, as_user, spec_v3)

    for res in (res_a, res_b):
        rel = res["release"]
        assert rel["previousVersion"] == "2.4.0" and rel["version"] == "3.0.0" and rel["status"] == "analyzed"
        assert any(c["kind"] == "deprecated" and c["method"] == "FIELD" for c in rel["changes"])
        assert rel["mcpTools"]
        assert any(i.startswith("Unblocks roadmap: Contract-to-cash automation") for i in rel["impact"])
        dep = {d["field"]: d for d in res["deprecatedFields"]}
        assert dep["Envelope.signer_email"]["replacedBy"] == "Agreement.signers[].email"
        assert dep["Envelope.signer_email"]["usedBy"] == ["Send contract for signature (CRM → e-Signature)"]
        c2c = next(m for m in res["roadmapMatches"] if m["title"] == "Contract-to-cash automation")
        assert c2c["unblocked"] and c2c["fitScore"] >= service.AGENT_DRAFT_MIN_FIT
        drafted = res["drafted"]
        assert drafted is not None and res["agentError"] is None
        assert drafted["integration"]["initiatedBy"] == "agent_from_roadmap"
        assert drafted["integration"]["roadmapItemId"] == c2c["itemId"]
        assert drafted["integration"]["authorId"] is None
        assert drafted["version"]["status"] == "in_review"
        assert "esign.get_signers" in drafted["version"]["allowedTools"]

    with_learning = res_a["drafted"]["version"]
    without = res_b["drafted"]["version"]
    reused = [m for m in with_learning["mappings"] if m["origin"] == "kg_reused"]
    assert len(with_learning["mappings"]) == 9 and len(reused) == 7
    by_target = {m["target"]: m for m in with_learning["mappings"]}
    assert by_target["TaxQuote.jurisdiction"]["source"] == "Account.shipping_address"
    assert by_target["Customer.currency_code"]["transform"] == "to_iso4217()"
    assert by_target["Customer.terms_code"]["source"] == "Agreement.fields.payment_terms"
    assert by_target["Customer.terms_code"]["ruleId"] == "rule-terms-map"
    assert "pat-crm-billing" in with_learning["plan"]["patternsApplied"]
    assert with_learning["patternReused"]
    assert with_learning["plan"]["migrations"] == ["signer_email"]

    # Learning disabled: no reuse, Loop 1's mistakes come back.
    assert sum(m["origin"] == "kg_reused" for m in without["mappings"]) == 0
    assert without["plan"]["patternsApplied"] == []
    assert without["confidence"] < with_learning["confidence"]
    flagged = {m["target"] for m in without["mappings"] if m.get("flag")}
    assert {"Customer.currency_code", "Customer.terms_code"} <= flagged

    # Each tenant's KG reflects only its own history.
    as_user(tenant, Role.VIEWER, user_id="viewer-a")
    assert "pat-crm-billing" in {n["id"] for n in (await _kg(client, "pattern"))["nodes"]}
    as_user(no_learning_tenant, Role.VIEWER, user_id="viewer-b")
    kg_b = await _kg(client)
    assert "pat-crm-billing" not in {n["id"] for n in kg_b["nodes"]}
    assert "bo-agreement" in {n["id"] for n in kg_b["nodes"]}  # release capabilities still registered


async def test_loop2_draft_goes_live_and_reinforces(
    client: httpx.AsyncClient, tenant: Tenant, as_user: Actor, spec_v3: dict[str, Any],
    run_loop1: Callable[[Tenant], Awaitable[dict[str, Any]]],
    go_live: Callable[[str, str], Awaitable[service.LearnOutcome]],
) -> None:
    v1 = await run_loop1(tenant)
    await go_live(tenant.id, v1["id"])
    res = await _loop2(client, tenant, as_user, spec_v3)
    draft = res["drafted"]
    base = f"/v1/integrations/{draft['integration']['id']}/versions/{draft['version']['id']}"
    as_user(tenant, Role.PM, user_id="pm-2")
    assert (await client.post(f"{base}/approvals", json={"role": "pm"})).status_code == 200
    as_user(tenant, Role.ENGINEER, user_id="eng-1")
    r = await client.post(f"{base}/approvals", json={"role": "eng"})
    assert r.status_code == 200 and r.json()["status"] == "approved"

    outcome = await go_live(tenant.id, draft["version"]["id"])
    assert outcome.learned
    assert outcome.report["reinforced"]["pat-crm-billing"] == [0.81, 0.92]
    assert outcome.migrated == ["Send contract for signature (CRM → e-Signature)"]
    as_user(tenant, Role.VIEWER, user_id="viewer-1")
    kg = await _kg(client)
    nodes = {n["id"]: n for n in kg["nodes"]}
    assert nodes["pat-crm-billing"]["confidence"] == 0.92
    edges = {e["id"] for e in kg["edges"]}
    assert "pat-crm-esign-send--IMPLEMENTS--f-agr-signers" in edges
    assert any(e.endswith("--DERIVED_FROM--pat-crm-billing") for e in edges)
