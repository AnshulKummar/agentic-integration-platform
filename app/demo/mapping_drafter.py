"""Drafts field mappings for an integration's target slots.

For each target field ("slot") the drafter tries, in order:

1. **kg_reused**   a learned pattern mapping / rule for the same target (sources are
   substituted through EQUIVALENT_TO so a Loop 1 rule on ``Opportunity.payment_terms``
   applies to ``Agreement.fields.payment_terms``; mappings to a deprecated field are
   transferred to its replacement via DEPRECATES);
2. **kg_inferred** the most similar candidate source (KG edge evidence or token
   similarity) when unambiguous and format-compatible;
3. **llm_new**     a low-confidence guess with a review flag (ambiguous source,
   format/enum mismatch). Live mode lets Claude phrase the plan; scores stay here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.demo.kg_store import KnowledgeGraph
from app.demo.learning import PatternMatch
from app.demo.models import KGNode, MappingRow
from app.demo.similarity import concept_of, field_similarity, parse_field

logger = logging.getLogger(__name__)

INFER_THRESHOLD = 0.7
AMBIGUITY_MARGIN = 0.05
AMBIGUITY_PENALTY = 0.8
FORMAT_CAP = 0.55
ENUM_CAP = 0.51
AFFINITY_BONUS = 0.06
REUSE_BOOST = 0.5
TRIGGER_PREFERENCE = 0.001


@dataclass(frozen=True)
class Slot:
    target: str
    target_system: str


def _owner(label: str) -> str:
    return label.split(".", 1)[0]


def _reuse_confidence(stored: float, pattern_conf: float) -> float:
    return round(min(0.99, stored + (1 - stored) * REUSE_BOOST * pattern_conf), 2)


class MappingDrafter:
    def __init__(self, kg: KnowledgeGraph, field_catalog: dict[str, dict[str, Any]]) -> None:
        self.kg = kg
        self.catalog = field_catalog

    # ------------------------------------------------------------ candidates
    def candidate_sources(self, source_objects: list[str]) -> list[KGNode]:
        out = [
            n for n in self.kg.nodes("data_field") if _owner(n.label) in source_objects
        ]
        return sorted(out, key=lambda n: n.id)

    def _equivalent_labels(self, label: str) -> set[str]:
        node = self.kg.find_by_label(label, "data_field")
        if node is None:
            return {label}
        return {self.kg.require_node(i).label for i in self.kg.equivalents(node.id)}

    def _prefer(self, label: str, candidates: list[str], trigger_object: str | None) -> str:
        """Pick the equivalent of ``label`` on the trigger object if there is one."""
        eq = self._equivalent_labels(label)
        if trigger_object:
            for c in candidates:
                if c in eq and _owner(c) == trigger_object:
                    return c
        if label in candidates:
            return label
        for c in candidates:
            if c in eq:
                return c
        return label

    def _transform_for(self, source: str, target: str) -> str:
        t_meta, s_meta = self.catalog.get(target, {}), self.catalog.get(source, {})
        if t_meta.get("defaultTransform"):
            return str(t_meta["defaultTransform"])
        if t_meta.get("format") == "region_code" and s_meta.get("format") == "postal_address":
            return "geocode"
        if "[]" in source:
            return "match first signer"
        return "copy"

    # --------------------------------------------------------------- reuse
    def _reuse(
        self,
        slot: Slot,
        candidates: list[str],
        trigger_object: str | None,
        patterns: list[PatternMatch],
    ) -> MappingRow | None:
        deprecated_by = {e.target: e.source for e in self.kg.edges("DEPRECATES")}
        for match in patterns:
            pattern = match.pattern
            pconf = pattern.confidence or 0.5
            rules = {(r.props or {}).get("target"): r for r in match.rules}
            for m in (pattern.props or {}).get("mappings", []):
                src, tgt = m["source"], m["target"]
                rule = rules.get(tgt)
                if tgt == slot.target:
                    source = (rule.props or {}).get("source", src) if rule else src
                    source = self._prefer(source, candidates, trigger_object)
                    transform = (rule.props or {}).get("transform") if rule else m.get("transform", "copy")
                    stored = rule.confidence if rule and rule.confidence else float(m.get("confidence", 0.8))
                    return MappingRow(
                        id="", source=source, target=slot.target, transform=transform, origin="kg_reused",
                        confidence=_reuse_confidence(stored, pconf), rule_id=rule.id if rule else None,
                        pattern_id=pattern.id,
                    )
                # Transfer across a deprecation: (A -> deprecated) becomes (replacement ~ A).
                dep_node = self.kg.find_by_label(tgt, "data_field")
                if src == slot.target and dep_node is not None and dep_node.id in deprecated_by:
                    replacement = self.kg.require_node(deprecated_by[dep_node.id]).label
                    if replacement in candidates:
                        return MappingRow(
                            id="", source=replacement, target=slot.target,
                            transform=self._transform_for(replacement, slot.target), origin="kg_reused",
                            confidence=_reuse_confidence(float(m.get("confidence", 0.8)), pconf),
                            pattern_id=pattern.id,
                        )
        return None

    # --------------------------------------------------------------- infer
    def _edge_evidence(self, source: str, target: str) -> float | None:
        s, t = self.kg.find_by_label(source, "data_field"), self.kg.find_by_label(target, "data_field")
        if s is None or t is None:
            return None
        for et in ("EQUIVALENT_TO", "MAPS_TO"):
            e = self.kg.get_edge(s.id, et, t.id) or self.kg.get_edge(t.id, et, s.id)  # type: ignore[arg-type]
            if e is not None:
                return e.confidence or 0.9
        return None

    def _infer(self, slot: Slot, candidates: list[str], trigger_object: str | None) -> MappingRow:
        target_short = slot.target_system.removeprefix("sys-")
        # Collapse equivalent candidates onto one representative (prefer the trigger object).
        reps: dict[str, str] = {}
        for c in candidates:
            rep = self._prefer(c, candidates, trigger_object)
            reps.setdefault(rep, rep)
        scored: list[tuple[float, float, str]] = []
        for c in reps:
            raw = field_similarity(c, slot.target)
            evidence = self._edge_evidence(c, slot.target)
            if evidence is not None:
                raw = max(raw, evidence)
            rank = raw + (AFFINITY_BONUS if target_short in parse_field(c).leaf else 0.0)
            rank += TRIGGER_PREFERENCE if trigger_object and _owner(c) == trigger_object else 0.0
            scored.append((rank, raw, c))
        scored.sort(key=lambda x: (-x[0], x[2]))
        if not scored or scored[0][1] < 0.3:
            return MappingRow(id="", source="?", target=slot.target, origin="llm_new", confidence=0.3,
                              transform="copy", flag="Low confidence: no source found")
        rank, conf, source = scored[0]
        flag: str | None = None
        if len(scored) > 1 and rank - scored[1][0] < AMBIGUITY_MARGIN:
            conf = round(conf * AMBIGUITY_PENALTY, 2)
            concept = concept_of(parse_field(source).leaf) or "source"
            flag = f"Low confidence: which {concept}?"
        transform = self._transform_for(source, slot.target)
        t_meta, s_meta = self.catalog.get(slot.target, {}), self.catalog.get(source, {})
        constraint = str(t_meta.get("constraint", ""))
        if constraint and s_meta.get("format") != t_meta.get("format") and transform == "copy":
            if constraint.startswith("enum"):
                conf, flag = min(conf, ENUM_CAP), "Low confidence: enum vs free text"
            else:
                conf, flag = min(conf, FORMAT_CAP), "Low confidence: format unknown"
        conf = round(conf, 2)
        origin = "kg_inferred" if conf >= INFER_THRESHOLD else "llm_new"
        return MappingRow(id="", source=source, target=slot.target, transform=transform,
                          origin=origin, confidence=conf, flag=flag)

    # --------------------------------------------------------------- draft
    def draft(
        self,
        slots: list[Slot],
        source_objects: list[str],
        trigger_object: str | None = None,
        patterns: list[PatternMatch] | None = None,
        id_prefix: str = "m",
    ) -> list[MappingRow]:
        candidates = [n.label for n in self.candidate_sources(source_objects)]
        rows: list[MappingRow] = []
        for slot in slots:
            row = self._reuse(slot, candidates, trigger_object, patterns or []) or self._infer(
                slot, candidates, trigger_object
            )
            row.id = f"{id_prefix}-{slot.target.split('.')[-1].replace('_', '-')}-{slot.target.split('.')[0].lower()}"
            rows.append(row)
        logger.info(
            "drafter.drafted",
            extra={"slots": len(slots), "reused": sum(r.origin == "kg_reused" for r in rows)},
        )
        return rows
