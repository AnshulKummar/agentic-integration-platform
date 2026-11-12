"""Turns an intent (plus optional explicit systems/trigger) into a drafting plan.

Mirrors ``DemoOrchestrator._parse_intent`` / ``_slots`` / ``_refresh`` (same
keyword tables, same operation catalog) but works on a tenant's persisted KG and
accepts the caller's explicit ``source_system`` / ``target_system`` / ``trigger``.

Assumption: the operation and field catalogs (``seed.json`` ``operations`` /
``fieldCatalog``) are platform-wide connector metadata, not per-tenant data.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from app.demo.kg_store import KnowledgeGraph, load_seed
from app.demo.mapping_drafter import Slot
from app.demo.models import Integration, KGNode, MappingRow, NodeType, RoadmapItem
from app.demo.orchestrator import SIGNING_WORDS, TRIGGER_KEYWORDS, IntentPlan
from app.demo.similarity import content_words, text_overlap
from app.platform.errors import Conflict, ValidationFailed

#: Text overlap needed to link an intent to a roadmap item automatically (demo value).
ROADMAP_LINK_THRESHOLD = 0.3
DEFAULT_SOURCE_SYSTEM = "sys-crm"
TRIGGER_NODE_TYPES: tuple[NodeType, ...] = ("process", "api_endpoint")


@lru_cache(maxsize=1)
def _catalogs() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    seed = load_seed()
    return seed["operations"], seed["fieldCatalog"]


def op_catalog() -> dict[str, dict[str, Any]]:
    return _catalogs()[0]


def field_catalog() -> dict[str, dict[str, Any]]:
    return _catalogs()[1]


def label(kg: KnowledgeGraph, node_id: str) -> str:
    node = kg.get_node(node_id)
    return node.label if node else node_id


def short_system(kg: KnowledgeGraph, system_id: str) -> str:
    return label(kg, system_id).replace(" (ours)", "")


def require_system(kg: KnowledgeGraph, system_id: str) -> KGNode:
    node = kg.get_node(system_id)
    if node is None or node.type != "system":
        raise ValidationFailed(f"unknown system {system_id!r}", code="unknown_system")
    return node


# ----------------------------------------------------------------- trigger
def _hook_for_process(kg: KnowledgeGraph, process: KGNode) -> str:
    """A system-less process is only usable through an API that TRIGGERS it."""
    if process.system:
        return process.id
    hooks = [e.source for e in kg.edges("TRIGGERS", target=process.id)]
    if not hooks:
        raise Conflict(f'No API exposes "{process.label}" yet; check for API releases first', code="no_trigger")
    return hooks[0]


def _trigger_from_text(kg: KnowledgeGraph, text: str) -> str | None:
    low = text.lower()
    keyword = next((node for kw, node in TRIGGER_KEYWORDS.items() if kw in low), None)
    if keyword is not None and kg.has_node(keyword):
        return keyword
    if any(w in low for w in SIGNING_WORDS) and "contract" in low:
        signed = kg.find_by_label("Contract signed", "process")
        if signed is None:
            raise Conflict('No "Contract signed" process in the knowledge graph', code="no_trigger")
        return _hook_for_process(kg, signed)
    return None


def resolve_trigger(kg: KnowledgeGraph, intent: str, trigger: str | None) -> str:
    """Trigger node id from an explicit trigger (node id, label or phrase) or the intent text."""
    if trigger:
        node = kg.get_node(trigger)
        if node is None:
            node = next(
                (n for t in TRIGGER_NODE_TYPES for n in kg.nodes(t) if n.label.lower() == trigger.strip().lower()),
                None,
            )
        if node is not None:
            if node.type not in TRIGGER_NODE_TYPES:
                raise ValidationFailed(f"{node.id} ({node.type}) cannot trigger an integration", code="bad_trigger")
            return _hook_for_process(kg, node) if node.type == "process" else node.id
        found = _trigger_from_text(kg, trigger)
        if found is None:
            raise ValidationFailed(f"unknown trigger {trigger!r}", code="bad_trigger")
        return found
    found = _trigger_from_text(kg, intent)
    if found is None:
        raise ValidationFailed("Could not find a trigger in the intent (e.g. 'closed-won').", code="unparsed_intent")
    return found


def _trigger_object(kg: KnowledgeGraph, system_id: str) -> str | None:
    """Business object carrying the trigger payload: newest object of the trigger system."""
    objects = sorted(kg.nodes("business_object", system=system_id), key=lambda n: (n.learned_in == "seed", n.id))
    return objects[0].label.split(" (")[0] if objects else None


# -------------------------------------------------------------------- plan
def plan_intent(
    kg: KnowledgeGraph,
    text: str,
    *,
    trigger: str | None = None,
    source_system: str | None = None,
    target_system: str | None = None,
) -> IntentPlan:
    if not kg.nodes("system"):
        raise Conflict("the knowledge graph is empty; bootstrap it first (POST /v1/kg/bootstrap)",
                       code="kg_not_bootstrapped")
    for sid in (source_system, target_system):
        if sid:
            require_system(kg, sid)
    catalog = op_catalog()
    low = text.lower()
    trigger_node = resolve_trigger(kg, text, trigger)
    trig = kg.require_node(trigger_node)
    trigger_system = trig.system or DEFAULT_SOURCE_SYSTEM
    ops = [op for op, meta in catalog.items() if any(kw in low for kw in meta["keywords"])]
    ops = [op for op in ops if kg.has_node(catalog[op]["system"])]
    if "billing.create_invoice" in ops and "billing.create_customer" not in ops and "customer" in low:
        ops.insert(0, "billing.create_customer")
    sources = [trigger_system]
    targets = sorted({catalog[o]["system"] for o in ops}, key=lambda s: (s == "sys-tax", s))
    trigger_object: str | None = None
    source_objects = ["Account", "Opportunity"]
    if trigger_system != DEFAULT_SOURCE_SYSTEM:
        ops.insert(0, "crm.match_contact")
        sources.append(DEFAULT_SOURCE_SYSTEM)
        targets.insert(0, DEFAULT_SOURCE_SYSTEM)
        trigger_object = _trigger_object(kg, trigger_system)
        source_objects = [o for o in [trigger_object] if o] + ["Account", "Opportunity"]
    if not ops or not [t for t in targets if t not in sources]:
        raise ValidationFailed("Could not find a target operation in the intent.", code="unparsed_intent")
    if source_system and source_system not in sources:
        raise ValidationFailed(
            f"the intent's trigger runs in {trigger_system}, not {source_system}", code="system_mismatch"
        )
    if target_system and target_system not in targets:
        raise ValidationFailed(
            f"the intent does not write to {target_system} (targets: {', '.join(targets)})", code="system_mismatch"
        )
    return IntentPlan(text=text, source_systems=sources, target_systems=targets, trigger_node=trigger_node,
                      operations=ops, source_objects=source_objects, trigger_object=trigger_object)


def primary_target(plan: IntentPlan) -> str:
    return next((t for t in plan.target_systems if t not in plan.source_systems and t != "sys-tax"),
                plan.target_systems[0])


def slots(ops: list[str]) -> list[Slot]:
    catalog = op_catalog()
    return [Slot(target=t, target_system=catalog[o]["system"]) for o in ops for t in catalog[o]["slots"]]


def trigger_rows(kg: KnowledgeGraph, trigger_node: str) -> list[MappingRow]:
    """Webhook triggers get a row recording the business-state change they signal (as in loop 2)."""
    node = kg.require_node(trigger_node)
    if node.type != "api_endpoint":
        return []
    edges = kg.edges("TRIGGERS", source=trigger_node)
    if not edges:
        return []
    event = (node.props or {}).get("event", node.label)
    process = kg.require_node(edges[0].target)
    return [
        MappingRow(id="m-trigger", source=f"webhook {event}", target=f"Opportunity.stage = {process.label.title()}",
                   origin="kg_inferred", confidence=round(edges[0].confidence or 0.9, 2))
    ]


def match_roadmap_item(items: list[RoadmapItem], text: str) -> str | None:
    """Best roadmap item for an intent (demo heuristic), or None."""
    candidates = [r for r in items if r.status != "shipped"]
    if not candidates:
        return None
    words = content_words(text)
    best = max(candidates, key=lambda r: text_overlap(r.intent + " " + r.description, words))
    return best.id if text_overlap(best.intent, words) >= ROADMAP_LINK_THRESHOLD else None


def tools_and_endpoints(integ: Integration) -> tuple[list[str], list[str]]:
    """MCP tools the version needs (its allow-list) and the KG endpoints it implements (demo ``_refresh``)."""
    catalog = op_catalog()
    trigger_prefix = next(iter(integ.source_systems), DEFAULT_SOURCE_SYSTEM).removeprefix("sys-")
    trigger_tools = (
        {"crm.get_account"} if integ.trigger_node == "proc-closedwon" or trigger_prefix == "crm"
        else {f"{trigger_prefix}.get_agreement_fields", f"{trigger_prefix}.get_signers"}
    )
    tools = sorted({catalog[o]["mcpTool"] for o in integ.operations} | trigger_tools)
    endpoints = [catalog[o]["endpoint"] for o in integ.operations if catalog[o]["endpoint"]]
    if integ.trigger_node and integ.trigger_node.startswith("ep-"):
        endpoints.insert(0, integ.trigger_node)
    return tools, endpoints


def default_name(kg: KnowledgeGraph, plan: IntentPlan) -> str:
    trig = label(kg, plan.trigger_node)
    trig = re.sub(r"^(Deal |WEBHOOK )", "", trig)
    return f"{trig[:1].upper()}{trig[1:]} → {short_system(kg, primary_target(plan)).split(' /')[0]}"
