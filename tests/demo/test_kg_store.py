from __future__ import annotations

import json

import pytest

from app.demo.kg_store import KGError, KnowledgeGraph
from app.demo.models import KGNode


def test_seed_matches_frontend_storyline(kg: KnowledgeGraph, seed: dict) -> None:
    assert kg.counts()["nodes"] == 36
    assert kg.counts()["edges"] == 36
    assert {n.id for n in kg.nodes("system")} == {"sys-crm", "sys-billing", "sys-tax", "sys-esign"}
    assert all(n.learned_in == "seed" for n in kg.nodes())
    assert {r["id"] for r in seed["roadmap"]} == {
        "rm-billing-sync", "rm-contract-to-cash", "rm-renewals", "rm-partner", "rm-forecast", "rm-esign-send"
    }
    blocked = next(r for r in seed["roadmap"] if r["id"] == "rm-contract-to-cash")
    assert blocked["status"] == "blocked"


def test_add_update_and_idempotent_edges(kg: KnowledgeGraph) -> None:
    node, created = kg.add_node(KGNode(id="x", label="X", type="process", learned_in="loop1"))
    assert created and node.learned_in == "loop1"
    _, created_again = kg.add_node(KGNode(id="x", label="X2", type="process"))
    assert not created_again
    assert kg.require_node("x").label == "X2"
    assert kg.require_node("x").learned_in == "loop1"  # merge never rewrites provenance
    edge, created = kg.add_edge("x", "TRIGGERS", "proc-contract-signed", "loop2", 0.9)
    assert created and edge.id == "x--TRIGGERS--proc-contract-signed"
    _, created = kg.add_edge("x", "TRIGGERS", "proc-contract-signed", "loop2")
    assert not created
    kg.update_node("x", confidence=0.5)
    assert kg.require_node("x").confidence == 0.5


def test_dangling_edges_and_unknown_nodes_raise(kg: KnowledgeGraph) -> None:
    with pytest.raises(KGError):
        kg.add_edge("nope", "MAPS_TO", "f-acc-name")
    with pytest.raises(KGError):
        kg.update_node("nope", label="x")


def test_neighbours_and_reachability(kg: KnowledgeGraph) -> None:
    fields = {n.id for n in kg.neighbours("bo-account", ["HAS_FIELD"], "out")}
    assert fields == {"f-acc-name", "f-acc-billing", "f-acc-shipping", "f-acc-currency"}
    reach = kg.reachable("rm-contract-to-cash", ["REQUIRES"], "out")
    assert set(reach) == {"rm-contract-to-cash", "proc-contract-signed", "bo-invoice"}
    assert reach["bo-invoice"] == 1
    path = kg.path("f-acc-name", "sys-crm")
    assert path == ["f-acc-name", "bo-account", "sys-crm"]
    assert kg.path("proc-contract-signed", "sys-tax", ["TRIGGERS"]) is None


def test_snapshot_is_json_serialisable_and_clone_is_independent(kg: KnowledgeGraph) -> None:
    snap = json.loads(kg.to_json())
    assert snap["counts"]["patterns"] == 1
    assert any(n["learnedIn"] == "seed" for n in snap["nodes"])
    other = kg.clone()
    other.add_node(KGNode(id="tmp", label="tmp", type="rule"))
    assert not kg.has_node("tmp")
