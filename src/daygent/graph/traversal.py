"""Cycle-safe graph walks. A → B means B depends on A; impact follows arrows."""

from __future__ import annotations

from collections import defaultdict, deque

from daygent.models import Graph, Node


def adjacency(graph: Graph, *, forward: bool) -> dict[str, list[str]]:
    """Build sorted adjacency lists. Forward follows arrows (downstream)."""
    edges: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if forward:
            edges[edge.source].add(edge.target)
        else:
            edges[edge.target].add(edge.source)
    return {key: sorted(values) for key, values in edges.items()}


def walk(
    graph: Graph,
    start_id: str,
    *,
    forward: bool,
    max_depth: int | None = None,
) -> list[tuple[Node, int]]:
    """BFS from `start_id`. Each node is visited once; the origin is omitted.

    `forward=True` walks with the arrows (descendants / impact).
    `forward=False` walks against the arrows (ancestors / upstream).
    """
    index = graph.node_index()
    neighbors = adjacency(graph, forward=forward)
    seen: set[str] = {start_id}
    found: list[tuple[Node, int]] = []
    queue: deque[tuple[str, int]] = deque([(start_id, 0)])
    while queue:
        current, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        for nxt in neighbors.get(current, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            node = index.get(nxt)
            if node is None:
                continue
            new_depth = depth + 1
            found.append((node, new_depth))
            queue.append((nxt, new_depth))
    return found
