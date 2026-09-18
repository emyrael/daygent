"""Lineage and impact API tests. Analysis must stay CLI-free."""

from __future__ import annotations

import pytest

from daygent.analysis import (
    AmbiguousNodeError,
    NodeNotFoundError,
    get_ancestors,
    get_descendants,
    get_downstream,
    get_upstream,
    impact,
    resolve_node,
)
from daygent.models import Edge, Graph, Node


def _chain() -> Graph:
    """A → B → C (B depends on A, C depends on B)."""
    nodes = [
        Node(id="a", name="A", type="sql_table"),
        Node(id="b", name="B", type="sql_table"),
        Node(id="c", name="C", type="sql_table"),
    ]
    edges = [
        Edge(source="a", target="b", type="read_by", confidence="high"),
        Edge(source="b", target="c", type="read_by", confidence="high"),
    ]
    return Graph(nodes=nodes, edges=edges)


def test_downstream_and_upstream_are_immediate() -> None:
    graph = _chain()
    assert [node.id for node in get_downstream(graph, "a")] == ["b"]
    assert [node.id for node in get_upstream(graph, "c")] == ["b"]
    assert [node.id for node in get_upstream(graph, "a")] == []
    assert [node.id for node in get_downstream(graph, "c")] == []


def test_descendants_and_ancestors_follow_convention() -> None:
    graph = _chain()
    assert [node.id for node in get_descendants(graph, "a")] == ["b", "c"]
    assert [node.id for node in get_ancestors(graph, "c")] == ["b", "a"]


def test_impact_splits_direct_and_transitive() -> None:
    graph = _chain()
    full = impact(graph, "a")
    assert [node.id for node in full.direct] == ["b"]
    assert [node.id for node in full.transitive] == ["c"]
    limited = impact(graph, "a", max_depth=1)
    assert [node.id for node in limited.direct] == ["b"]
    assert limited.transitive == []


def test_empty_downstream() -> None:
    graph = _chain()
    result = impact(graph, "c")
    assert result.direct == []
    assert result.transitive == []
    assert get_descendants(graph, "c") == []


def test_cycles_are_visited_once_and_terminate() -> None:
    graph = Graph(
        nodes=[
            Node(id="a", name="A", type="sql_table"),
            Node(id="b", name="B", type="sql_table"),
            Node(id="c", name="C", type="sql_table"),
        ],
        edges=[
            Edge(source="a", target="b", type="read_by"),
            Edge(source="b", target="c", type="read_by"),
            Edge(source="c", target="a", type="read_by"),
        ],
    )
    descendants = get_descendants(graph, "a")
    ids = [node.id for node in descendants]
    assert ids == ["b", "c"]
    assert len(ids) == len(set(ids))
    result = impact(graph, "a")
    assert [node.id for node in result.direct] == ["b"]
    assert [node.id for node in result.transitive] == ["c"]


def test_resolve_unique_name_and_reject_ambiguous() -> None:
    graph = Graph(
        nodes=[
            Node(id="sql_table:one", name="dup", type="sql_table"),
            Node(id="sql_table:two", name="dup", type="sql_table"),
            Node(id="sql_table:orders", name="orders", type="sql_table"),
        ]
    )
    assert resolve_node(graph, "sql_table:orders").name == "orders"
    assert resolve_node(graph, "orders").id == "sql_table:orders"
    with pytest.raises(AmbiguousNodeError, match="Ambiguous node"):
        resolve_node(graph, "dup")
    with pytest.raises(NodeNotFoundError):
        get_descendants(graph, "missing")


def test_resolve_unique_case_insensitive_name() -> None:
    graph = Graph(nodes=[Node(id="dbt_model:stg_users", name="stg_users", type="dbt_model")])
    assert resolve_node(graph, "STG_USERS").id == "dbt_model:stg_users"


def test_resolve_qualified_table_and_ambiguous_short_name() -> None:
    graph = Graph(
        nodes=[
            Node(
                id="sql_table:warehouse.mirror_station_summary",
                name="station_summary",
                type="sql_table",
            ),
            Node(
                id="sql_table:warehouse.reporting.station_summary",
                name="station_summary",
                type="sql_table",
            ),
        ]
    )
    resolved = resolve_node(graph, "warehouse.reporting.station_summary")
    assert resolved.id == "sql_table:warehouse.reporting.station_summary"
    with pytest.raises(AmbiguousNodeError) as exc_info:
        resolve_node(graph, "station_summary")
    text = str(exc_info.value)
    assert "Ambiguous node 'station_summary'" in text
    assert "Choose one:" in text
    assert 'daygent impact "warehouse.reporting.station_summary"' in text
    assert "sql_table:warehouse.mirror_station_summary" in text
    assert "sql_table:warehouse.reporting.station_summary" in text


def test_unknown_node_includes_suggestions() -> None:
    graph = Graph(nodes=[Node(id="dbt_model:stg_users", name="stg_users", type="dbt_model")])
    with pytest.raises(NodeNotFoundError) as exc_info:
        resolve_node(graph, "stgg_users")
    assert any(node.id == "dbt_model:stg_users" for node in exc_info.value.suggestions)
