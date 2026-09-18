"""Domain model tests: convention, merge, validation, serialization."""

from __future__ import annotations

import ast
from itertools import product
from pathlib import Path

import pytest
from pydantic import ValidationError

from daygent.models import (
    Confidence,
    Edge,
    EdgeType,
    Graph,
    Node,
    NodeType,
    make_api_route_id,
    make_python_function_id,
    max_confidence,
    merge_edge_list,
    merge_node_list,
)

MODELS_DIR = Path(__file__).resolve().parents[1] / "src" / "daygent" / "models"
FORBIDDEN_IMPORTS = frozenset({"networkx", "typer", "rich"})


def test_graph_convention_function_invoked_by_route() -> None:
    """A → B means B depends on A: recommend() → POST /recommend, not the reverse."""
    function = Node(
        id=make_python_function_id("services.recommend", "recommend"),
        name="recommend",
        type=NodeType.PYTHON_FUNCTION,
    )
    route = Node(
        id=make_api_route_id("POST", "/recommend"),
        name="POST /recommend",
        type=NodeType.API_ROUTE,
    )
    edge = Edge(
        source=function.id,
        target=route.id,
        type=EdgeType.INVOKED_BY,
        confidence=Confidence.HIGH,
    )
    assert function.id == "python_function:services.recommend.recommend"
    assert route.id == "api_route:POST:/recommend"
    assert edge.source == function.id
    assert edge.target == route.id
    assert edge.type == "invoked_by"
    assert edge.confidence == Confidence.HIGH
    inverted = Edge(
        source=route.id,
        target=function.id,
        type=EdgeType.INVOKED_BY,
        confidence=Confidence.HIGH,
    )
    assert inverted.key != edge.key
    assert edge.source != route.id


def test_confidence_values() -> None:
    assert set(Confidence) == {Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW}
    assert Confidence("high") is Confidence.HIGH
    with pytest.raises(ValueError):
        Confidence("critical")


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (Confidence.LOW, Confidence.LOW, Confidence.LOW),
        (Confidence.LOW, Confidence.MEDIUM, Confidence.MEDIUM),
        (Confidence.LOW, Confidence.HIGH, Confidence.HIGH),
        (Confidence.MEDIUM, Confidence.LOW, Confidence.MEDIUM),
        (Confidence.MEDIUM, Confidence.MEDIUM, Confidence.MEDIUM),
        (Confidence.MEDIUM, Confidence.HIGH, Confidence.HIGH),
        (Confidence.HIGH, Confidence.LOW, Confidence.HIGH),
        (Confidence.HIGH, Confidence.MEDIUM, Confidence.HIGH),
        (Confidence.HIGH, Confidence.HIGH, Confidence.HIGH),
    ],
)
def test_max_confidence_rank_not_alphabetical(
    left: Confidence, right: Confidence, expected: Confidence
) -> None:
    assert max_confidence(left, right) == expected


def test_confidence_rank_is_not_alphabetical() -> None:
    alphabetical = sorted(Confidence, key=lambda item: item.value)
    ranked = [Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW]
    assert alphabetical != ranked
    assert len(list(product(Confidence, Confidence))) == 9


def test_node_required_fields_and_metadata_isolation() -> None:
    node = Node(id="sql_table:raw.users", name="raw.users", type=NodeType.SQL_TABLE)
    other = Node(id="sql_table:orders", name="orders", type=NodeType.SQL_TABLE)
    node.metadata["parser"] = "sql"
    assert other.metadata == {}
    assert node.file_path is None
    assert node.line_number is None


def test_node_source_location() -> None:
    node = Node(
        id="python_module:app",
        name="app",
        type=NodeType.PYTHON_MODULE,
        file_path="app.py",
        line_number=3,
    )
    assert node.file_path == "app.py"
    assert node.line_number == 3


def test_node_rejects_blank_id_and_name() -> None:
    with pytest.raises(ValidationError):
        Node(id=" ", name="users", type="sql_table")
    with pytest.raises(ValidationError):
        Node(id="sql_table:users", name="", type="sql_table")


def test_node_line_number_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Node(id="n", name="n", type="sql_table", line_number=0)
    with pytest.raises(ValidationError):
        Node(id="n", name="n", type="sql_table", line_number=-1)


def test_node_allows_unknown_future_type() -> None:
    node = Node(id="airflow_dag:nightly", name="nightly", type="airflow_dag")
    assert node.typed() == "airflow_dag"


