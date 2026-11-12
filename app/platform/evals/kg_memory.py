"""KG of reviewed field mappings, used as retrieval context for the live LLM.

The demo's KG reuses whole *patterns* between near-identical systems. This is
the alternative the evals pointed to: after each integration is reviewed, every
corrected field decision is written into a ``KnowledgeGraph`` as typed facts,
and the next integration's prompt gets the relevant facts as context.

    data_field(src system/object/path) --MAPS_TO--> data_field(tgt system/object/path)
        edge confidence = 1.0 (human-reviewed); transform kept on the target node props
    data_field(tgt ...) with props.no_source = True   (reviewer: nothing populates it)

Retrieval for a new case (never includes the case itself - it is written only in
``learn`` after the case was scored):
  1. same integration type (source kind -> target kind), e.g. crm -> billing;
  2. otherwise any reviewed fact whose target is the same system family.
Ranked: same target system first, then same target object, then most recent.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.demo.kg_store import KnowledgeGraph
from app.demo.models import KGNode
from app.platform.evals.dataset import EvalCase

#: Upper bound on facts put in one prompt (keeps the context cost predictable).
MAX_CONTEXT_FACTS = 120


@dataclass(frozen=True)
class ReviewedFact:
    source_kind: str
    target_kind: str
    source_system: str
    target_system: str
    target_object: str
    source: str | None  # "system.Object.path", None = reviewed as "no source"
    target: str  # "system.Object.path"
    transform: str | None
    seq: int  # learning order (higher = more recent)
    target_path: str = ""
    source_path: str | None = None

    def render(self) -> str:
        if self.source is None:
            return f"- {self.target} <- (no source field; reviewed)"
        transform = f" [transform: {self.transform}]" if self.transform else ""
        return f"- {self.target} <- {self.source}{transform}"


def _field_id(system: str, obj: str, path: str) -> str:
    return f"fld:{system}:{obj}.{path}"


def _fact(target: KGNode, no_source_kind: str | None, source: KGNode | None) -> ReviewedFact:
    """A reviewed decision for ``target``: mapped from ``source``, or "no source" when it is None."""
    props = target.props or {}
    sp = (source.props or {}) if source is not None else {}
    transform = props.get("transform")
    return ReviewedFact(
        source_kind=str(sp.get("kind", "")) if source is not None else (no_source_kind or ""),
        target_kind=str(props["kind"]),
        source_system=(source.system or "") if source is not None else "",
        target_system=target.system or "",
        target_object=str(props["object"]),
        source=f"{source.system}.{source.label}" if source is not None else None,
        target=f"{target.system}.{target.label}",
        transform=str(transform) if transform and source is not None else None,
        seq=int(props["seq"]),
        target_path=str(props["path"]),
        source_path=(str(sp.get("path", "")) or None) if source is not None else None,
    )


class KGMappingMemory:
    def __init__(self) -> None:
        self.kg = KnowledgeGraph()
        self._seq = 0

    def __len__(self) -> int:
        return len(self.facts())

    # ------------------------------------------------------------- write
    def _field(self, system: str, kind: str, obj: str, path: str) -> str:
        node_id = _field_id(system, obj, path)
        if self.kg.has_node(node_id):
            return node_id  # never overwrite props (a field can be a source in one case, a target in another)
        self.kg.add_node(
            KGNode(
                id=node_id, label=f"{obj}.{path}", type="data_field", system=system, learned_in="loop2",
                props={"kind": kind, "object": obj, "path": path},
            )
        )
        return node_id

    def learn(self, case: EvalCase) -> int:
        """Record the reviewed (gold) decisions of a finished integration. Returns facts written."""
        self._seq += 1
        written = 0
        for gold in case.gold:
            tgt = self._field(case.target.system, case.target.kind, case.target.object, gold.target)
            node = self.kg.require_node(tgt)
            props = dict(node.props or {})
            props.update({"seq": self._seq, "no_source": gold.source is None, "transform": gold.transform,
                          "source_kind": case.source.kind})
            self.kg.update_node(tgt, props=props)
            if gold.source is not None:
                src = self._field(case.source.system, case.source.kind, case.source.object, gold.source)
                self.kg.add_edge(src, "MAPS_TO", tgt, learned_in="loop2", confidence=1.0)
            written += 1
        return written

    # -------------------------------------------------------------- read
    def facts(self) -> list[ReviewedFact]:
        out: list[ReviewedFact] = []
        for node in self.kg.nodes("data_field"):
            props = node.props or {}
            if "seq" not in props:
                continue  # a source-side field, not a reviewed target
            incoming = self.kg.edges("MAPS_TO", target=node.id)
            if props.get("no_source") or not incoming:
                out.append(_fact(node, str(props.get("source_kind", "")), None))
                continue
            for edge in incoming:
                out.append(_fact(node, None, self.kg.require_node(edge.source)))
        return out

    def retrieve(self, case: EvalCase, limit: int = MAX_CONTEXT_FACTS) -> list[ReviewedFact]:
        same_type = [f for f in self.facts()
                     if f.source_kind == case.source.kind and f.target_kind == case.target.kind]
        pool = same_type or [f for f in self.facts() if f.target_kind == case.target.kind]
        pool.sort(key=lambda f: (f.target_system != case.target.system, f.target_object != case.target.object,
                                 -f.seq, f.target))
        return pool[:limit]


def render_context(facts: list[ReviewedFact]) -> str:
    if not facts:
        return ""
    return (
        "Knowledge graph: field mappings that reviewers approved in earlier integrations of the same kind. "
        "Systems and field names may differ from this integration; use them as evidence about meaning, "
        "conventions and transforms, not as answers to copy.\n" + "\n".join(f.render() for f in facts)
    )
