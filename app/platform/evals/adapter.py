"""Adapter: run the app.demo pipeline (schema aligner + mapping drafter + KG learning) on an EvalCase.

app.demo was written for four fixed systems; nothing there is modified. The
adaptations, all local to this module:

* Field labels are ``<Object>.<path>`` (the drafter parses owner/path/leaf from
  them). Labels are not unique across vendors, so :class:`CaseGraph` indexes
  nodes by label and resolves a label to the node of the *active* source or
  target system first; KG node ids are ``f::<system>::<label>``.
* :class:`CaseDrafter` restricts candidate sources to this case's source schema
  (the stock drafter takes every KG field whose owner name matches, which would
  leak fields of other vendors that use the same object name) and resolves
  edge evidence by side (source label in the source system, target label in
  the target system).
* The schema aligner runs on hand-built :class:`Registration` objects (the
  stock path builds them from an OpenAPI diff). Every case field is registered,
  including ``id``/``status`` that the OpenAPI path skips as "technical".
* ``Slot.target_system`` is ``sys-<kind>`` (``sys-billing``), matching the demo's
  system ids, so the drafter's "target system name in source leaf" affinity
  bonus behaves as it does in the demo.
* Source fields marked deprecated are still candidates: the drafter has no
  notion of source-side deprecation, and hiding them would overstate it.
* ``trigger_object`` is ``None``: a case has a single source object.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.demo.kg_store import KnowledgeGraph
from app.demo.learning import LearningService, PatternMatch
from app.demo.mapping_drafter import MappingDrafter, Slot
from app.demo.models import HumanEdit, Integration, KGNode, MappingRow, NodeType
from app.demo.openapi_diff import OpenApiDiff
from app.demo.schema_aligner import Registration, apply_alignments, compute_alignments
from app.platform.evals.dataset import TRANSFORMS, EvalCase, EvalField

#: Confidence the demo assigns to a human-corrected row (app.demo.orchestrator.HUMAN_EDIT_CONFIDENCE).
HUMAN_EDIT_CONFIDENCE = 0.97
ABSTAIN_SOURCE = "?"

#: Drafter transform strings -> eval vocabulary (anything unknown is "other").
_DRAFTER_TRANSFORMS = {"copy": None, "match first signer": "first_item", "geocode": "geocode"}


def normalize_transform(transform: str | None) -> str | None:
    if transform is None:
        return None
    t = transform.strip()
    if t in _DRAFTER_TRANSFORMS:
        return _DRAFTER_TRANSFORMS[t]
    return t if t in TRANSFORMS else "other"


def system_node(system: str) -> str:
    return f"sys-{system}"


def label_of(obj: str, path: str) -> str:
    return f"{obj}.{path}"


def _field_node_id(system: str, label: str) -> str:
    return f"f::{system}::{label}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class CaseGraph(KnowledgeGraph):
    """A KnowledgeGraph whose label lookups prefer the active systems (labels collide across vendors)."""

    def __init__(self) -> None:
        super().__init__()
        self._by_label: dict[str, list[str]] = {}
        self.scope: tuple[str, ...] = ()

    def add_node(self, node: KGNode) -> tuple[KGNode, bool]:
        stored, created = super().add_node(node)
        if created:
            self._by_label.setdefault(stored.label, []).append(stored.id)
        return stored, created

    def find_by_label(self, label: str, node_type: NodeType | None = None) -> KGNode | None:
        matches = [self.require_node(i) for i in self._by_label.get(label, ())]
        matches = [n for n in matches if node_type is None or n.type == node_type]
        if not matches:
            return None
        for system in self.scope:
            for n in matches:
                if n.system == system:
                    return n
        return matches[0]

    def field_node(self, system: str, label: str) -> KGNode | None:
        return self.get_node(_field_node_id(system, label))


@dataclass
class RegisteredCase:
    source_system: str
    target_system: str
    source_labels: list[str]
    target_labels: list[str]
    catalog: dict[str, dict[str, Any]] = field(default_factory=dict)


def _catalog_entry(f: EvalField) -> dict[str, Any]:
    entry: dict[str, Any] = {"format": f.format or f.type}
    if f.enum:
        entry["constraint"] = "enum " + "|".join(f.enum)
    elif f.format == "iso4217":
        entry["constraint"] = "pattern ^[A-Z]{3}$"
    return entry


def register_case(kg: CaseGraph, case: EvalCase) -> RegisteredCase:
    """Add the case's systems, objects and fields to ``kg`` and run the schema aligner on them."""
    src_sys, tgt_sys = system_node(case.source.system), system_node(case.target.system)
    reg_fields: list[str] = []
    business_objects: list[str] = []
    out = RegisteredCase(src_sys, tgt_sys, [], [])
    for system, ref, fields, labels in (
        (src_sys, case.source, case.source_fields, out.source_labels),
        (tgt_sys, case.target, case.target_fields, out.target_labels),
    ):
        kg.add_node(KGNode(id=system, label=ref.system, type="system", learned_in="loop1"))
        bo_id = f"bo::{ref.system}::{ref.object}"
        _, created = kg.add_node(KGNode(id=bo_id, label=ref.object, type="business_object", system=system,
                                        learned_in="loop1"))
        kg.add_edge(bo_id, "BELONGS_TO", system, "loop1")
        if created:
            business_objects.append(bo_id)
        for f in fields:
            label = label_of(ref.object, f.path)
            fid = _field_node_id(system, label)
            kg.add_node(KGNode(id=fid, label=label, type="data_field", system=system, learned_in="loop1",
                               note="deprecated" if f.deprecated else None))
            kg.add_edge(bo_id, "HAS_FIELD", fid, "loop1")
            labels.append(label)
            reg_fields.append(fid)
            out.catalog[label] = _catalog_entry(f)
    reg = Registration(system=src_sys, version_tag="v1", business_objects=business_objects, fields=reg_fields)
    diff = OpenApiDiff(title=case.id, from_version="0", to_version="1")
    apply_alignments(kg, compute_alignments(kg, reg, diff), learned_in="loop1")
    return out


