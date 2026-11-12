"""Per-tenant knowledge graph persistence.

``KGRepository.load`` builds an ``app.demo.kg_store.KnowledgeGraph`` from the
tenant's ``KGNodeRecord`` / ``KGEdgeRecord`` rows; the domain logic (drafter,
aligner, learning, roadmap matcher) mutates that in-memory graph; ``save``
upserts what changed back (by ``node_id`` / ``edge_id``). The KG never deletes.

Mapping: node ``props`` <-> ``KGNodeRecord.data``; edge ``confidence`` is stored
in ``KGEdgeRecord.data["confidence"]`` (the table has no confidence column).

Roadmap items are also KG nodes (type ``roadmap``) so learned patterns can
``IMPLEMENTS`` them. Seed items keep their seed key as node id (``rm-...``); items
created over the API get ``rm-<record id>``. Either way the node's
``props.roadmapItemId`` points at the ``RoadmapItemRecord``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.kg_store import FIXTURES, KGError, KnowledgeGraph, load_seed
from app.demo.mcp_tools import generate_tools
from app.demo.models import KGEdge, KGNode
from app.platform.audit import record_audit
from app.platform.core_models import ApiReleaseRecord, KGEdgeRecord, KGNodeRecord, RoadmapItemRecord
from app.platform.db import new_id
from app.platform.errors import Conflict, PlatformError
from app.platform.integrations.models import ContextIntegrationRecord

log = logging.getLogger(__name__)

ROADMAP_ITEM_KEY = "roadmapItemId"


class KGIntegrityError(PlatformError):
    """Stored KG rows cannot be loaded (dangling edge, unknown type). Server-side fault."""

    status = 500
    code = "kg_integrity"


@dataclass(frozen=True)
class KGSaveStats:
    nodes_created: int = 0
    nodes_updated: int = 0
    edges_created: int = 0
    edges_updated: int = 0


def _node_values(node: KGNode) -> dict[str, Any]:
    return {
        "label": node.label,
        "type": node.type,
        "system": node.system,
        "learned_in": node.learned_in,
        "confidence": node.confidence,
        "note": node.note,
        "data": dict(node.props or {}),
    }


def _edge_values(edge: KGEdge) -> dict[str, Any]:
    return {
        "source": edge.source,
        "target": edge.target,
        "type": edge.type,
        "learned_in": edge.learned_in,
        "data": {"confidence": edge.confidence} if edge.confidence is not None else {},
    }


class KGRepository:
    """Load/save one tenant's KG. Create one per unit of work (it caches the rows it loaded)."""

    def __init__(self, session: AsyncSession, tenant_id: str) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self._nodes: dict[str, KGNodeRecord] | None = None
        self._edges: dict[str, KGEdgeRecord] | None = None

    async def _fetch(self) -> tuple[dict[str, KGNodeRecord], dict[str, KGEdgeRecord]]:
        if self._nodes is None or self._edges is None:
            nodes = await self.session.execute(
                select(KGNodeRecord).where(KGNodeRecord.tenant_id == self.tenant_id).order_by(KGNodeRecord.node_id)
            )
            edges = await self.session.execute(
                select(KGEdgeRecord).where(KGEdgeRecord.tenant_id == self.tenant_id).order_by(KGEdgeRecord.edge_id)
            )
            self._nodes = {r.node_id: r for r in nodes.scalars()}
            self._edges = {r.edge_id: r for r in edges.scalars()}
        return self._nodes, self._edges

    async def load(self) -> KnowledgeGraph:
        nodes, edges = await self._fetch()
        kg = KnowledgeGraph()
        try:
            for nrec in nodes.values():
                # model_validate checks the stored type / learned_in against the domain literals.
                kg.add_node(
                    KGNode.model_validate(
                        {
                            "id": nrec.node_id, "label": nrec.label, "type": nrec.type, "system": nrec.system,
                            "learned_in": nrec.learned_in, "confidence": nrec.confidence, "note": nrec.note,
                            "props": dict(nrec.data) if nrec.data else None,
                        }
                    )
                )
            for erec in edges.values():
                raw_conf = (erec.data or {}).get("confidence")
                edge = KGEdge.model_validate(
                    {
                        "id": erec.edge_id, "source": erec.source, "target": erec.target, "type": erec.type,
                        "learned_in": erec.learned_in,
                        "confidence": float(raw_conf) if isinstance(raw_conf, int | float) else None,
                    }
                )
                kg.add_edge(edge.source, edge.type, edge.target, edge.learned_in, edge.confidence)
        except (KGError, ValidationError) as exc:
            log.error("kg.load_failed", extra={"tenant": self.tenant_id, "error": str(exc)})
            raise KGIntegrityError(f"stored knowledge graph is inconsistent: {exc}") from exc
        return kg

    async def save(self, kg: KnowledgeGraph) -> KGSaveStats:
        """Upsert every node/edge of ``kg`` that is new or changed, then flush."""
        nodes, edges = await self._fetch()
        nc = nu = ec = eu = 0
        for node in kg.nodes():
            values = _node_values(node)
            rec = nodes.get(node.id)
            if rec is None:
                rec = KGNodeRecord(id=new_id(), tenant_id=self.tenant_id, node_id=node.id, **values)
                self.session.add(rec)
                nodes[node.id] = rec
                nc += 1
            elif any(getattr(rec, k) != v for k, v in values.items()):
                for k, v in values.items():
                    setattr(rec, k, v)
                nu += 1
        for edge in kg.edges():
            values = _edge_values(edge)
            erec = edges.get(edge.id)
            if erec is None:
                erec = KGEdgeRecord(id=new_id(), tenant_id=self.tenant_id, edge_id=edge.id, **values)
                self.session.add(erec)
                edges[edge.id] = erec
                ec += 1
            elif any(getattr(erec, k) != v for k, v in values.items()):
                for k, v in values.items():
                    setattr(erec, k, v)
                eu += 1
        try:
            await self.session.flush()
        except IntegrityError as exc:
            log.warning("kg.save_conflict", extra={"tenant": self.tenant_id})
            raise Conflict("the knowledge graph was changed concurrently; retry", code="kg_conflict") from exc
        return KGSaveStats(nodes_created=nc, nodes_updated=nu, edges_created=ec, edges_updated=eu)


