"""Register a new API's capabilities in the KG and align them with existing nodes.

``register_capabilities`` turns an OpenAPI diff into KG nodes (business objects,
data fields, endpoints, webhooks). ``align`` then links them to what the KG
already knows:

* business objects  -> EQUIVALENT_TO (synonym table + structural evidence)
* data fields       -> EQUIVALENT_TO (leaf/owner token similarity)
* webhooks          -> TRIGGERS a process (word synonyms: completed ~ signed)
* x-replaced-by     -> replacement DEPRECATES deprecated field

Deterministic by default; ``refine_with_llm`` optionally lets Claude veto or
adjust confidences when an API key is configured.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

from pydantic import Field

from app.demo.kg_store import KnowledgeGraph
from app.demo.models import CamelModel, EdgeType, KGNode, LearnedIn
from app.demo.openapi_diff import OpenApiDiff, deref, operations
from app.demo.similarity import field_similarity, object_similarity, phrase_similarity

logger = logging.getLogger(__name__)

ALIGN_THRESHOLD = 0.75
TECHNICAL_FIELDS = {
    "id", "status", "external_ref", "role", "template_id", "created_at", "updated_at", "completed_at", "event",
}
ID_ABBREVIATIONS = {"payment_terms": "terms"}


class Alignment(CamelModel):
    source: str
    target: str
    edge_type: EdgeType
    confidence: float
    reason: str
    source_label: str = ""
    target_label: str = ""
    accepted: bool = True


class Registration(CamelModel):
    system: str
    version_tag: str
    business_objects: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    endpoints: list[str] = Field(default_factory=list)
    webhooks: list[str] = Field(default_factory=list)
    nodes_added: list[str] = Field(default_factory=list)
    edges_added: list[str] = Field(default_factory=list)

    @property
    def all_nodes(self) -> list[str]:
        return self.business_objects + self.fields + self.endpoints + self.webhooks


def _short_system(system_id: str) -> str:
    return system_id.removeprefix("sys-")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _endpoint_node_id(system_id: str, tag: str, method: str, path: str) -> str:
    static = [seg for seg in path.strip("/").split("/") if seg and not seg.startswith("{") and seg != tag]
    last = static[-1] if static else "root"
    if method == "POST" and not path.rstrip("/").endswith("}") and len(static) == 1:
        last = "create"
    return f"ep-{_short_system(system_id)}-{tag}-{_slug(last)}"


def _business_schemas(spec: dict[str, Any], diff: OpenApiDiff) -> list[str]:
    """New schemas that represent business objects (have an id, not a Create/Event payload)."""
    schemas = spec.get("components", {}).get("schemas", {}) or {}
    out: list[str] = []
    for name in diff.added_schemas:
        schema = schemas.get(name, {})
        props = schema.get("properties", {}) or {}
        if schema.get("x-business-object") or (
            "id" in props and not name.endswith(("Create", "Event", "Fields", "Request", "Response"))
        ):
            out.append(name)
    return out


def _leaf_fields(spec: dict[str, Any], schema: dict[str, Any], prefix: str, depth: int = 0) -> list[tuple[str, str]]:
    """Flatten business-relevant leaf properties: ``[(label, first_segment)]``."""
    if depth > 3:
        return []
    schema = deref(spec, schema)
    out: list[tuple[str, str]] = []
    for prop, raw in (schema.get("properties", {}) or {}).items():
        if prop in TECHNICAL_FIELDS or raw.get("readOnly"):
            continue
        sub = deref(spec, raw)
        if sub.get("type") == "array" and isinstance(sub.get("items"), dict):
            items = deref(spec, sub["items"])
            for label, _ in _leaf_fields(spec, items, f"{prefix}.{prop}[]", depth + 1):
                out.append((label, prop))
        elif sub.get("type") == "object" or "properties" in sub:
            for label, _ in _leaf_fields(spec, sub, f"{prefix}.{prop}", depth + 1):
                leaf = label.rsplit(".", 1)[-1]
                out.append((label, ID_ABBREVIATIONS.get(leaf, leaf)))
        else:
            out.append((f"{prefix}.{prop}", ID_ABBREVIATIONS.get(prop, prop)))
    return out


def register_capabilities(
    kg: KnowledgeGraph,
    spec: dict[str, Any],
    diff: OpenApiDiff,
    system: str,
    learned_in: LearnedIn = "loop2",
) -> Registration:
    """Add the release's new objects, fields, endpoints and webhooks to the KG."""
    major = diff.to_version.split(".")[0] or "0"
    tag = f"v{major}"
    reg = Registration(system=system, version_tag=tag)
    schemas = spec.get("components", {}).get("schemas", {}) or {}

    def add(node: KGNode) -> None:
        _, created = kg.add_node(node)
        if created:
            reg.nodes_added.append(node.id)

    def link(src: str, et: EdgeType, tgt: str) -> None:
        edge, created = kg.add_edge(src, et, tgt, learned_in)
        if created:
            reg.edges_added.append(edge.id)

    for name in _business_schemas(spec, diff):
        bo_id = f"bo-{_slug(name)}"
        add(KGNode(id=bo_id, label=f"{name} ({tag})", type="business_object", system=system, learned_in=learned_in))
        link(bo_id, "BELONGS_TO", system)
        reg.business_objects.append(bo_id)
        abbrev = _slug(name)[:3]
        for label, key in _leaf_fields(spec, schemas[name], name):
            f_id = f"f-{abbrev}-{_slug(key)}"
            add(KGNode(id=f_id, label=label, type="data_field", system=system, learned_in=learned_in))
            link(bo_id, "HAS_FIELD", f_id)
            reg.fields.append(f_id)

    ops = operations(spec)
    for ep in diff.added_endpoints:
        ep_id = _endpoint_node_id(system, tag, ep.method, ep.path)
        op = ops[ep.key][2]
        add(
            KGNode(
                id=ep_id,
                label=f"{ep.method} {ep.path}",
                type="api_endpoint",
                system=system,
                learned_in=learned_in,
                props={"operationId": op.get("operationId"), "summary": ep.summary},
            )
        )
        link(ep_id, "BELONGS_TO", system)
        reg.endpoints.append(ep_id)

    for i, wh in enumerate(diff.added_webhooks):
        wh_id = f"ep-{_short_system(system)}-{tag}-webhook" + (f"-{i + 1}" if i else "")
        add(
            KGNode(
                id=wh_id,
                label=f"WEBHOOK {wh.name}",
                type="api_endpoint",
                system=system,
                learned_in=learned_in,
                props={"event": wh.name, "summary": wh.summary},
            )
        )
        link(wh_id, "BELONGS_TO", system)
        reg.webhooks.append(wh_id)

    logger.info(
        "aligner.registered",
        extra={"system": system, "nodes": len(reg.nodes_added), "edges": len(reg.edges_added)},
    )
    return reg


