"""Graph builder tests: merge, warnings, dangling edges, direction lock."""

from __future__ import annotations

from daygent.graph.builder import DANGLING_EDGE_POLICY, ScanStats, build_graph
from daygent.models import (
    Confidence,
    Edge,
    EdgeType,
    Node,
    NodeType,
    ProjectMetadata,
    make_api_route_id,
    make_python_function_id,
    make_sql_table_id,
)
from daygent.parsers.base import ParseResult


def _table(name: str, **kwargs: object) -> Node:
    extras = dict(kwargs)
    return Node(
        id=make_sql_table_id(name),
        name=name,
        type=NodeType.SQL_TABLE,
        **extras,
    )


def test_duplicate_nodes_merge_by_id() -> None:
    first = ParseResult(nodes=[_table("customers", file_path="a.sql", line_number=4)])
    second = ParseResult(nodes=[_table("customers", file_path="b.sql", line_number=2)])
    graph = build_graph([first, second], ScanStats(files_scanned=2))
    assert len(graph.nodes) == 1
    node = graph.nodes[0]
    assert node.id == "sql_table:customers"
    assert node.line_number == 2
    assert node.file_path in {"a.sql", "b.sql"}


def test_metadata_is_unioned_on_node_merge() -> None:
    first = ParseResult(nodes=[_table("customers", metadata={"schema": "raw"})])
    second = ParseResult(nodes=[_table("customers", metadata={"kind": "table"})])
    graph = build_graph([first, second])
    assert graph.nodes[0].metadata == {"kind": "table", "schema": "raw"}


def test_duplicate_edges_merge_and_strongest_confidence_wins() -> None:
    nodes = [_table("customers"), _table("summary")]
    low = ParseResult(
        nodes=nodes,
        edges=[
            Edge(
                source="sql_table:customers",
                target="sql_table:summary",
                type="read_by",
                confidence=Confidence.LOW,
                evidence="guess",
            )
        ],
    )
    high = ParseResult(
        nodes=nodes,
        edges=[
            Edge(
                source="sql_table:customers",
                target="sql_table:summary",
                type="read_by",
                confidence=Confidence.HIGH,
                evidence="sqlglot",
            )
        ],
    )
    graph = build_graph([low, high])
    assert len(graph.edges) == 1
    assert graph.edges[0].confidence == Confidence.HIGH
    reverse = build_graph([high, low])
    assert reverse.edges[0].confidence == Confidence.HIGH
    assert graph.edges[0].source == "sql_table:customers"
    assert graph.edges[0].target == "sql_table:summary"


def test_warnings_and_files_scanned_are_aggregated() -> None:
    results = [
        ParseResult(nodes=[_table("a")], warnings=["from parser a"]),
        ParseResult(nodes=[_table("b")], warnings=["from parser b"]),
    ]
    graph = build_graph(
        results,
        ScanStats(files_scanned=7, warnings=["scanner"]),
    )
    assert graph.scan.files_scanned == 7
    assert graph.scan.warnings == ["scanner", "from parser a", "from parser b"]


def test_dangling_edges_are_dropped_with_warning() -> None:
    assert DANGLING_EDGE_POLICY == "drop_and_warn"
    result = ParseResult(
        nodes=[_table("customers")],
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


def test_builder_results_are_deterministic() -> None:
    first = ParseResult(
        nodes=[_table("orders"), _table("customers")],
        edges=[
            Edge(
                source="sql_table:customers",
                target="sql_table:orders",
                type="read_by",
                confidence=Confidence.MEDIUM,
            )
        ],
    )
    second = ParseResult(
        nodes=[_table("customers", metadata={"z": 1}), _table("orders")],
        edges=[
            Edge(
                source="sql_table:customers",
                target="sql_table:orders",
                type="read_by",
                confidence=Confidence.HIGH,
            )
        ],
    )
    left = build_graph([first, second], ScanStats(files_scanned=2, timestamp="2026-01-01T00:00:00Z"))
    right = build_graph(
        [second, first], ScanStats(files_scanned=2, timestamp="2026-01-01T00:00:00Z")
    )
    assert [node.id for node in left.nodes] == [node.id for node in right.nodes]
    assert [edge.key for edge in left.edges] == [edge.key for edge in right.edges]
    assert left.nodes[0].metadata == right.nodes[0].metadata
    assert left.model_dump() == right.model_dump()


def test_edge_direction_is_not_reversed() -> None:
    """raw_users → stg_users → recommend() → POST /recommend stays that order."""
    raw = _table("raw_users")
    stg = _table("stg_users")
    recommend = Node(
        id=make_python_function_id("services.recommend", "recommend"),
        name="recommend",
        type=NodeType.PYTHON_FUNCTION,
    )
    route = Node(
        id=make_api_route_id("POST", "/recommend"),
        name="POST /recommend",
        type=NodeType.API_ROUTE,
    )
    result = ParseResult(
        nodes=[raw, stg, recommend, route],
        edges=[
            Edge(
                source=raw.id,
                target=stg.id,
                type=EdgeType.READ_BY,
                confidence=Confidence.HIGH,
            ),
            Edge(
                source=stg.id,
                target=recommend.id,
                type=EdgeType.READ_BY,
                confidence=Confidence.HIGH,
            ),
            Edge(
                source=recommend.id,
                target=route.id,
                type=EdgeType.INVOKED_BY,
                confidence=Confidence.HIGH,
            ),
        ],
    )
    graph = build_graph([result], ScanStats(project=ProjectMetadata(name="demo")))
    pairs = [(edge.source, edge.target, edge.type) for edge in graph.edges]
    assert (
        "sql_table:raw_users",
        "sql_table:stg_users",
        "read_by",
    ) in pairs
    assert (
        "sql_table:stg_users",
        "python_function:services.recommend.recommend",
        "read_by",
    ) in pairs
    assert (
        "python_function:services.recommend.recommend",
        "api_route:POST:/recommend",
        "invoked_by",
    ) in pairs
    assert not any(edge.source == route.id for edge in graph.edges)
    assert not any(edge.target == raw.id for edge in graph.edges)


def test_unresolved_imported_modules_are_dropped() -> None:
    graph = build_graph(
        [
            ParseResult(
                nodes=[
                    Node(
                        id="python_module:app",
                        name="app",
                        type="python_module",
                        file_path="app.py",
                    ),
                    Node(
                        id="python_module:ast",
                        name="ast",
                        type="python_module",
                        metadata={"imported": True},
                    ),
                ],
                edges=[
                    Edge(
                        source="python_module:ast",
                        target="python_module:app",
                        type="imported_by",
                        confidence=Confidence.HIGH,
                    )
                ],
            )
        ]
    )
    assert [node.id for node in graph.nodes] == ["python_module:app"]
    assert graph.edges == []
    assert graph.scan.warnings == []


def test_imported_module_kept_when_source_file_exists() -> None:
    graph = build_graph(
        [
            ParseResult(
                nodes=[
                    Node(
                        id="python_module:lib",
                        name="lib",
                        type="python_module",
                        metadata={"imported": True},
                    )
                ]
            ),
            ParseResult(
                nodes=[
                    Node(
                        id="python_module:lib",
                        name="lib",
                        type="python_module",
                        file_path="lib.py",
                    )
                ]
            ),
        ]
    )
    matches = [node for node in graph.nodes if node.id == "python_module:lib"]
    assert len(matches) == 1
    assert matches[0].file_path == "lib.py"