# ------------------------------------------------------------------ roadmap <-> KG
def roadmap_node_index(kg: KnowledgeGraph) -> dict[str, str]:
    """``{roadmap record id: KG node id}`` for roadmap nodes linked to a record."""
    out: dict[str, str] = {}
    for node in kg.nodes("roadmap"):
        rid = (node.props or {}).get(ROADMAP_ITEM_KEY)
        if isinstance(rid, str):
            out[rid] = node.id
    return out


def link_roadmap_node(kg: KnowledgeGraph, record: RoadmapItemRecord, node_id: str | None = None) -> str:
    """Create/refresh the KG node for a roadmap record and its REQUIRES edges. Returns the node id.

    Unknown ``requires`` ids raise ``KGError`` (callers validate first).
    """
    nid = node_id or roadmap_node_index(kg).get(record.id) or f"rm-{record.id}"
    existing = kg.get_node(nid)
    if existing is None:
        kg.add_node(
            KGNode(id=nid, label=f"Roadmap: {record.title}", type="roadmap", props={ROADMAP_ITEM_KEY: record.id})
        )
    else:
        kg.update_node(nid, props={**(existing.props or {}), ROADMAP_ITEM_KEY: record.id})
    for req in record.requires:
        kg.add_edge(nid, "REQUIRES", req)
    return nid


# ------------------------------------------------------------------ bootstrap
@dataclass(frozen=True)
class BootstrapResult:
    nodes_added: int
    edges_added: int
    roadmap_items_added: int
    context_integrations_added: int
    baseline_releases_added: int
    counts: dict[str, int]


def _baseline_fixture(system_id: str, version: str) -> Path | None:
    parts = version.split(".")
    if len(parts) < 2:
        return None
    path = FIXTURES / f"{system_id.removeprefix('sys-')}_openapi_v{parts[0]}_{parts[1]}.json"
    return path if path.is_file() else None


