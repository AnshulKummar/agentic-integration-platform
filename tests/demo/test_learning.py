from __future__ import annotations

import pytest

from app.demo.learning import LearningService
from app.demo.orchestrator import DemoOrchestrator


async def test_loop1_learns_pattern_and_rules(after_loop1: DemoOrchestrator) -> None:
    o = after_loop1
    int1 = o.integrations["int-1"]
    assert int1.status == "live" and int1.human_edits == 3 and int1.turns == 2
    pattern = o.kg.require_node("pat-crm-billing")
    assert pattern.learned_in == "loop1" and pattern.confidence == 0.81
    rules = {e.source for e in o.kg.edges("APPLIES_TO", target="pat-crm-billing")}
    assert rules == {"rule-iso-currency", "rule-terms-map", "rule-tax-shipto"}
    assert o.kg.require_node("rule-tax-shipto").props["source"] == "Account.shipping_address"
    assert o.kg.has_edge("bo-account", "EQUIVALENT_TO", "bo-customer")
    assert o.kg.has_edge("f-acc-shipping", "MAPS_TO", "f-tax-jur")
    assert o.kg.has_edge("pat-crm-billing", "IMPLEMENTS", "rm-billing-sync")
    assert o.roadmap["rm-billing-sync"].status == "shipped"


async def _loop2(learning: bool) -> DemoOrchestrator:
    o = DemoOrchestrator(learning_enabled=learning)
    await o.run_loop1()
    await o.check_api_releases()
    await o.draft_opportunity("rm-contract-to-cash")
    return o


async def test_loop2_reuses_learned_mappings() -> None:
    o = await _loop2(learning=True)
    int2 = o.integrations["int-2"]
    assert int2.initiated_by == "agent_from_roadmap" and int2.turns == 0
    assert len(int2.mappings) == 9
    reused = [m for m in int2.mappings if m.origin == "kg_reused"]
    assert len(reused) == 7
    by_target = {m.target: m for m in int2.mappings}
    # All three Loop 1 human corrections come back as reused rules.
    assert by_target["TaxQuote.jurisdiction"].source == "Account.shipping_address"
    assert by_target["Customer.currency_code"].transform == "to_iso4217()"
    assert by_target["Customer.terms_code"].source == "Agreement.fields.payment_terms"
    assert by_target["Customer.terms_code"].rule_id == "rule-terms-map"
    # Deprecation-aware transfer from the seed "send for signature" pattern.
    assert by_target["Contact.email"].pattern_id == "pat-crm-esign-send"
    assert all(t.passed for t in int2.tests)
    assert "pat-crm-billing" in int2.patterns_applied


async def test_ablation_no_learning_repeats_loop1_mistakes() -> None:
    o = await _loop2(learning=False)
    assert not o.kg.has_node("pat-crm-billing")  # nothing written back in Loop 1
    int2 = o.integrations["int-2"]
    assert sum(m.origin == "kg_reused" for m in int2.mappings) == 0
    failing = {t.target_field for t in int2.tests if not t.passed}
    assert failing == {"Customer.currency_code", "Customer.terms_code", "TaxQuote.jurisdiction"}
    await o.review(int2.id)
    assert int2.human_edits == 3
    assert all(t.passed for t in int2.tests)


async def test_reinforcement_after_zero_edit_reuse() -> None:
    o = await _loop2(learning=True)
    int2 = o.integrations["int-2"]
    await o.review(int2.id, [])
    o.approve(int2.id, "pm", "Priya (PM)", "ok")
    o.approve(int2.id, "eng", "Marco (Eng)", "ok")
    await o.deploy(int2.id)
    assert int2.status == "live" and int2.human_edits == 0
    assert o.kg.require_node("pat-crm-billing").confidence == 0.92
    derived = [e for e in o.kg.edges("DERIVED_FROM") if e.target == "pat-crm-billing"]
    assert len(derived) == 1 and o.kg.require_node(derived[0].source).learned_in == "loop2"
    assert o.kg.has_edge("pat-crm-esign-send", "IMPLEMENTS", "f-agr-signers")  # migration recorded


async def test_ablation_skips_retrieval_and_write_back(after_loop1: DemoOrchestrator) -> None:
    service = LearningService(after_loop1.kg, enabled=False)
    assert service.find_applicable({"sys-crm", "sys-billing", "sys-tax"}) == []
    report = service.learn(after_loop1.integrations["int-1"], "loop1")
    assert report.skipped and report.summary() == "KG learning disabled: no write-back"


async def test_learning_rejects_non_live_integration(orch: DemoOrchestrator) -> None:
    integ = await orch.loop1_intent()
    with pytest.raises(ValueError):
        orch.learning.learn(integ, "loop1")