def test_node_merge_same_id_is_order_independent() -> None:
    first = Node(
        id="sql_table:raw.users",
        name="raw.users",
        type="sql_table",
        file_path="a.sql",
        line_number=10,
        metadata={"schema": "raw", "note": ""},
    )
    second = Node(
        id="sql_table:raw.users",
        name="raw.users",
        type="sql_table",
        file_path="b.sql",
        line_number=4,
        metadata={"dialect": "ansi", "note": "from join"},
    )
    merged_ab = first.merge(second)
    merged_ba = second.merge(first)
    assert merged_ab == merged_ba
    assert merged_ab.line_number == 4
    assert merged_ab.metadata["schema"] == "raw"
    assert merged_ab.metadata["dialect"] == "ansi"
    assert merged_ab.metadata["note"] == "from join"


def test_edge_required_fields_and_metadata_isolation() -> None:
    edge = Edge(source="a", target="b", type=EdgeType.REF, confidence=Confidence.HIGH)
    other = Edge(source="a", target="c", type=EdgeType.REF, confidence=Confidence.LOW)
    edge.metadata["parser"] = "dbt"
    assert other.metadata == {}
    assert other.confidence == Confidence.LOW


def test_edge_rejects_blank_endpoints() -> None:
    with pytest.raises(ValidationError):
        Edge(source="", target="b", type="ref")
    with pytest.raises(ValidationError):
        Edge(source="a", target="  ", type="ref")


def test_duplicate_edges_merge_highest_confidence() -> None:
    key = {
        "source": "dbt_model:stg_users",
        "target": "dbt_model:user_features",
        "type": EdgeType.REF,
    }
    low = Edge(**key, confidence=Confidence.LOW, evidence="heuristic", metadata={"a": 1})
    high = Edge(**key, confidence=Confidence.HIGH, evidence="dbt_ref", metadata={"b": 2})
    merged = merge_edge_list([low, high])
    assert len(merged) == 1
    assert merged[0].confidence == Confidence.HIGH
    assert merged[0].evidence is not None
    assert "dbt_ref" in merged[0].evidence
    assert merged[0].metadata == {"a": 1, "b": 2}
    assert low.merge(high) == high.merge(low)


def test_different_edge_types_do_not_merge() -> None:
    shared = {"source": "sql_table:users", "target": "sql_table:summary"}
    reads = Edge(**shared, type=EdgeType.READ_BY, confidence=Confidence.HIGH)
    writes = Edge(**shared, type=EdgeType.WRITES_TO, confidence=Confidence.MEDIUM)
    merged = merge_edge_list([reads, writes])
    assert len(merged) == 2


def test_duplicate_nodes_collapse_by_id() -> None:
    nodes = merge_node_list(
        [
            Node(id="sql_table:raw.users", name="raw.users", type="sql_table"),
            Node(
                id="sql_table:raw.users",
                name="raw.users",
                type="sql_table",
                file_path="models.sql",
            ),
        ]
    )
    assert len(nodes) == 1
    assert nodes[0].file_path == "models.sql"


def test_graph_round_trip_serialization() -> None:
    graph = Graph(
        nodes=[
            Node(id="dbt_model:stg_users", name="stg_users", type=NodeType.DBT_MODEL),
            Node(
                id="api_route:POST:/recommend",
                name="POST /recommend",
                type=NodeType.API_ROUTE,
            ),
        ],
        edges=[
            Edge(
                source="dbt_model:stg_users",
                target="api_route:POST:/recommend",
                type=EdgeType.FEEDS,
                confidence=Confidence.MEDIUM,
            )
        ],
    )
    dumped = graph.model_dump()
    restored = Graph.model_validate(dumped)
    assert restored == graph
    json_ready = graph.model_dump(mode="json")
    assert Graph.model_validate(json_ready) == graph


def test_node_and_edge_model_dump() -> None:
    node = Node(id="n", name="n", type="sql_table")
    edge = Edge(source="n", target="n", type="depends_on", confidence=Confidence.LOW)
    assert isinstance(node.model_dump(), dict)
    assert isinstance(edge.model_dump(), dict)


def test_public_models_do_not_import_forbidden_libraries() -> None:
    for path in MODELS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", maxsplit=1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".", maxsplit=1)[0]]
                assert node.module != "daygent.cli"
            for name in names:
                assert name not in FORBIDDEN_IMPORTS, f"{path} imports {name}"