def _systems_connected(kg: KnowledgeGraph, a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return kg.has_edge(a, "CONNECTS_TO", b) or kg.has_edge(b, "CONNECTS_TO", a)


def _object_name(label: str) -> str:
    return label.split(" (")[0].strip().lower()


def compute_alignments(kg: KnowledgeGraph, reg: Registration, diff: OpenApiDiff) -> list[Alignment]:
    """Score candidate alignments without writing them."""
    out: list[Alignment] = []

    # Business objects.
    for bo_id in reg.business_objects:
        bo = kg.require_node(bo_id)
        best: Alignment | None = None
        for cand in kg.nodes("business_object"):
            if cand.id == bo_id or cand.system == bo.system:
                continue
            syn = object_similarity(_object_name(bo.label), _object_name(cand.label))
            if syn == 0:
                continue
            structural = 1.0 if _systems_connected(kg, bo.system, cand.system) else 0.0
            score = round(0.85 * syn + 0.15 * structural, 2)
            if best is None or score > best.confidence:
                best = Alignment(
                    source=bo_id, target=cand.id, edge_type="EQUIVALENT_TO", confidence=score,
                    reason=f"synonym {_object_name(bo.label)}~{_object_name(cand.label)} ({syn:.2f})"
                    + ("; systems already connected" if structural else ""),
                )
        if best is not None:
            out.append(best)

    # Data fields.
    for f_id in reg.fields:
        field = kg.require_node(f_id)
        best = None
        for cand in kg.nodes("data_field"):
            if cand.system == field.system or cand.id == f_id:
                continue
            score = round(field_similarity(field.label, cand.label), 2)
            if best is None or score > best.confidence:
                best = Alignment(
                    source=f_id, target=cand.id, edge_type="EQUIVALENT_TO", confidence=score,
                    reason="leaf/owner token similarity",
                )
        if best is not None:
            out.append(best)

    # Webhooks -> processes.
    for wh_id in reg.webhooks:
        event = (kg.require_node(wh_id).props or {}).get("event", "")
        best = None
        for proc in kg.nodes("process"):
            score = round(phrase_similarity(event, proc.label), 2)
            if best is None or score > best.confidence:
                best = Alignment(
                    source=wh_id, target=proc.id, edge_type="TRIGGERS", confidence=score,
                    reason=f'event "{event}" ~ process "{proc.label}"',
                )
        if best is not None:
            out.append(best)

    # Deprecations declared by the spec (x-replaced-by).
    for dep in diff.deprecated_fields:
        old = kg.find_by_label(f"{dep.schema_name}.{dep.field}", "data_field")
        if old is None or not dep.replaced_by:
            continue
        replacement = next(
            (kg.require_node(f) for f in reg.fields if kg.require_node(f).label.endswith(dep.replaced_by)), None
        )
        if replacement is None:
            logger.warning("aligner.replacement_not_found", extra={"replaced_by": dep.replaced_by})
            continue
        out.append(
            Alignment(
                source=replacement.id, target=old.id, edge_type="DEPRECATES", confidence=1.0,
                reason=f"spec marks {dep.path} deprecated, x-replaced-by {dep.replaced_by}",
            )
        )

    for a in out:
        a.source_label = kg.require_node(a.source).label
        a.target_label = kg.require_node(a.target).label
        a.accepted = a.confidence >= ALIGN_THRESHOLD
    return out


def apply_alignments(
    kg: KnowledgeGraph, alignments: list[Alignment], learned_in: LearnedIn = "loop2"
) -> list[str]:
    """Write accepted alignments as edges; returns ids of edges created."""
    created_ids: list[str] = []
    for a in alignments:
        if not a.accepted:
            continue
        edge, created = kg.add_edge(a.source, a.edge_type, a.target, learned_in, a.confidence)
        if created:
            created_ids.append(edge.id)
        if a.edge_type == "TRIGGERS":
            src = kg.require_node(a.source)
            system = kg.get_node(src.system or "")
            kg.update_node(
                a.target,
                note=f"Now exposed by {system.label if system else src.system} webhook {src.label.split(' ', 1)[-1]}",
            )
    return created_ids


def align(
    kg: KnowledgeGraph, reg: Registration, diff: OpenApiDiff, learned_in: LearnedIn = "loop2"
) -> tuple[list[Alignment], list[str]]:
    alignments = compute_alignments(kg, reg, diff)
    return alignments, apply_alignments(kg, alignments, learned_in)


# ------------------------------------------------------------- LLM refinement
class JsonLLM(Protocol):
    async def json_call(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


REFINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "accept": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "accept", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


async def refine_with_llm(alignments: list[Alignment], llm: JsonLLM) -> list[Alignment]:
    """Ask the LLM to accept/reject each heuristic alignment and adjust confidence."""
    listing = "\n".join(
        f"{i}. {a.source_label} -[{a.edge_type}]-> {a.target_label} (heuristic {a.confidence:.2f}: {a.reason})"
        for i, a in enumerate(alignments)
    )
    result = await llm.json_call(
        "You are the knowledge-graph agent of an integration platform. Judge proposed semantic alignments "
        "between a partner's new API and our existing schema. Be conservative.",
        f"Proposed alignments:\n{listing}\n\nReturn one verdict per index.",
        REFINE_SCHEMA,
    )
    refined = [a.model_copy() for a in alignments]
    for verdict in result.get("verdicts", []):
        idx = verdict.get("index")
        if not isinstance(idx, int) or not 0 <= idx < len(refined):
            logger.warning("aligner.llm_bad_index", extra={"index": idx})
            continue
        a = refined[idx]
        a.accepted = bool(verdict["accept"]) and float(verdict["confidence"]) >= ALIGN_THRESHOLD
        a.confidence = round(max(0.0, min(1.0, float(verdict["confidence"]))), 2)
        a.reason = f"{a.reason}; LLM: {verdict['reason']}"
    return refined
