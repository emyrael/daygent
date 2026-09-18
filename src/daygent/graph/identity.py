"""Repository-level data-asset identity resolution.

Parsers run per file and cannot know what other files declare. A relation name
such as `v_orders` is emitted as a provisional `sql_table:` reference; if any
file declares it as a logical DLT/Lakeflow dataset, that reference must collapse
onto the declaration instead of standing as a duplicate physical table.

Resolution priority for a relation reference:
1. declared logical pipeline dataset (`@dlt.view`, temporary views)
2. declared persisted pipeline table (already `sql_table:` from the parser)
3. physical SQL table (the reference itself)

Runs after `build_graph` and before `project_lineage_graph`. Never reverses an
edge; it only rewrites endpoint ids.
"""

from __future__ import annotations

from daygent.models import Edge, Graph, Node, NodeType, ScanMetadata, normalize_sql_table_name

# Copied onto the declaration node so the physical reference is not lost, but
# never used for identity.
_REFERENCE_KEYS_DROPPED: frozenset[str] = frozenset({"relation_ref", "table"})


def _relation_name(node: Node) -> str | None:
    """Return the normalized relation name encoded in a node id."""
    _, _, raw = node.id.partition(":")
    if not raw:
        return None
    try:
        return normalize_sql_table_name(raw)
    except ValueError:
        return None


def declared_datasets(graph: Graph) -> dict[str, str]:
    """Map normalized relation name to the id of its logical dataset declaration."""
    declared: dict[str, str] = {}
    for node in graph.nodes:
        if node.type != NodeType.PIPELINE_DATASET:
            continue
        if not node.metadata.get("dataset_declaration"):
            continue
        name = _relation_name(node)
        if name is None:
            continue
        # Deterministic when two files declare the same logical name.
        current = declared.get(name)
        if current is None or node.id < current:
            declared[name] = node.id
    return declared


def _absorbed_metadata(reference: Node) -> dict[str, object]:
    """Keep evidence from an absorbed reference without its physical identity."""
    return {
        key: value
        for key, value in reference.metadata.items()
        if key not in _REFERENCE_KEYS_DROPPED
    }


def resolve_dataset_identity(graph: Graph) -> Graph:
    """Collapse physical relation references onto declared logical datasets."""
    declared = declared_datasets(graph)
    if not declared:
        return graph.sorted()

    index = graph.node_index()
    rewrites: dict[str, str] = {}
    for node in graph.nodes:
        if node.type != NodeType.SQL_TABLE:
            continue
        name = _relation_name(node)
        if name is None:
            continue
        target = declared.get(name)
        if target is None or target == node.id or target not in index:
            continue
        rewrites[node.id] = target
    if not rewrites:
        return graph.sorted()

    nodes: dict[str, Node] = {}
    for node in graph.nodes:
        target = rewrites.get(node.id)
        if target is None:
            merged = node
        else:
            declaration = index[target]
            merged = Node(
                id=target,
                name=declaration.name,
                type=declaration.type,
                metadata={**_absorbed_metadata(node), "referenced_as": node.name},
            )
        existing = nodes.get(merged.id)
        nodes[merged.id] = existing.merge(merged) if existing else merged

    edges: dict[tuple[str, str, str], Edge] = {}
    for edge in graph.edges:
        source = rewrites.get(edge.source, edge.source)
        target = rewrites.get(edge.target, edge.target)
        if source == target:
            continue
        moved = edge.model_copy(update={"source": source, "target": target})
        existing = edges.get(moved.key)
        edges[moved.key] = existing.merge(moved) if existing else moved

    return Graph(
        version=graph.version,
        project=graph.project.model_copy(deep=True),
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        scan=ScanMetadata(
            timestamp=graph.scan.timestamp,
            files_scanned=graph.scan.files_scanned,
            warnings=list(graph.scan.warnings),
        ),
    ).sorted()
