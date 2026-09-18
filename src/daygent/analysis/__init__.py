"""Core analysis APIs. Must not import Typer or daygent.cli."""

from __future__ import annotations

from daygent.analysis.impact import ImpactResult, ImpactedNode, impact, impact_as_dict
from daygent.analysis.lineage import (
    get_ancestors,
    get_descendants,
    get_downstream,
    get_upstream,
    resolve_node,
    suggest_nodes,
)
from daygent.exceptions import AmbiguousNodeError, NodeNotFoundError

__all__ = [
    "AmbiguousNodeError",
    "ImpactResult",
    "ImpactedNode",
    "NodeNotFoundError",
    "get_ancestors",
    "get_descendants",
    "get_downstream",
    "get_upstream",
    "impact",
    "impact_as_dict",
    "resolve_node",
    "suggest_nodes",
]
