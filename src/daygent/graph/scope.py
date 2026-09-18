"""Project a candidate graph onto data/AI lineage.

Parsers may emit a broad candidate graph. This step keeps domain anchors and
the connector nodes required to join them. It reasons from node types and
edges only, so a later TypeScript parser can reuse it without changes.

Scan broadly, graph narrowly. Never reverse edge direction.
"""

from __future__ import annotations

from collections.abc import Iterable

from daygent.graph.traversal import adjacency
from daygent.models import Graph, Node, NodeType, ScanMetadata

# Domain anchors always stay in the Daygent graph.
ANCHOR_TYPES: frozenset[str] = frozenset(
    {
        NodeType.SQL_TABLE,
        NodeType.DBT_MODEL,
        NodeType.DBT_SOURCE,
        NodeType.LANGGRAPH_NODE,
        NodeType.LLM,
        NodeType.EMBEDDING_MODEL,
        NodeType.VECTOR_STORE,
        NodeType.VECTOR_COLLECTION,
        NodeType.EXTERNAL_SYSTEM,
        NodeType.PIPELINE_DATASET,
        NodeType.DJANGO_MODEL,
        NodeType.SQLALCHEMY_MODEL,
    }
)

# API/service nodes and pipeline aliases stay when they sit on a relevant
# lineage flow. A Spark temp view is an alias, so it is only meaningful when it
# actually connects two kept nodes.
BRIDGE_TYPES: frozenset[str] = frozenset(
    {
        NodeType.API_ROUTE,
        NodeType.API_CLIENT,
        NodeType.SERVICE,
        NodeType.AGENT,
        NodeType.TEMP_VIEW,
    }
)

# Language-agnostic code kinds. python_function matches; so would go_function.
_CODE_SUFFIXES: tuple[str, ...] = (
    "_function",
    "_module",
    "_class",
    "_method",
    "_file",
)


def is_anchor_type(node_type: str) -> bool:
    """Return True for data/AI domain anchors."""
    return node_type in ANCHOR_TYPES


def is_code_type(node_type: str) -> bool:
    """Return True for language-level code nodes, not domain assets."""
    lowered = node_type.strip().lower()
    return lowered.endswith(_CODE_SUFFIXES)


def is_bridge_type(node_type: str) -> bool:
    """Return True for non-code, non-anchor nodes that may join a lineage path.

    v0.1 bridges include api_route / api_client / service / agent. Unknown
    future types are treated the same so the engine stays language-agnostic.
    """
    if is_anchor_type(node_type) or is_code_type(node_type):
        return False
    return True


def project_lineage_graph(graph: Graph) -> Graph:
    """Return a lineage projection of `graph` without mutating the candidate.

    Keeps anchors, nodes adjacent to anchors, directed connector paths between
    kept nodes, and bridge nodes on those flows. Drops unrelated code.
    """
    index = graph.node_index()
    if not index:
        return graph.sorted()

    fwd = adjacency(graph, forward=True)
    rev = adjacency(graph, forward=False)
    undirected = _undirected(fwd, rev)

    kept = {node.id for node in graph.nodes if is_anchor_type(node.type)}
    kept |= _neighbors_of(kept, undirected)

    while True:
        before = len(kept)
        kept |= _directed_connectors(kept, fwd, rev)
        kept |= _relevant_handlers(graph.nodes, kept, undirected, fwd, rev)
        kept |= _relevant_bridges(graph.nodes, kept, undirected, fwd)
        if len(kept) == before:
            break

    nodes = [index[node_id] for node_id in kept if node_id in index]
    edges = [
        edge
        for edge in graph.edges
        if edge.source in kept and edge.target in kept
    ]
    return Graph(
        version=graph.version,
        project=graph.project.model_copy(deep=True),
        nodes=nodes,
        edges=edges,
        scan=ScanMetadata(
            timestamp=graph.scan.timestamp,
            files_scanned=graph.scan.files_scanned,
            warnings=list(graph.scan.warnings),
        ),
    ).sorted()


def _undirected(
    fwd: dict[str, list[str]],
    rev: dict[str, list[str]],
) -> dict[str, set[str]]:
    """Merge directed adjacency into undirected neighbor sets."""
    neighbors: dict[str, set[str]] = {}
    for src, dests in fwd.items():
        neighbors.setdefault(src, set()).update(dests)
    for src, dests in rev.items():
        neighbors.setdefault(src, set()).update(dests)
    return neighbors


def _neighbors_of(ids: set[str], undirected: dict[str, set[str]]) -> set[str]:
    """Return undirected neighbors of `ids`."""
    found: set[str] = set()
    for node_id in ids:
        found.update(undirected.get(node_id, ()))
    return found


def _reachable(starts: Iterable[str], adj: dict[str, list[str]]) -> set[str]:
    """Return nodes reachable from `starts` following `adj` (includes starts)."""
    seen: set[str] = set()
    stack = list(starts)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(adj.get(current, ()))
    return seen


def _directed_connectors(
    kept: set[str],
    fwd: dict[str, list[str]],
    rev: dict[str, list[str]],
) -> set[str]:
    """Nodes on a directed path from some kept node to another kept node."""
    if len(kept) < 2:
        return set()
    return _reachable(kept, fwd) & _reachable(kept, rev)


def _touches(node_id: str, kept: set[str], undirected: dict[str, set[str]]) -> bool:
    """Return True if `node_id` is kept or undirected-adjacent to kept."""
    if node_id in kept:
        return True
    return bool(undirected.get(node_id, set()) & kept)


def _on_kept_flow(
    node_id: str,
    kept: set[str],
    fwd: dict[str, list[str]],
    rev: dict[str, list[str]],
) -> bool:
    """Return True if a kept node can reach this id or this id can reach kept."""
    if node_id in _reachable(kept, fwd):
        return True
    if node_id in _reachable(kept, rev):
        return True
    return False


def _relevant_handlers(
    nodes: list[Node],
    kept: set[str],
    undirected: dict[str, set[str]],
    fwd: dict[str, list[str]],
    rev: dict[str, list[str]],
) -> set[str]:
    """Keep code handlers that touch a bridge and already join lineage."""
    bridge_ids = {node.id for node in nodes if is_bridge_type(node.type)}
    added: set[str] = set()
    for node in nodes:
        if node.id in kept or not is_code_type(node.type):
            continue
        if not (undirected.get(node.id, set()) & bridge_ids):
            continue
        if _touches(node.id, kept, undirected) or _on_kept_flow(
            node.id, kept, fwd, rev
        ):
            added.add(node.id)
    return added


def _relevant_bridges(
    nodes: list[Node],
    kept: set[str],
    undirected: dict[str, set[str]],
    fwd: dict[str, list[str]],
) -> set[str]:
    """Keep bridge nodes that participate in a kept lineage flow."""
    added: set[str] = set()
    reachable = _reachable(kept, fwd)
    for node in nodes:
        if node.id in kept or not is_bridge_type(node.type):
            continue
        if _touches(node.id, kept, undirected) or node.id in reachable:
            added.add(node.id)
    return added
