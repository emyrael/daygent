"""Impact analysis: walk with the arrows; direct is depth 1, transitive is depth 2+."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from daygent.analysis.lineage import resolve_node
from daygent.graph.traversal import walk
from daygent.models import Graph, Node


class ImpactedNode(BaseModel):
    """One node reached from the origin, with shortest-path depth."""

    model_config = ConfigDict(extra="forbid")

    node: Node
    depth: int = Field(gt=0)


class ImpactResult(BaseModel):
    """Downstream blast radius. Direct = depth 1; transitive = depth ≥ 2."""

    model_config = ConfigDict(extra="forbid")

    origin: Node
    affected: list[ImpactedNode] = Field(default_factory=list)
    max_depth: int | None = None

    @property
    def direct(self) -> list[Node]:
        """Nodes exactly one hop downstream."""
        return [item.node for item in self.affected if item.depth == 1]

    @property
    def transitive(self) -> list[Node]:
        """Nodes two or more hops downstream."""
        return [item.node for item in self.affected if item.depth >= 2]


def impact(
    graph: Graph,
    node_id: str,
    max_depth: int | None = None,
) -> ImpactResult:
    """Compute downstream impact. `max_depth=1` returns only direct consumers."""
    origin = resolve_node(graph, node_id)
    reached = walk(graph, origin.id, forward=True, max_depth=max_depth)
    return ImpactResult(
        origin=origin,
        affected=[ImpactedNode(node=node, depth=depth) for node, depth in reached],
        max_depth=max_depth,
    )


def _node_payload(node: Node, depth: int) -> dict[str, Any]:
    """JSON object for one affected node."""
    return {"id": node.id, "name": node.name, "type": node.type, "depth": depth}


def impact_as_dict(result: ImpactResult, query: str) -> dict[str, Any]:
    """Machine-readable impact document matching the v0.1 CLI JSON shape."""
    direct = [_node_payload(item.node, item.depth) for item in result.affected if item.depth == 1]
    downstream = [
        _node_payload(item.node, item.depth) for item in result.affected if item.depth >= 2
    ]
    return {
        "query": query,
        "resolved_id": result.origin.id,
        "direct": direct,
        "downstream": downstream,
        "affected_count": len(result.affected),
    }
