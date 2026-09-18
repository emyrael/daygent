"""Human graph chains and Mermaid export. No Typer; arrows follow stored direction."""

from __future__ import annotations

import re

from daygent.graph.traversal import adjacency
from daygent.models import Graph, Node, NodeType

_MERMAID_SAFE = re.compile(r"[^A-Za-z0-9_]")
MAX_CHAIN_PATHS = 80


def display_name(node: Node) -> str:
    """Human-facing label. Functions are shown with `()`."""
    if node.type == NodeType.PYTHON_FUNCTION and not node.name.endswith("()"):
        return f"{node.name}()"
    return node.name


def mermaid_safe_id(node_id: str, used: dict[str, str] | None = None) -> str:
    """Return a Mermaid-safe identifier unique within `used`."""
    cleaned = _MERMAID_SAFE.sub("_", node_id).strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"n_{cleaned}" if cleaned else "n"
    if used is None:
        return cleaned
    candidate = cleaned
    suffix = 2
    existing = {value for value in used.values()}
    while candidate in existing:
        candidate = f"{cleaned}_{suffix}"
        suffix += 1
    used[node_id] = candidate
    return candidate


def _escape_label(text: str) -> str:
    """Quote-safe Mermaid node label."""
    return text.replace('"', "'").replace("\n", " ")


def render_mermaid(graph: Graph) -> str:
    """Render `graph LR` with stored A → B as `A --> B`."""
    used: dict[str, str] = {}
    lines = ["graph LR"]
    for node in graph.nodes:
        mid = mermaid_safe_id(node.id, used)
        lines.append(f'    {mid}["{_escape_label(display_name(node))}"]')
    index_ids = {node.id for node in graph.nodes}
    for edge in graph.edges:
        if edge.source not in index_ids or edge.target not in index_ids:
            continue
        src = used[edge.source]
        tgt = used[edge.target]
        lines.append(f"    {src} --> {tgt}")
    return "\n".join(lines) + "\n"


def _roots(graph: Graph) -> list[Node]:
    """Nodes with no incoming edge, or every node if the graph is only cycles."""
    targets = {edge.target for edge in graph.edges}
    roots = [node for node in graph.nodes if node.id not in targets]
    if not roots:
        return list(graph.nodes)
    sources = {edge.source for edge in graph.edges}
    return sorted(roots, key=lambda node: (0 if node.id in sources else 1, node.id))


def _collect_paths(graph: Graph) -> list[list[Node]]:
    """Enumerate simple root-to-sink paths, stopping at cycles."""
    index = graph.node_index()
    neighbors = adjacency(graph, forward=True)
    paths: list[list[Node]] = []

    def dfs(node: Node, path: list[Node], visiting: set[str]) -> None:
        if len(paths) >= MAX_CHAIN_PATHS:
            return
        children = [cid for cid in neighbors.get(node.id, []) if cid in index]
        if not children:
            paths.append(path[:])
            return
        progressed = False
        for child_id in children:
            child = index[child_id]
            if child_id in visiting:
                continue
            progressed = True
            visiting.add(child_id)
            path.append(child)
            dfs(child, path, visiting)
            path.pop()
            visiting.remove(child_id)
            if len(paths) >= MAX_CHAIN_PATHS:
                return
        if not progressed:
            paths.append(path[:])

    for root in _roots(graph):
        dfs(root, [root], {root.id})
        if len(paths) >= MAX_CHAIN_PATHS:
            break
    if not paths and graph.nodes:
        paths = [[node] for node in graph.nodes]
    return paths


def format_chains(graph: Graph) -> str:
    """Readable dependency chains using the locked A → B (B depends on A) direction."""
    if not graph.nodes:
        return "Graph is empty.\n"
    blocks: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for path in _collect_paths(graph):
        key = tuple(node.id for node in path)
        if key in seen:
            continue
        seen.add(key)
        lines = [display_name(path[0])]
        for node in path[1:]:
            lines.append("   ↓")
            lines.append(display_name(node))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"
