"""Repository-level data-asset identity resolution.

Parsers run per file and cannot know what other files declare. A relation name
such as `v_orders` is emitted as a provisional `sql_table:` reference; if any
file declares it as a logical DLT/Lakeflow dataset, that reference must collapse
onto the declaration instead of standing as a duplicate physical table.

Resolution priority for a relation reference:
1. declared logical pipeline dataset (`@dlt.view`, temporary views)
2. declared persisted pipeline table (already `sql_table:` from the parser)
3. declared dbt asset, when the evidence is unambiguous
4. physical SQL table (the reference itself)

A dbt model only absorbs a physical reference when static evidence ties them
together: either the reference equals a qualified relation dbt recorded in
manifest.json, or the reference is unqualified and exactly one dbt model in the
repository carries that name. A qualified reference never merges into a dbt
model on basename similarity alone.

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


def _qualified_relations(node: Node) -> set[str]:
    """Return normalized qualified relation names dbt recorded for a model."""
    relations: set[str] = set()
    raw = node.metadata.get("relation_name")
    candidates: list[str] = [raw] if isinstance(raw, str) else []
    database = node.metadata.get("database")
    schema = node.metadata.get("schema")
    alias = node.metadata.get("alias")
    table = alias if isinstance(alias, str) and alias.strip() else node.name
    if isinstance(schema, str) and schema.strip() and isinstance(table, str):
        if isinstance(database, str) and database.strip():
            candidates.append(f"{database}.{schema}.{table}")
        candidates.append(f"{schema}.{table}")
    for candidate in candidates:
        try:
            relations.add(normalize_sql_table_name(candidate))
        except ValueError:
            continue
    return relations


def declared_dbt_sources(graph: Graph) -> dict[str, str]:
    """Map a dbt source's qualified relation to its node id.

    A dbt source is declared as `<source>.<table>`, which by dbt's own default
    is the qualified warehouse relation. Matching is therefore only allowed on
    the full dotted name, never on the bare table name.
    """
    by_relation: dict[str, str] = {}
    ambiguous: set[str] = set()
    for node in graph.nodes:
        if node.type != NodeType.DBT_SOURCE:
            continue
        name = _relation_name(node)
        if name is None or "." not in name:
            continue
        if name in by_relation and by_relation[name] != node.id:
            ambiguous.add(name)
        by_relation[name] = node.id
    for name in ambiguous:
        by_relation.pop(name, None)
    return by_relation


def declared_dbt_assets(graph: Graph) -> tuple[dict[str, str], dict[str, str]]:
    """Map dbt models by unqualified name and by qualified manifest relation.

    A name claimed by more than one model is dropped from both maps so an
    ambiguous reference stays unresolved rather than being merged by guess.
    """
    by_name: dict[str, str] = {}
    by_relation: dict[str, str] = {}
    ambiguous_names: set[str] = set()
    ambiguous_relations: set[str] = set()
    for node in graph.nodes:
        if node.type != NodeType.DBT_MODEL:
            continue
        name = _relation_name(node)
        if name is not None and "." not in name:
            if name in by_name and by_name[name] != node.id:
                ambiguous_names.add(name)
            by_name[name] = node.id
        for relation in _qualified_relations(node):
            if relation in by_relation and by_relation[relation] != node.id:
                ambiguous_relations.add(relation)
            by_relation[relation] = node.id
    for name in ambiguous_names:
        by_name.pop(name, None)
    for relation in ambiguous_relations:
        by_relation.pop(relation, None)
    return by_name, by_relation


def _absorbed_metadata(reference: Node) -> dict[str, object]:
    """Keep evidence from an absorbed reference without its physical identity."""
    return {
        key: value
        for key, value in reference.metadata.items()
        if key not in _REFERENCE_KEYS_DROPPED
    }


def _resolve_reference(
    name: str,
    declared: dict[str, str],
    dbt_by_name: dict[str, str],
    dbt_by_relation: dict[str, str],
    source_by_relation: dict[str, str],
) -> str | None:
    """Return the canonical id for a physical relation reference, if provable."""
    target = declared.get(name)
    if target is not None:
        return target
    target = dbt_by_relation.get(name)
    if target is not None:
        return target
    target = source_by_relation.get(name)
    if target is not None:
        return target
    # Basename identity is only safe when the reference carries no schema of
    # its own; `analytics.orders` must not collapse onto a bare `orders` model.
    if "." not in name:
        return dbt_by_name.get(name)
    return None


def resolve_dataset_identity(graph: Graph) -> Graph:
    """Collapse physical relation references onto declared canonical assets."""
    declared = declared_datasets(graph)
    dbt_by_name, dbt_by_relation = declared_dbt_assets(graph)
    source_by_relation = declared_dbt_sources(graph)
    if not declared and not dbt_by_name and not dbt_by_relation and not source_by_relation:
        return graph.sorted()

    index = graph.node_index()
    rewrites: dict[str, str] = {}
    for node in graph.nodes:
        if node.type != NodeType.SQL_TABLE:
            continue
        name = _relation_name(node)
        if name is None:
            continue
        target = _resolve_reference(
            name, declared, dbt_by_name, dbt_by_relation, source_by_relation
        )
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