class CaseDrafter(MappingDrafter):
    def __init__(self, kg: CaseGraph, reg: RegisteredCase) -> None:
        super().__init__(kg, reg.catalog)
        self.case_kg = kg
        self.reg = reg

    def candidate_sources(self, source_objects: list[str]) -> list[KGNode]:
        nodes = [self.case_kg.field_node(self.reg.source_system, label) for label in self.reg.source_labels]
        return sorted((n for n in nodes if n is not None), key=lambda n: n.id)

    def _edge_evidence(self, source: str, target: str) -> float | None:
        s = self.case_kg.field_node(self.reg.source_system, source)
        t = self.case_kg.field_node(self.reg.target_system, target)
        if s is None or t is None:
            return None
        for et in ("EQUIVALENT_TO", "MAPS_TO"):
            e = self.kg.get_edge(s.id, et, t.id) or self.kg.get_edge(t.id, et, s.id)
            if e is not None:
                return e.confidence or 0.9
        return None


def draft_case(kg: CaseGraph, case: EvalCase, reg: RegisteredCase, patterns: list[PatternMatch]) -> list[MappingRow]:
    kg.scope = (reg.source_system, reg.target_system)
    drafter = CaseDrafter(kg, reg)
    slots = [Slot(target=label, target_system=f"sys-{case.target.kind}") for label in reg.target_labels]
    return drafter.draft(slots, [case.source.object], None, patterns, id_prefix=_slug(case.id))


def row_source_path(case: EvalCase, row: MappingRow) -> str | None:
    """Drafted source label -> case source path (None = abstained). Labels from other vendors stay as-is."""
    if row.source == ABSTAIN_SOURCE or not row.source:
        return None
    prefix = f"{case.source.object}."
    return row.source[len(prefix):] if row.source.startswith(prefix) else row.source


