"""Deterministic graph.json store tests. No scanner or CLI graph command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daygent.exceptions import GraphNotFoundError
from daygent.graph.store import (
    GraphStore,
    GraphStoreError,
    canonical_graph_dict,
    default_graph_path,
    load_graph,
    save_graph,
)
from daygent.models import (
    Confidence,
    Edge,
    Graph,
    Node,
    ProjectMetadata,
    ScanMetadata,
)


def _sample_graph() -> Graph:
    """Unsorted graph with extra fields to prove canonicalization."""
    return Graph(
        version="0.1",
        project=ProjectMetadata(name="demo", path="/tmp/demo"),
        nodes=[
            Node(
                id="sql_table:orders",
                name="orders",
                type="sql_table",
                file_path="orders.sql",
                line_number=2,
                metadata={"schema": "raw", "kind": "table"},
            ),
            Node(
                id="sql_table:customers",
                name="customers",
                type="sql_table",
                metadata={"kind": "table"},
            ),
        ],
        edges=[
            Edge(
                source="sql_table:orders",
                target="sql_table:summary",
                type="read_by",
                confidence=Confidence.MEDIUM,
            ),
            Edge(
                source="sql_table:customers",
                target="sql_table:summary",
                type="read_by",
                confidence=Confidence.HIGH,
                evidence="sqlglot",
                metadata={"parser": "sql"},
            ),
        ],
        scan=ScanMetadata(
            timestamp="2026-09-18T08:00:00+00:00",
            files_scanned=3,
            warnings=["skip huge.py"],
        ),
    )


def test_default_graph_path() -> None:
    assert default_graph_path(Path("/repo")) == Path("/repo") / ".daygent" / "graph.json"


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / ".daygent" / "graph.json"
    save_graph(_sample_graph(), path)
    assert path.is_file()


def test_custom_output_path(tmp_path: Path) -> None:
    path = tmp_path / "artifacts" / "lineage.json"
    save_graph(_sample_graph(), path)
    loaded = load_graph(path)
    assert loaded.nodes[0].id == "sql_table:customers"
    assert path == tmp_path / "artifacts" / "lineage.json"


def test_default_root_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    saved = save_graph(_sample_graph())
    assert saved == tmp_path / ".daygent" / "graph.json"
    loaded = load_graph()
    assert loaded.project.name == "demo"


def test_round_trip_preserves_fields(tmp_path: Path) -> None:
    path = tmp_path / ".daygent" / "graph.json"
    original = _sample_graph()
    save_graph(original, path)
    loaded = load_graph(path)
    customers = loaded.node_index()["sql_table:customers"]
    orders = loaded.node_index()["sql_table:orders"]
    assert customers.name == "customers"
    assert customers.type == "sql_table"
    assert orders.file_path == "orders.sql"
    assert orders.line_number == 2
    assert orders.metadata["schema"] == "raw"
    edge = next(
        item
        for item in loaded.edges
        if item.source == "sql_table:customers"
    )
    assert edge.confidence == Confidence.HIGH
    assert edge.evidence == "sqlglot"
    assert edge.metadata["parser"] == "sql"
    assert loaded.scan.files_scanned == 3
    assert loaded.scan.warnings == ["skip huge.py"]
    assert loaded.version == "0.1"


def test_nodes_sorted_by_id_and_edges_sorted_by_key(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    save_graph(_sample_graph(), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [node["id"] for node in payload["nodes"]] == [
        "sql_table:customers",
        "sql_table:orders",
    ]
    edge_keys = [(edge["source"], edge["target"], edge["type"]) for edge in payload["edges"]]
    assert edge_keys == sorted(edge_keys)


def test_schema_key_order_is_canonical(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    save_graph(_sample_graph(), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert list(payload.keys()) == ["version", "project", "nodes", "edges", "scan"]
    assert list(payload["scan"].keys()) == ["timestamp", "files_scanned", "warnings"]
    assert list(payload["nodes"][0].keys())[:3] == ["id", "name", "type"]


def test_timestamp_is_utc_with_z(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    save_graph(_sample_graph(), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    timestamp = payload["scan"]["timestamp"]
    assert timestamp.endswith("Z")
    assert "+00:00" not in timestamp
    assert timestamp == "2026-09-18T08:00:00Z"


def test_deterministic_saves_ignore_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    graph = _sample_graph()
    save_graph(graph, path)
    first = json.loads(path.read_text(encoding="utf-8"))
    graph.scan.timestamp = "2099-01-01T00:00:00Z"
    save_graph(graph, path)
    second = json.loads(path.read_text(encoding="utf-8"))
    assert first["nodes"] == second["nodes"]
    assert first["edges"] == second["edges"]
    assert first["version"] == second["version"]
    assert first["scan"]["timestamp"] != second["scan"]["timestamp"]


def test_identical_payload_when_timestamp_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    graph = _sample_graph()
    save_graph(graph, path)
    first = path.read_text(encoding="utf-8")
    save_graph(graph, path)
    second = path.read_text(encoding="utf-8")
    assert first == second


def test_missing_file_raises_graph_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(GraphNotFoundError, match="not found"):
        load_graph(missing)


def test_invalid_json_raises_store_error(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(GraphStoreError, match="Invalid graph JSON"):
        load_graph(path)


def test_atomic_replace_overwrites_previous(tmp_path: Path) -> None:
    path = tmp_path / ".daygent" / "graph.json"
    first = Graph(nodes=[Node(id="a", name="a", type="sql_table")])
    second = Graph(nodes=[Node(id="b", name="b", type="sql_table")])
    save_graph(first, path)
    save_graph(second, path)
    loaded = load_graph(path)
    assert [node.id for node in loaded.nodes] == ["b"]


def test_failed_replace_keeps_previous_graph_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".daygent" / "graph.json"
    original = Graph(nodes=[Node(id="keep", name="keep", type="sql_table")])
    save_graph(original, path)
    before = path.read_text(encoding="utf-8")

    real_replace = Path.replace

    def boom(self: Path, target: Path | str) -> Path:
        if Path(target) == path:
            raise OSError("simulated interrupt")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="simulated interrupt"):
        save_graph(Graph(nodes=[Node(id="new", name="new", type="sql_table")]), path)

    assert path.read_text(encoding="utf-8") == before
    assert list(path.parent.glob("*.tmp")) == []
    assert load_graph(path).nodes[0].id == "keep"


def test_graph_store_default_factory(tmp_path: Path) -> None:
    store = GraphStore.default(tmp_path)
    assert store.path == tmp_path / ".daygent" / "graph.json"
    store.save(_sample_graph())
    assert store.load().version == "0.1"


def test_canonical_dict_shape() -> None:
    payload = canonical_graph_dict(_sample_graph())
    assert payload["version"] == "0.1"
    assert isinstance(payload["project"], dict)
    assert isinstance(payload["nodes"], list)
    assert isinstance(payload["edges"], list)
    assert set(payload["scan"]) >= {"timestamp", "files_scanned", "warnings"}
