"""Roadmap matcher: scores stored roadmap intents against a new capability.

Each roadmap item lists the KG nodes it REQUIRES. For every requirement we ask the
KG how the new capability reaches it:

* ``unblocks``   the capability TRIGGERS a process that had no API before   (1.00)
* ``triggers``   the capability TRIGGERS an already-available process       (0.90)
* ``equivalent`` a capability object/field is EQUIVALENT_TO the requirement (0.65)
* ``satisfied``  not touched, but already available elsewhere               (0.92)
* ``missing``    not touched and not available                              (0.00)

fit = 0.92 * requirement score + 0.08 * intent-text overlap with the capability's
vocabulary. If the capability touches none of an item's requirements the
requirement score is damped to 10%. Shipped items are checked for impact instead
(live integrations that use a deprecated field).
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import Field

from app.demo.kg_store import KnowledgeGraph
from app.demo.models import CamelModel, KGNode, RoadmapItem
from app.demo.similarity import content_words, text_overlap

logger = logging.getLogger(__name__)

Reach = Literal["unblocks", "triggers", "equivalent", "satisfied", "missing"]
CONTRIBUTION: dict[str, float] = {
    "unblocks": 1.0, "triggers": 0.9, "equivalent": 0.65, "satisfied": 0.92, "missing": 0.0,
}
REQ_WEIGHT = 0.92
INTENT_WEIGHT = 0.08
UNTOUCHED_DAMPING = 0.1
PARTIAL_RANGE = (0.3, 0.8)


class RequirementReach(CamelModel):
    node: str
    label: str
    reach: Reach
    via: str | None = None


class RoadmapMatch(CamelModel):
    item_id: str
    title: str
    fit_score: float | None = None
    rationale: str
    requirements: list[RequirementReach] = Field(default_factory=list)
    unblocked: bool = False
    partial: bool = False
    impacted: bool = False


def is_available(kg: KnowledgeGraph, node_id: str, ignore_edges: set[str] | None = None) -> bool:
    """A process is available when it belongs to a system or some API TRIGGERS it."""
    node = kg.require_node(node_id)
    if node.type != "process":
        return True
    if node.system:
        return True
    ignore = ignore_edges or set()
    return any(e.id not in ignore for e in kg.edges("TRIGGERS", target=node_id))


def capability_vocabulary(kg: KnowledgeGraph, capability: list[str], extra_text: str = "") -> set[str]:
    words: set[str] = set(content_words(extra_text))
    for nid in capability:
        node = kg.require_node(nid)
        words |= content_words(node.label)
        words |= content_words(str((node.props or {}).get("summary", "")))
        for nb in kg.neighbours(nid, ["EQUIVALENT_TO", "TRIGGERS"]):
            words |= content_words(nb.label)
    return words - {"v3", "v2", "post", "get", "id", "webhook"}


def _reach(
    kg: KnowledgeGraph, req: str, capability: set[str], new_edges: set[str]
) -> tuple[Reach, str | None]:
    for e in kg.edges("TRIGGERS", target=req):
        if e.source in capability:
            was_available = is_available(kg, req, ignore_edges=new_edges)
            return ("triggers" if was_available else "unblocks"), kg.require_node(e.source).label
    for eq in kg.equivalents(req) - {req}:
        if eq in capability:
            return "equivalent", kg.require_node(eq).label
    return ("satisfied" if is_available(kg, req) else "missing"), None


def _rationale(item: RoadmapItem, reqs: list[RequirementReach], system_label: str) -> str:
    reqs = [r.model_copy(update={"via": (r.via or "").removeprefix("WEBHOOK ") or None}) for r in reqs]
    by = {r.reach: [x for x in reqs if x.reach == r.reach] for r in reqs}
    parts: list[str] = []
    for r in by.get("unblocks", []):
        parts.append(f'{r.via} supplies the missing "{r.label}" trigger')
    for r in by.get("triggers", []):
        parts.append(f'{r.via} now also signals "{r.label}"')
    for r in by.get("equivalent", []):
        parts.append(f"{r.via} ≡ {r.label}: {r.label.lower()}s can be created and read via the new API, "
                     "but no blocked step is unlocked and the remaining logic is not covered")
    for r in by.get("satisfied", []):
        parts.append(f"{r.label} already available")
    for r in by.get("missing", []):
        parts.append(f"{r.label} still missing")
    if not reqs:
        return f"No KG requirements reachable from the {system_label} capability; unrelated."
    return "; ".join(parts) + "."


def match_roadmap(
    kg: KnowledgeGraph,
    roadmap: list[RoadmapItem],
    capability: list[str],
    new_edges: set[str],
    system_label: str,
    capability_text: str = "",
    context_integrations: list[dict[str, Any]] | None = None,
) -> list[RoadmapMatch]:
    """Score every roadmap item; flag shipped items impacted by deprecations."""
    cap = set(capability)
    vocab = capability_vocabulary(kg, capability, capability_text)
    deprecated = {e.target: e.source for e in kg.edges("DEPRECATES")}
    matches: list[RoadmapMatch] = []

    for item in roadmap:
        if item.status == "shipped":
            impact = _impact(kg, item, deprecated, context_integrations or [])
            if impact:
                matches.append(RoadmapMatch(item_id=item.id, title=item.title, rationale=impact, impacted=True))
            continue
        reqs = [
            RequirementReach(node=r, label=kg.require_node(r).label, reach=reach, via=via)
            for r in item.requires
            if kg.has_node(r)
            for reach, via in [_reach(kg, r, cap, new_edges)]
        ]
        touched = any(r.reach in ("unblocks", "triggers", "equivalent") for r in reqs)
        req_score = sum(CONTRIBUTION[r.reach] for r in reqs) / len(reqs) if reqs else 0.0
        if not touched:
            req_score *= UNTOUCHED_DAMPING
        intent = text_overlap(f"{item.intent} {item.description}", vocab)
        fit = round(REQ_WEIGHT * req_score + INTENT_WEIGHT * intent, 2)
        unblocked = (
            item.status == "blocked"
            and any(r.reach == "unblocks" for r in reqs)
            and all(r.reach != "missing" for r in reqs)
        )
        rationale = _rationale(item, reqs, system_label) if touched else (
            f"Unrelated to the {system_label} capability." if not reqs or fit < 0.2 else _rationale(item, reqs, system_label)
        )
        matches.append(
            RoadmapMatch(
                item_id=item.id, title=item.title, fit_score=fit, rationale=rationale, requirements=reqs,
                unblocked=unblocked, partial=PARTIAL_RANGE[0] <= fit < PARTIAL_RANGE[1],
            )
        )
    logger.info("roadmap.matched", extra={"scores": {m.item_id: m.fit_score for m in matches}})
    return matches


def _impact(
    kg: KnowledgeGraph, item: RoadmapItem, deprecated: dict[str, str], context_integrations: list[dict[str, Any]]
) -> str | None:
    for ci in context_integrations:
        if ci.get("roadmapId") != item.id or ci.get("status") != "live":
            continue
        for used in ci.get("uses", []):
            if used in deprecated:
                old: KGNode = kg.require_node(used)
                new = kg.require_node(deprecated[used])
                return (
                    f"Impacted: live integration \"{ci['name']}\" uses deprecated {old.label} "
                    f"(replaced by {new.label}). Migration required before the old API is sunset."
                )
    return None