async def bootstrap_tenant_kg(session: AsyncSession, tenant_id: str, actor_id: str | None) -> BootstrapResult:
    """Seed a tenant from ``app/demo/fixtures/seed.json``. Idempotent.

    Adds missing seed nodes/edges (never overwrites what the tenant has learned),
    the seed roadmap (linked to KG roadmap nodes), the context store's external
    integrations, and a baseline API release per ``knownApiVersions`` entry whose
    spec fixture exists (so the next release can be diffed against it).
    Flushes; the caller commits.
    """
    seed = load_seed()
    repo = KGRepository(session, tenant_id)
    kg = await repo.load()

    nodes_added = edges_added = 0
    for raw in seed["nodes"]:
        node = KGNode.model_validate(raw)
        if not kg.has_node(node.id):
            kg.add_node(node)
            nodes_added += 1
    for raw in seed["edges"]:
        edge = KGEdge.model_validate(raw)
        if not kg.has_edge(edge.source, edge.type, edge.target):
            kg.add_edge(edge.source, edge.type, edge.target, edge.learned_in, edge.confidence)
            edges_added += 1

    # Roadmap: one record per seed item, linked through the KG node's props.
    records = {
        r.id: r
        for r in (
            await session.execute(select(RoadmapItemRecord).where(RoadmapItemRecord.tenant_id == tenant_id))
        ).scalars()
    }
    linked = {nid: rid for rid, nid in roadmap_node_index(kg).items() if rid in records}
    seed_key_to_record: dict[str, str] = {}
    roadmap_added = 0
    for raw in seed["roadmap"]:
        key = str(raw["id"])
        if key in linked:
            seed_key_to_record[key] = linked[key]
            continue
        rec = RoadmapItemRecord(
            id=new_id(),
            tenant_id=tenant_id,
            title=raw["title"],
            description=raw.get("description", ""),
            intent=raw.get("intent", ""),
            column=raw.get("column", "later"),
            status=raw.get("status", "planned"),
            quarter=raw.get("quarter", ""),
            owner=raw.get("owner", ""),
            requires=[r for r in raw.get("requires", []) if kg.has_node(r)],
            blocked_reason=raw.get("blockedReason"),
        )
        session.add(rec)
        link_roadmap_node(kg, rec, node_id=key)
        seed_key_to_record[key] = rec.id
        roadmap_added += 1

    # Context store: integrations running outside the platform.
    existing_ctx = {
        r.external_id
        for r in (
            await session.execute(
                select(ContextIntegrationRecord).where(ContextIntegrationRecord.tenant_id == tenant_id)
            )
        ).scalars()
    }
    ctx_added = 0
    for raw in seed.get("contextStore", {}).get("integrations", []):
        if raw["id"] in existing_ctx:
            continue
        session.add(
            ContextIntegrationRecord(
                tenant_id=tenant_id,
                external_id=raw["id"],
                name=raw["name"],
                roadmap_item_id=seed_key_to_record.get(raw.get("roadmapId") or ""),
                pattern_id=raw.get("pattern"),
                status=raw.get("status", "live"),
                uses=list(raw.get("uses", [])),
                source_system=raw.get("sourceSystem", ""),
                target_system=raw.get("targetSystem", ""),
            )
        )
        ctx_added += 1

    # Baseline API releases (the spec each watched system currently runs).
    releases_added = 0
    for system_id, version in seed.get("knownApiVersions", {}).items():
        has_release = (
            await session.execute(
                select(ApiReleaseRecord.id)
                .where(ApiReleaseRecord.tenant_id == tenant_id, ApiReleaseRecord.system_id == system_id)
                .limit(1)
            )
        ).first()
        if has_release is not None:
            continue
        fixture = _baseline_fixture(system_id, str(version))
        if fixture is None:
            log.warning("kg.bootstrap_no_baseline_spec", extra={"system": system_id, "version": version})
            continue
        spec: dict[str, Any] = json.loads(fixture.read_text(encoding="utf-8"))
        system_node = kg.get_node(system_id)
        session.add(
            ApiReleaseRecord(
                tenant_id=tenant_id,
                system_id=system_id,
                app=system_node.label if system_node else system_id,
                version=str(version),
                previous_version=None,
                status="baseline",
                spec=spec,
                changes=[],
                mcp_tools=[t.name for t in generate_tools(spec, system_id.removeprefix("sys-"))],
                impact=[],
            )
        )
        releases_added += 1

    await repo.save(kg)
    result = BootstrapResult(
        nodes_added=nodes_added,
        edges_added=edges_added,
        roadmap_items_added=roadmap_added,
        context_integrations_added=ctx_added,
        baseline_releases_added=releases_added,
        counts=kg.counts(),
    )
    record_audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="kg.bootstrap",
        target_type="kg",
        target_id=tenant_id,
        detail={
            "nodesAdded": nodes_added,
            "edgesAdded": edges_added,
            "roadmapItemsAdded": roadmap_added,
            "contextIntegrationsAdded": ctx_added,
            "baselineReleasesAdded": releases_added,
        },
    )
    await session.flush()
    log.info("kg.bootstrapped", extra={"tenant": tenant_id, "nodes_added": nodes_added, "edges_added": edges_added})
    return result
