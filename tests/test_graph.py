"""Graph store and builder tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from daygent.exceptions import GraphNotFoundError
from daygent.graph.builder import ScanStats, build_graph
from daygent.graph.store import load_graph, save_graph
from daygent.models import Confidence, Edge, Node, ProjectMetadata
from daygent.parsers.base import ParseResult


def _node(node_id: str, name: str | None = None) -> Node:
    return Node(id=node_id, name=name or node_id, type="sql_table")


def test_builder_merges_nodes_and_edges() -> None:
    first = ParseResult(
        nodes=[_node("sql_table:customers", "customers")],
        edges=[
            Edge(
                source="sql_table:customers",
                target="sql_table:summary",
                type="read_by",
                confidence=Confidence.LOW,
            )
        ],
        warnings=["from parser a"],
    )
    second = ParseResult(
        nodes=[
            _node("sql_table:customers", "customers"),
            _node("sql_table:summary", "summary"),
        ],
        edges=[
            Edge(
                source="sql_table:customers",
                target="sql_table:summary",
                type="read_by",
                confidence=Confidence.HIGH,
                evidence="sqlglot",
            )
        ],
        warnings=["from parser b"],
    )
    graph = build_graph(
        [first, second],
        ScanStats(
            project=ProjectMetadata(name="demo"),
            files_scanned=2,
            warnings=["scanner"],
        ),
    )
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    assert graph.edges[0].confidence == Confidence.HIGH
    assert graph.edges[0].source == "sql_table:customers"
    assert graph.edges[0].target == "sql_table:summary"
    assert "scanner" in graph.scan.warnings
    assert "from parser a" in graph.scan.warnings


def test_builder_drops_dangling_edges() -> None:
    result = ParseResult(
        nodes=[_node("sql_table:customers")],
        edges=[
            Edge(
                source="sql_table:customers",
                target="sql_table:missing",
                type="read_by",
                confidence=Confidence.HIGH,
            )
        ],
    )
    graph = build_graph([result], ScanStats(files_scanned=1))
    assert graph.edges == []
    assert any("dangling" in warning for warning in graph.scan.warnings)


def test_store_round_trip_and_determinism(tmp_path: Path) -> None:
    result = ParseResult(
        nodes=[_node("b", "b"), _node("a", "a")],
        edges=[Edge(source="a", target="b", type="read_by", confidence=Confidence.HIGH)],
    )
    graph = build_graph([result], ScanStats(files_scanned=1))
    path = tmp_path / ".daygent" / "graph.json"
    save_graph(graph, path)
    loaded = load_graph(path)
    first = path.read_text(encoding="utf-8")

    graph.scan.timestamp = "2099-01-01T00:00:00Z"
    save_graph(graph, path)
    second = path.read_text(encoding="utf-8")

    assert loaded.nodes[0].id == "a"
    assert '"a"' in first
    # timestamp may change; node/edge order must not
    assert '"id": "a"' in first
    nodes_block_one = first.split('"nodes"')[1].split('"edges"')[0]
    nodes_block_two = second.split('"nodes"')[1].split('"edges"')[0]
    assert nodes_block_one == nodes_block_two


def test_load_missing_graph(tmp_path: Path) -> None:
    with pytest.raises(GraphNotFoundError, match="daygent scan"):
        load_graph(tmp_path / "missing.json")
