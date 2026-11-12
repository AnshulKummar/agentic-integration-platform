"""In-memory knowledge graph for the demo (stand-in for the Neo4j store).

Pure Python dicts. Nodes and edges carry ``learned_in`` (seed | loop1 | loop2)
so the frontend can colour what each loop taught the platform.
"""

from __future__ import annotations

import copy
import json
import logging
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from app.demo.models import EdgeType, KGEdge, KGNode, LearnedIn, NodeType

logger = logging.getLogger(__name__)

FIXTURES = Path(__file__).parent / "fixtures"
SEED_PATH = FIXTURES / "seed.json"

Direction = Literal["out", "in", "both"]


class KGError(ValueError):
    """Raised for invalid graph operations (unknown node, dangling edge ...)."""


class KnowledgeGraph:
    """A small typed property graph."""

    def __init__(self) -> None:
        self._nodes: dict[str, KGNode] = {}
        self._edges: dict[str, KGEdge] = {}
        self._out: dict[str, set[str]] = {}
        self._in: dict[str, set[str]] = {}

    # ------------------------------------------------------------------ nodes
    def add_node(self, node: KGNode) -> tuple[KGNode, bool]:
        """Insert or merge a node. Returns ``(node, created)``."""
        existing = self._nodes.get(node.id)
        if existing is not None:
            merged = existing.model_copy(
                update={k: v for k, v in node.model_dump().items() if v is not None and k != "learned_in"}
            )
            self._nodes[node.id] = merged
            return merged, False
        self._nodes[node.id] = node
        self._out.setdefault(node.id, set())
        self._in.setdefault(node.id, set())
        return node, True

    def update_node(self, node_id: str, **fields: Any) -> KGNode:
        node = self.get_node(node_id)
        if node is None:
            raise KGError(f"unknown node {node_id!r}")
        updated = node.model_copy(update=fields)
        self._nodes[node_id] = updated
        return updated

    def get_node(self, node_id: str) -> KGNode | None:
        return self._nodes.get(node_id)

    def require_node(self, node_id: str) -> KGNode:
        node = self._nodes.get(node_id)
        if node is None:
            raise KGError(f"unknown node {node_id!r}")
        return node

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def nodes(self, node_type: NodeType | None = None, system: str | None = None) -> list[KGNode]:
        return [
            n
            for n in self._nodes.values()
            if (node_type is None or n.type == node_type) and (system is None or n.system == system)
        ]

    def find_by_label(self, label: str, node_type: NodeType | None = None) -> KGNode | None:
        for n in self._nodes.values():
            if n.label == label and (node_type is None or n.type == node_type):
                return n
        return None

    # ------------------------------------------------------------------ edges
    def add_edge(
        self,
        source: str,
        edge_type: EdgeType,
        target: str,
        learned_in: LearnedIn = "seed",
        confidence: float | None = None,
    ) -> tuple[KGEdge, bool]:
        """Insert an edge (idempotent by id). Returns ``(edge, created)``."""
        if source not in self._nodes:
            raise KGError(f"edge source {source!r} does not exist")
        if target not in self._nodes:
            raise KGError(f"edge target {target!r} does not exist")
        edge_id = KGEdge.make_id(source, edge_type, target)
        existing = self._edges.get(edge_id)
        if existing is not None:
            if confidence is not None and confidence != existing.confidence:
                existing = existing.model_copy(update={"confidence": confidence})
                self._edges[edge_id] = existing
            return existing, False
        edge = KGEdge(
            id=edge_id, source=source, target=target, type=edge_type, learned_in=learned_in, confidence=confidence
        )
        self._edges[edge_id] = edge
        self._out[source].add(edge_id)
        self._in[target].add(edge_id)
        return edge, True

    def has_edge(self, source: str, edge_type: EdgeType, target: str) -> bool:
        return KGEdge.make_id(source, edge_type, target) in self._edges

    def get_edge(self, source: str, edge_type: EdgeType, target: str) -> KGEdge | None:
        return self._edges.get(KGEdge.make_id(source, edge_type, target))

    def edges(
        self,
        edge_type: EdgeType | None = None,
        source: str | None = None,
        target: str | None = None,
    ) -> list[KGEdge]:
        if source is not None:
            pool: Iterable[KGEdge] = (self._edges[e] for e in self._out.get(source, ()))
        elif target is not None:
            pool = (self._edges[e] for e in self._in.get(target, ()))
        else:
            pool = self._edges.values()
        return sorted(
            (
                e
                for e in pool
                if (edge_type is None or e.type == edge_type)
                and (source is None or e.source == source)
                and (target is None or e.target == target)
            ),
            key=lambda e: e.id,
        )

    # -------------------------------------------------------------- traversal
    def neighbours(
        self,
        node_id: str,
        edge_types: Iterable[EdgeType] | None = None,
        direction: Direction = "both",
    ) -> list[KGNode]:
        self.require_node(node_id)
        allowed = set(edge_types) if edge_types is not None else None
        ids: list[str] = []
        if direction in ("out", "both"):
            ids += [
                self._edges[e].target
                for e in sorted(self._out[node_id])
                if allowed is None or self._edges[e].type in allowed
            ]
        if direction in ("in", "both"):
            ids += [
                self._edges[e].source
                for e in sorted(self._in[node_id])
                if allowed is None or self._edges[e].type in allowed
            ]
        seen: dict[str, KGNode] = {}
        for i in ids:
            seen.setdefault(i, self._nodes[i])
        return list(seen.values())

    def reachable(
        self,
        start: str | Iterable[str],
        edge_types: Iterable[EdgeType] | None = None,
        direction: Direction = "out",
        max_depth: int | None = None,
    ) -> dict[str, int]:
        """BFS from ``start``; returns ``{node_id: depth}`` (start nodes at depth 0)."""
        starts = [start] if isinstance(start, str) else list(start)
        for s in starts:
            self.require_node(s)
        types = list(edge_types) if edge_types is not None else None
        depth: dict[str, int] = {s: 0 for s in starts}
        queue: deque[str] = deque(starts)
        while queue:
            cur = queue.popleft()
            if max_depth is not None and depth[cur] >= max_depth:
                continue
            for nb in self.neighbours(cur, types, direction):
                if nb.id not in depth:
                    depth[nb.id] = depth[cur] + 1
                    queue.append(nb.id)
        return depth

    def path(
        self,
        source: str,
        target: str,
        edge_types: Iterable[EdgeType] | None = None,
        direction: Direction = "both",
    ) -> list[str] | None:
        """Shortest path of node ids from ``source`` to ``target`` (or ``None``)."""
        self.require_node(source)
        self.require_node(target)
        types = list(edge_types) if edge_types is not None else None
        prev: dict[str, str | None] = {source: None}
        queue: deque[str] = deque([source])
        while queue:
            cur = queue.popleft()
            if cur == target:
                out: list[str] = []
                node: str | None = cur
                while node is not None:
                    out.append(node)
                    node = prev[node]
                return list(reversed(out))
            for nb in self.neighbours(cur, types, direction):
                if nb.id not in prev:
                    prev[nb.id] = cur
                    queue.append(nb.id)
        return None

    def equivalents(self, node_id: str) -> set[str]:
        """Transitive closure over EQUIVALENT_TO (undirected), including ``node_id``."""
        return set(self.reachable(node_id, ["EQUIVALENT_TO"], "both"))

    def owner_of(self, field_id: str) -> KGNode | None:
        owners = [e.source for e in self.edges("HAS_FIELD", target=field_id)]
        return self._nodes[owners[0]] if owners else None

    # --------------------------------------------------------------- export
    def counts(self) -> dict[str, int]:
        return {
            "nodes": len(self._nodes),
            "edges": len(self._edges),
            "patterns": len(self.nodes("pattern")),
            "rules": len(self.nodes("rule")),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_json() for n in sorted(self._nodes.values(), key=lambda n: n.id)],
            "edges": [e.to_json() for e in sorted(self._edges.values(), key=lambda e: e.id)],
            "counts": self.counts(),
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.snapshot(), indent=indent, ensure_ascii=False)

    def clone(self) -> KnowledgeGraph:
        return copy.deepcopy(self)

    # ----------------------------------------------------------------- seed
    @classmethod
    def from_seed(cls, seed: dict[str, Any]) -> KnowledgeGraph:
        kg = cls()
        for raw in seed["nodes"]:
            kg.add_node(KGNode.model_validate(raw))
        for raw in seed["edges"]:
            edge = KGEdge.model_validate(raw)
            kg.add_edge(edge.source, edge.type, edge.target, edge.learned_in, edge.confidence)
        logger.info("kg.seed_loaded", extra={"counts": kg.counts()})
        return kg


def load_seed(path: Path = SEED_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def load_fixture_json(name: str) -> dict[str, Any]:
    with (FIXTURES / name).open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data
