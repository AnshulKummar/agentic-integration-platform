from __future__ import annotations

from app.demo.kg_store import KnowledgeGraph
from app.demo.models import RoadmapItem
from app.demo.openapi_diff import diff_specs
from app.demo.orchestrator import DemoOrchestrator
from app.demo.roadmap_matcher import match_roadmap
from app.demo.schema_aligner import align, register_capabilities
from app.demo.similarity import field_similarity


def _register_and_align(kg: KnowledgeGraph, spec_v2: dict, spec_v3: dict):  # type: ignore[no-untyped-def]
    diff = diff_specs(spec_v2, spec_v3)
    reg = register_capabilities(kg, spec_v3, diff, "sys-esign")
    alignments, created = align(kg, reg, diff)
    return diff, reg, alignments, created


def test_registration_creates_capability_nodes(kg: KnowledgeGraph, spec_v2: dict, spec_v3: dict) -> None:
    _, reg, _, _ = _register_and_align(kg, spec_v2, spec_v3)
    assert reg.business_objects == ["bo-agreement"]
    assert set(reg.fields) == {"f-agr-signers", "f-agr-amount", "f-agr-terms"}
    assert kg.require_node("f-agr-signers").label == "Agreement.signers[].email"
    assert set(reg.endpoints) == {"ep-esign-v3-create", "ep-esign-v3-fields"}
    assert reg.webhooks == ["ep-esign-v3-webhook"]
    assert kg.require_node("bo-agreement").learned_in == "loop2"


def test_alignment_matches_storyline(kg: KnowledgeGraph, spec_v2: dict, spec_v3: dict) -> None:
    _, _, alignments, _ = _register_and_align(kg, spec_v2, spec_v3)
    accepted = {(a.source, a.edge_type, a.target): a.confidence for a in alignments if a.accepted}
    assert accepted[("bo-agreement", "EQUIVALENT_TO", "bo-contract")] == 0.93
    assert accepted[("f-agr-signers", "EQUIVALENT_TO", "f-contact-email")] == 0.92
    assert ("f-agr-amount", "EQUIVALENT_TO", "f-opp-amount") in accepted
    assert ("f-agr-terms", "EQUIVALENT_TO", "f-opp-terms") in accepted
    assert accepted[("ep-esign-v3-webhook", "TRIGGERS", "proc-contract-signed")] >= 0.9
    assert accepted[("f-agr-signers", "DEPRECATES", "f-env-signer")] == 1.0
    assert all(c >= 0.75 for c in accepted.values())
    assert kg.has_edge("ep-esign-v3-webhook", "TRIGGERS", "proc-contract-signed")
    assert "Now exposed" in (kg.require_node("proc-contract-signed").note or "")


def test_similarity_prefers_same_concept() -> None:
    assert field_similarity("Agreement.fields.payment_terms", "Opportunity.payment_terms") > field_similarity(
        "Agreement.fields.payment_terms", "Customer.terms_code"
    )
    assert field_similarity("Account.name", "Customer.legal_name") >= 0.85


def test_roadmap_scores(kg: KnowledgeGraph, seed: dict, spec_v2: dict, spec_v3: dict) -> None:
    _, reg, _, created = _register_and_align(kg, spec_v2, spec_v3)
    roadmap = [RoadmapItem.model_validate(r) for r in seed["roadmap"]]
    matches = {m.item_id: m for m in match_roadmap(
        kg, roadmap, reg.all_nodes, set(created), "e-Signature App", "Create agreement from template",
        seed["contextStore"]["integrations"],
    )}
    c2c = matches["rm-contract-to-cash"]
    assert 0.85 <= (c2c.fit_score or 0) <= 0.95
    assert c2c.unblocked and not c2c.partial
    renewals = matches["rm-renewals"]
    assert 0.5 <= (renewals.fit_score or 0) <= 0.7 and renewals.partial and not renewals.unblocked
    assert (matches["rm-partner"].fit_score or 1) < 0.1
    assert (matches["rm-forecast"].fit_score or 1) < 0.1
    esign = matches["rm-esign-send"]
    assert esign.impacted and "Envelope.signer_email" in esign.rationale


async def test_orchestrator_unblocks_roadmap(after_release: DemoOrchestrator) -> None:
    o = after_release
    c2c = o.roadmap["rm-contract-to-cash"]
    assert c2c.status == "in_progress" and c2c.blocked_reason is None
    assert o.roadmap["rm-esign-send"].impacted is True
    release = o.releases["rel-esign-v3"]
    assert release.status == "analyzed"
    assert release.version == "v3.0" and release.previous_version == "v2.4"
    assert len(release.impact or []) == 3
