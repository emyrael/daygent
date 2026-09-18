"""Lineage queries: upstream/ancestors against arrows, downstream/descendants with them."""

from __future__ import annotations

import difflib

from daygent.exceptions import AmbiguousNodeError, NodeNotFoundError
from daygent.graph.traversal import walk
from daygent.models import Graph, Node


def suggest_nodes(graph: Graph, query: str, limit: int = 5) -> list[Node]:
    """Return close name/id matches for an unknown query. Never auto-selects."""
    needle = query.strip()
    if not needle or limit <= 0:
        return []
    by_label: dict[str, Node] = {}
    labels: list[str] = []
    for node in graph.nodes:
        for label in (node.id, node.name):
            if label not in by_label:
                by_label[label] = node
                labels.append(label)
    ranked = difflib.get_close_matches(needle, labels, n=limit * 2, cutoff=0.45)
    found: list[Node] = []
    seen: set[str] = set()
    for label in ranked:
        node = by_label[label]
        if node.id in seen:
            continue
        seen.add(node.id)
        found.append(node)
        if len(found) >= limit:
            break
    return found


def _unique_or_ambiguous(query: str, matches: list[Node]) -> Node | None:
    """Return the only match, raise if several, or None if empty."""
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousNodeError(query, matches)
    return None


def resolve_node(graph: Graph, node_id: str) -> Node:
    """Resolve a node without silently picking among candidates.

    Order: exact id, exact name, unique case-insensitive name, unique id suffix.
    """
    query = node_id.strip()
    if not query:
        raise NodeNotFoundError("Node query is empty", query=query)
    index = graph.node_index()
    if query in index:
        return index[query]
    exact_name = _unique_or_ambiguous(
        query, [node for node in graph.nodes if node.name == query]
    )
    if exact_name is not None:
        return exact_name
    lowered = query.lower()
    casefold_name = _unique_or_ambiguous(
        query, [node for node in graph.nodes if node.name.lower() == lowered]
    )
    if casefold_name is not None:
        return casefold_name
    suffix = _unique_or_ambiguous(
        query,
        [
            node
            for node in graph.nodes
            if node.id.endswith(f":{query}") or node.id.split(":")[-1] == query
        ],
    )
    if suffix is not None:
        return suffix
    ci_suffix = _unique_or_ambiguous(
        query,
        [
            node
            for node in graph.nodes
            if node.id.split(":")[-1].lower() == lowered
        ],
    )
    if ci_suffix is not None:
        return ci_suffix
    raise NodeNotFoundError(
        f"No graph node matches {query!r}",
        query=query,
        suggestions=suggest_nodes(graph, query),
    )


def get_downstream(graph: Graph, node_id: str) -> list[Node]:
    """Return immediate downstream consumers (depth 1, with the arrows)."""
    origin = resolve_node(graph, node_id)
    return [node for node, _depth in walk(graph, origin.id, forward=True, max_depth=1)]


def get_upstream(graph: Graph, node_id: str) -> list[Node]:
    """Return immediate upstream dependencies (depth 1, against the arrows)."""
    origin = resolve_node(graph, node_id)
    return [node for node, _depth in walk(graph, origin.id, forward=False, max_depth=1)]


def get_descendants(graph: Graph, node_id: str) -> list[Node]:
    """Return every reachable downstream node (with the arrows)."""
    origin = resolve_node(graph, node_id)
    return [node for node, _depth in walk(graph, origin.id, forward=True)]


def get_ancestors(graph: Graph, node_id: str) -> list[Node]:
    """Return every reachable upstream node (against the arrows)."""
    origin = resolve_node(graph, node_id)
    return [node for node, _depth in walk(graph, origin.id, forward=False)]
