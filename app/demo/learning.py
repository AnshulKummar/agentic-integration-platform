"""KG learning: patterns and rules from completed integrations.

* ``learn`` writes a pattern node (with its mappings), one rule per human edit,
  MAPS_TO / EQUIVALENT_TO / CONNECTS_TO / IMPLEMENTS edges, and DERIVED_FROM
  links to any pattern it reused.
* ``find_applicable`` retrieves patterns whose systems are a subset of the new
  integration's systems (the "system types" the original PatternService TODO
  never extracted), with their rules.
* ``reinforce`` raises a reused pattern's confidence when reuse needed no edits.
* ``enabled=False`` is the ablation: no write-back and no retrieval.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import Field

from app.demo.kg_store import KnowledgeGraph
from app.demo.models import CamelModel, Integration, KGNode, LearnedIn, MappingRow

logger = logging.getLogger(__name__)

REINFORCE_RATE = 0.6
EDIT_PENALTY = 0.24
MIN_SHARED_FIELD_MAPPINGS = 2

RULE_TEMPLATES: dict[str, tuple[str, str]] = {
    "to_iso4217()": ("rule-iso-currency", "Rule: currency → ISO 4217"),
    "TERMS_MAP lookup": ("rule-terms-map", "Rule: payment terms → billing terms code"),
    "geocode(ship-to)": ("rule-tax-shipto", "Rule: tax jurisdiction = ship-to address"),
}

SYSTEM_SHORT = {"sys-crm": "CRM", "sys-billing": "Billing", "sys-tax": "Tax", "sys-esign": "e-Signature"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def integration_confidence(mappings: list[MappingRow], human_edits: int) -> float:
    """Mean mapping confidence, discounted by the share of mappings humans had to fix."""
    if not mappings:
        return 0.0
    mean = sum(m.confidence for m in mappings) / len(mappings)
    edit_ratio = min(1.0, human_edits / len(mappings))
    return round(mean * (1 - EDIT_PENALTY * edit_ratio), 2)


def pattern_identity(integration: Integration) -> tuple[str, str]:
    """Deterministic pattern id + label from the systems and operations involved."""
    flow = integration.source_systems + [
        s for s in integration.target_systems if s not in integration.source_systems and s != "sys-tax"
    ]
    pid = "pat-" + "-".join(s.removeprefix("sys-") for s in flow)
    label = "Pattern: " + " → ".join(SYSTEM_SHORT.get(s, s) for s in flow)
    if "billing.create_customer" in integration.operations:
        label += " customer sync"
    if "tax.calculate" in integration.operations:
        label += " with tax"
    if "billing.create_invoice" in integration.operations:
        label += " + first invoice"
    return pid, label


class PatternMatch(CamelModel):
    pattern: KGNode
    rules: list[KGNode] = Field(default_factory=list)
    score: float


class LearningReport(CamelModel):
    skipped: bool = False
    pattern_id: str | None = None
    rules: list[str] = Field(default_factory=list)
    nodes_added: list[str] = Field(default_factory=list)
    edges_added: list[str] = Field(default_factory=list)
    mappings_learned: int = 0
    reinforced: dict[str, list[float]] = Field(default_factory=dict)

    def summary(self) -> str:
        if self.skipped:
            return "KG learning disabled: no write-back"
        parts = [f"+{1 if self.pattern_id else 0} pattern", f"+{len(self.rules)} rules from human edits",
                 f"+{self.mappings_learned} mappings"]
        parts += [f'"{pid}" confidence {a:.2f} → {b:.2f}' for pid, (a, b) in self.reinforced.items()]
        return ", ".join(parts)


class LearningService:
    def __init__(self, kg: KnowledgeGraph, enabled: bool = True) -> None:
        self.kg = kg
        self.enabled = enabled

    # -------------------------------------------------------------- retrieval
    def find_applicable(self, systems: set[str]) -> list[PatternMatch]:
        if not self.enabled:
            logger.info("learning.retrieval_skipped", extra={"reason": "learning disabled"})
            return []
        matches: list[PatternMatch] = []
        for pattern in self.kg.nodes("pattern"):
            props = pattern.props or {}
            p_systems = set(props.get("sourceSystems", [])) | set(props.get("targetSystems", []))
            if not p_systems or not p_systems <= systems:
                continue
            rules = [self.kg.require_node(e.source) for e in self.kg.edges("APPLIES_TO", target=pattern.id)
                     if self.kg.require_node(e.source).type == "rule"]
            score = round(len(p_systems) / max(1, len(systems)) * (pattern.confidence or 0.5), 3)
            matches.append(PatternMatch(pattern=pattern, rules=rules, score=score))
        return sorted(matches, key=lambda m: -m.score)

    # ---------------------------------------------------------- reinforcement
    def reinforce(self, pattern_id: str, edits_on_reused: int, learned_in: LearnedIn) -> tuple[float, float]:
        node = self.kg.require_node(pattern_id)
        before = node.confidence or 0.5
        if edits_on_reused == 0:
            after = round(before + REINFORCE_RATE * (1 - before), 2)
        else:
            after = round(max(0.1, before - 0.1 * edits_on_reused), 2)
        props = dict(node.props or {})
        props["uses"] = int(props.get("uses", 0)) + 1
        note = f"Reused with {edits_on_reused} human edits ({learned_in})"
        self.kg.update_node(pattern_id, confidence=after, props=props, note=note)
        return before, after

    # -------------------------------------------------------------- write-back
    def _field_node(self, label: str) -> KGNode | None:
        return self.kg.find_by_label(label, "data_field")

    def learn(self, integration: Integration, learned_in: LearnedIn) -> LearningReport:
        if not self.enabled:
            return LearningReport(skipped=True)
        if integration.status != "live":
            raise ValueError(f"only live integrations are learned from ({integration.id} is {integration.status})")

        report = LearningReport()
        kg = self.kg

        def node(n: KGNode) -> None:
            _, created = kg.add_node(n)
            if created:
                report.nodes_added.append(n.id)

        def edge(src: str, et: Any, tgt: str, conf: float | None = None) -> None:
            e, created = kg.add_edge(src, et, tgt, learned_in, conf)
            if created:
                report.edges_added.append(e.id)

        pid = integration.pattern_id or pattern_identity(integration)[0]
        label = integration.pattern_label or pattern_identity(integration)[1]
        confidence = integration_confidence(integration.mappings, integration.human_edits)

        # Reinforce reused patterns first (their confidence must reflect this reuse).
        reused_ids = [p for p in integration.patterns_applied if kg.has_node(p)]
        for rp in reused_ids:
            edits_on_reused = sum(
                1 for e in integration.edit_log
                if any(m.target == e.target and m.pattern_id == rp for m in integration.mappings)
            )
            before, after = self.reinforce(rp, edits_on_reused, learned_in)
            report.reinforced[rp] = [before, after]

        rules: list[str] = []
        for row in integration.mappings:
            if row.origin != "human_edit":
                continue
            rid, rlabel = RULE_TEMPLATES.get(row.transform or "", (f"rule-{_slug(row.target)}", ""))
            rlabel = rlabel or f"Rule: {row.target} ← {row.transform or 'copy'}({row.source})"
            node(
                KGNode(
                    id=rid, label=rlabel, type="rule", learned_in=learned_in, confidence=row.confidence,
                    props={"target": row.target, "source": row.source, "transform": row.transform,
                           "learnedFrom": integration.id},
                )
            )
            rules.append(rid)
        report.rules = rules

        mappings_prop = [
            {"source": m.source, "target": m.target, "transform": m.transform or "copy", "confidence": m.confidence,
             "ruleId": RULE_TEMPLATES.get(m.transform or "", (None, ""))[0] if m.origin == "human_edit" else m.rule_id}
            for m in integration.mappings
            if not m.source.startswith("webhook ")
        ]
        node(
            KGNode(
                id=pid, label=label, type="pattern", learned_in=learned_in, confidence=confidence,
                props={
                    "sourceSystems": integration.source_systems,
                    "targetSystems": integration.target_systems,
                    "operations": integration.operations,
                    "trigger": integration.trigger,
                    "mappings": mappings_prop,
                    "rules": rules,
                    "humanEdits": integration.human_edits,
                    "learnedFrom": integration.id,
                    "uses": 1,
                },
            )
        )
        report.pattern_id = pid
        for rid in rules:
            edge(rid, "APPLIES_TO", pid)
        for rp in reused_ids:
            if rp != pid:
                edge(pid, "DERIVED_FROM", rp)

        # Field mappings and object equivalences.
        owner_pairs: dict[tuple[str, str], int] = {}
        for m in integration.mappings:
            src, tgt = self._field_node(m.source), self._field_node(m.target)
            if src is None or tgt is None or src.system == tgt.system:
                continue
            if kg.has_edge(src.id, "EQUIVALENT_TO", tgt.id) or kg.has_edge(tgt.id, "EQUIVALENT_TO", src.id):
                continue
            created_before = len(report.edges_added)
            edge(src.id, "MAPS_TO", tgt.id, m.confidence)
            report.mappings_learned += len(report.edges_added) - created_before
            so, to = kg.owner_of(src.id), kg.owner_of(tgt.id)
            if so and to:
                owner_pairs[(so.id, to.id)] = owner_pairs.get((so.id, to.id), 0) + 1
            if src.system and tgt.system:
                edge(src.system, "CONNECTS_TO", tgt.system)
        for (so, to), count in sorted(owner_pairs.items()):
            if count >= MIN_SHARED_FIELD_MAPPINGS:
                edge(so, "EQUIVALENT_TO", to)

        # Endpoints and roadmap items this pattern implements.
        for ep in integration.endpoints:
            if kg.has_node(ep):
                edge(pid, "IMPLEMENTS", ep)
        if integration.roadmap_id and kg.has_node(integration.roadmap_id):
            edge(pid, "IMPLEMENTS", integration.roadmap_id)

        logger.info("learning.learned", extra={"integration": integration.id, "summary": report.summary()})
        return report

    def apply_migration(self, pattern_id: str, deprecated_id: str, replacement_id: str, learned_in: LearnedIn) -> None:
        """Record that a pattern was migrated off a deprecated field."""
        if not self.enabled:
            return
        node = self.kg.require_node(pattern_id)
        props = dict(node.props or {})
        dep_label = self.kg.require_node(deprecated_id).label
        rep_label = self.kg.require_node(replacement_id).label
        props["mappings"] = [
            {**m, "target": rep_label if m.get("target") == dep_label else m.get("target")}
            for m in props.get("mappings", [])
        ]
        self.kg.update_node(pattern_id, props=props)
        self.kg.add_edge(pattern_id, "IMPLEMENTS", replacement_id, learned_in)