# ------------------------------------------------------------------ learning
def reviewed_integration(case: EvalCase, rows: list[MappingRow]) -> Integration:
    """The integration after a human applied the gold corrections - what KG learning sees.

    Rows whose gold source is null are deleted by the reviewer (LearningService
    has no way to record "this target has no source"). A row counts as edited
    when its source or (normalized) transform differs from gold.
    """
    by_target = {r.target: r for r in rows}
    reviewed: list[MappingRow] = []
    edits: list[HumanEdit] = []
    for g in case.gold:
        label = label_of(case.target.object, g.target)
        row = by_target.get(label)
        if row is None:
            raise ValueError(f"case {case.id}: drafter produced no row for {label}")
        if g.source is None:
            if row.source != ABSTAIN_SOURCE:
                edits.append(HumanEdit(target=label, source=None, transform=None, by="eval-gold"))
            continue
        gold_label = label_of(case.source.object, g.source)
        if row.source == gold_label and normalize_transform(row.transform) == g.transform:
            reviewed.append(row.model_copy())
            continue
        reviewed.append(row.model_copy(update={
            "source": gold_label, "transform": g.transform or "copy", "origin": "human_edit",
            "confidence": HUMAN_EDIT_CONFIDENCE, "flag": None,
        }))
        edits.append(HumanEdit(target=label, source=gold_label, transform=g.transform or "copy", by="eval-gold"))
    src_sys, tgt_sys = system_node(case.source.system), system_node(case.target.system)
    return Integration(
        id=case.id,
        name=f"{case.source.object} -> {case.target.object}",
        loop=1,
        source=case.source.system,
        target=case.target.system,
        trigger="eval replay",
        status="live",
        mappings=reviewed,
        human_edits=len(edits),
        edit_log=edits,
        source_systems=[src_sys],
        target_systems=[tgt_sys],
        patterns_applied=sorted({r.pattern_id for r in rows if r.pattern_id}),
        pattern_id=f"pat-{_slug(case.id)}",
        pattern_label=f"Pattern: {case.source.object} ({case.source.system}) -> {case.target.object} "
        f"({case.target.system})",
    )


class LearningKG:
    """Persistent KG + LearningService across a sequence of cases (``enabled=False`` is the ablation)."""

    def __init__(self, enabled: bool, family_retrieval: bool = True) -> None:
        self.kg = CaseGraph()
        self.learning = LearningService(self.kg, enabled=enabled)
        self.family_retrieval = family_retrieval
        self._family: dict[str, set[str]] = {}

    def retrieval_systems(self, case: EvalCase) -> set[str]:
        """Systems passed to find_applicable.

        The demo only returns patterns whose systems are a subset of the new
        integration's systems, so a pattern learned on (crm-A, billing-X) never
        applies to (crm-B, billing-X). With ``family_retrieval`` the set is widened
        to every previously seen system of the same kinds (crm, billing ...),
        which is the generous reading of "the KG knows billing systems".
        """
        systems = {system_node(case.source.system), system_node(case.target.system)}
        if self.family_retrieval:
            systems |= self._family.get(case.source.kind, set()) | self._family.get(case.target.kind, set())
        return systems

    def draft(self, case: EvalCase) -> list[MappingRow]:
        reg = register_case(self.kg, case)
        patterns = self.learning.find_applicable(self.retrieval_systems(case))
        for ref in (case.source, case.target):
            self._family.setdefault(ref.kind, set()).add(system_node(ref.system))
        return draft_case(self.kg, case, reg, patterns)

    def learn(self, case: EvalCase, rows: Iterable[MappingRow]) -> None:
        if not self.learning.enabled:
            return
        integration = reviewed_integration(case, list(rows))
        self.kg.scope = (system_node(case.source.system), system_node(case.target.system))
        self.learning.learn(integration, "loop1")
