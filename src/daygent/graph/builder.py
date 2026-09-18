"""Merge parser results into one Graph.

Dangling-edge rule: if either endpoint is missing, drop the edge and add a
warning, unless the edge is marked `provisional` (a cross-file guess that is
expected to miss). Parser edge direction is never reversed.

Locked convention: A → B means B depends on A
(example: raw_users → stg_users → recommend() → POST /recommend).
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from daygent.models import Edge, Graph, Node, NodeType, ProjectMetadata, ScanMetadata, utc_now_iso
from daygent.parsers.base import ParseResult

# Drop edges whose source or target node was never emitted. Do not keep them.
DANGLING_EDGE_POLICY = "drop_and_warn"


class ScanStats(BaseModel):
    """Scanner-side stats merged into Graph.scan."""

    model_config = ConfigDict(extra="forbid")

    files_scanned: int = 0
    warnings: list[str] = Field(default_factory=list)
    project: ProjectMetadata | None = None
    timestamp: str | None = None


def build_graph(
    parse_results: Sequence[ParseResult],
    scan_stats: ScanStats | None = None,
) -> Graph:
    """Combine ParseResults: merge nodes by id, edges by (source, target, type).

    Strongest confidence wins. Metadata is unioned. Output is sorted so two
    equivalent scans produce the same node/edge order.
    """
    stats = scan_stats or ScanStats()
    nodes: dict[str, Node] = {}
    edges: dict[tuple[str, str, str], Edge] = {}
    warnings: list[str] = list(stats.warnings)

    for result in parse_results:
        warnings.extend(result.warnings)
        for node in result.nodes:
            existing = nodes.get(node.id)
            nodes[node.id] = existing.merge(node) if existing else node
        for edge in result.edges:
            key = edge.key
            existing_edge = edges.get(key)
            edges[key] = existing_edge.merge(edge) if existing_edge else edge

    unresolved_imports = {
        node_id
        for node_id, node in nodes.items()
        if _is_unresolved_import(node)
    }
    for node_id in unresolved_imports:
        nodes.pop(node_id)

    kept_edges: list[Edge] = []
    for key in sorted(edges):
        edge = edges[key]
        if edge.source in unresolved_imports or edge.target in unresolved_imports:
            continue
        if edge.source not in nodes or edge.target not in nodes:
            # A provisional edge is a best-effort guess (an imported symbol that
            # may be a class, a constant, or a third-party name). Dropping it is
            # the expected outcome, not a finding worth reporting.
            if not edge.metadata.get("provisional"):
                warnings.append(
                    f"Dropped dangling edge {edge.source} -> {edge.target} ({edge.type})"
                )
            continue
        kept_edges.append(edge)

    return Graph(
        project=stats.project or ProjectMetadata(),
        nodes=list(nodes.values()),
        edges=kept_edges,
        scan=ScanMetadata(
            timestamp=stats.timestamp or utc_now_iso(),
            files_scanned=stats.files_scanned,
            warnings=warnings,
        ),
    ).sorted()


def _is_unresolved_import(node: Node) -> bool:
    """True for placeholder import modules that were never seen as a source file."""
    if node.type != NodeType.PYTHON_MODULE and node.type != "python_module":
        return False
    if node.file_path:
        return False
    return bool(node.metadata.get("imported"))
