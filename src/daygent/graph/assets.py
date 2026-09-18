"""Asset-level lineage projection for display.

The detailed graph keeps Python functions, modules, and Spark temp views because
impact analysis needs them. A developer reading a pipeline does not: they want
`bronze.orders → silver.orders → gold.orders`, with the implementation hops
available on demand.

This module contracts every chain of implementation nodes between two data
assets into a single edge and records the traversed path in `metadata["via"]`.
It is display-only and never mutates or replaces the detailed graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from daygent.graph.traversal import adjacency
from daygent.models import Confidence, Edge, EdgeType, Graph, Node, NodeType

# Node kinds a developer thinks of as data/AI assets. Everything else is
# implementation detail that gets contracted away.
ASSET_TYPES: frozenset[str] = frozenset(
    {
        NodeType.SQL_TABLE,
        NodeType.DBT_MODEL,
        NodeType.DBT_SOURCE,
        NodeType.PIPELINE_DATASET,
        NodeType.API_ROUTE,
        NodeType.EXTERNAL_SYSTEM,
        NodeType.LLM,
        NodeType.EMBEDDING_MODEL,
        NodeType.VECTOR_STORE,
        NodeType.VECTOR_COLLECTION,
        NodeType.DJANGO_MODEL,
        NodeType.SQLALCHEMY_MODEL,
        NodeType.LANGGRAPH_NODE,
        NodeType.AGENT,
    }
)

# Contraction bounds. Real pipelines have short implementation chains; these
# caps keep a pathological graph from exploding the projection.
MAX_VIA_HOPS = 12
MAX_PATHS_PER_PAIR = 3
MAX_VISITS_PER_NODE = 8


@dataclass
class AssetProjection:
    """Contracted asset lineage plus the detailed paths behind each edge."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def edge_index(self) -> dict[tuple[str, str], Edge]:
        """Map (source, target) to the contracted edge."""
        return {(edge.source, edge.target): edge for edge in self.edges}


def is_asset_type(node_type: str) -> bool:
    """Return True for node kinds shown in the default asset lineage view."""
    return node_type in ASSET_TYPES


def _weakest(confidences: list[Confidence]) -> Confidence:
    """A contracted edge is only as trustworthy as its weakest hop."""
    if not confidences:
        return Confidence.LOW
    order = [Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH]
    return min(confidences, key=order.index)


def project_asset_graph(graph: Graph) -> AssetProjection:
    """Contract implementation chains between assets into direct edges."""
    index = graph.node_index()
    assets = [node for node in graph.nodes if is_asset_type(node.type)]
    if not assets:
        return AssetProjection()

    asset_ids = {node.id for node in assets}
    fwd = adjacency(graph, forward=True)
    edge_index: dict[tuple[str, str], list[Edge]] = {}
    for edge in graph.edges:
        edge_index.setdefault((edge.source, edge.target), []).append(edge)

    paths: dict[tuple[str, str], list[tuple[str, ...]]] = {}
    for start in sorted(asset_ids):
        for target, via in _contracted_targets(start, asset_ids, fwd):
            bucket = paths.setdefault((start, target), [])
            if via in bucket or len(bucket) >= MAX_PATHS_PER_PAIR:
                continue
            bucket.append(via)

    edges: list[Edge] = []
    for (source, target), variants in sorted(paths.items()):
        variants = sorted(variants, key=lambda item: (len(item), item))
        primary = variants[0]
        confidences: list[Confidence] = []
        hops = [source, *primary, target]
        for left, right in zip(hops, hops[1:], strict=False):
            for edge in edge_index.get((left, right), []):
                confidences.append(edge.confidence)
        metadata: dict[str, object] = {
            "projection": "asset",
            "via": list(primary),
            "hops": len(primary),
        }
        if len(variants) > 1:
            metadata["alternate_paths"] = [list(item) for item in variants[1:]]
        if primary:
            metadata["via_labels"] = [
                index[node_id].name for node_id in primary if node_id in index
            ]
        edges.append(
            Edge(
                source=source,
                target=target,
                type=EdgeType.DEPENDS_ON,
                confidence=_weakest(confidences),
                evidence="asset projection" if primary else "direct",
                metadata=metadata,
            )
        )
    return AssetProjection(nodes=sorted(assets, key=lambda node: node.id), edges=edges)


def _contracted_targets(
    start: str,
    asset_ids: set[str],
    fwd: dict[str, list[str]],
) -> list[tuple[str, tuple[str, ...]]]:
    """Walk forward from `start` through implementation nodes to other assets.

    A function that already writes or feeds an asset is a stage boundary: we
    record those assets and stop. Continuing through a further function call
    would skip the produced table and draw a shortcut such as a dbt mart
    jumping straight to a vector collection because the indexer happens to
    call the snapshot job.
    """
    found: list[tuple[str, tuple[str, ...]]] = []
    visits: dict[str, int] = {}
    queue: list[tuple[str, tuple[str, ...]]] = [(start, ())]
    while queue:
        current, via = queue.pop(0)
        neighbors = [
            nxt for nxt in fwd.get(current, ()) if nxt != start and nxt not in via
        ]
        asset_next = [nxt for nxt in neighbors if nxt in asset_ids]
        impl_next = [nxt for nxt in neighbors if nxt not in asset_ids]
        for nxt in asset_next:
            found.append((nxt, via))
        if current != start and asset_next:
            continue
        for nxt in impl_next:
            if len(via) >= MAX_VIA_HOPS:
                continue
            seen = visits.get(nxt, 0)
            if seen >= MAX_VISITS_PER_NODE:
                continue
            visits[nxt] = seen + 1
            queue.append((nxt, (*via, nxt)))
    return found
