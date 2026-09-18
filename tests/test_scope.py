"""Data/AI lineage projection tests. Language-agnostic at the graph layer."""

from __future__ import annotations

from pathlib import Path

from daygent.config import default_config
from daygent.graph.scope import (
    ANCHOR_TYPES,
    is_anchor_type,
    is_code_type,
    project_lineage_graph,
)
from daygent.models import Edge, Graph, Node, ScanMetadata
from daygent.parsers import PythonParser
from daygent.parsers.base import ParseContext
from daygent.scanner import Scanner

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lineage_app"


def _graph(nodes: list[Node], edges: list[Edge]) -> Graph:
    """Build a candidate graph with a stable scan timestamp."""
    return Graph(
        nodes=nodes,
        edges=edges,
        scan=ScanMetadata(timestamp="2026-09-18T12:00:00Z", files_scanned=len(nodes)),
    )


def _node(node_id: str, node_type: str, name: str | None = None) -> Node:
    """Build a test node from a typed id."""
    return Node(id=node_id, name=name or node_id.split(":")[-1], type=node_type)


def test_code_type_is_language_agnostic() -> None:
    assert is_code_type("python_function")
    assert is_code_type("python_module")
    assert is_code_type("go_function")
    assert not is_code_type("llm")
    assert not is_code_type("api_route")
    assert ANCHOR_TYPES == {
        "sql_table",
        "dbt_model",
        "dbt_source",
        "langgraph_node",
        "llm",
        "embedding_model",
        "vector_store",
        "vector_collection",
        "external_system",
        "pipeline_dataset",
        "django_model",
        "sqlalchemy_model",
    }
    assert is_anchor_type("vector_store")


def test_unrelated_code_is_omitted_from_projection() -> None:
    graph = _graph(
        [
            _node("vector_store:qdrant", "vector_store"),
            _node("llm:openai:gpt-5", "llm"),
            _node("go_function:retrieve", "go_function"),
            _node("go_function:answer", "go_function"),
            _node("go_function:format_date", "go_function"),
            _node("go_function:calculate_discount", "go_function"),
            _node("go_module:utils", "go_module"),
        ],
        [
            Edge(
                source="vector_store:qdrant",
                target="go_function:retrieve",
                type="retrieves_from",
            ),
            Edge(
                source="go_function:retrieve",
                target="go_function:answer",
                type="invoked_by",
            ),
            Edge(source="go_function:answer", target="llm:openai:gpt-5", type="invokes"),
            Edge(
                source="go_function:format_date",
                target="go_function:retrieve",
                type="invoked_by",
            ),
        ],
    )
    projected = project_lineage_graph(graph)
    ids = {node.id for node in projected.nodes}
    assert "go_function:format_date" not in ids
    assert "go_function:calculate_discount" not in ids
    assert "go_module:utils" not in ids
    assert "go_function:retrieve" in ids
    assert "go_function:answer" in ids
    assert "vector_store:qdrant" in ids
    assert "llm:openai:gpt-5" in ids


def test_connector_path_between_anchors_is_kept() -> None:
    graph = _graph(
        [
            _node("vector_store:qdrant", "vector_store"),
            _node("go_function:retrieve", "go_function"),
            _node("go_function:pipeline", "go_function"),
            _node("go_function:answer", "go_function"),
            _node("llm:openai:gpt-5", "llm"),
        ],
        [
            Edge(
                source="vector_store:qdrant",
                target="go_function:retrieve",
                type="retrieves_from",
            ),
            Edge(
                source="go_function:retrieve",
                target="go_function:pipeline",
                type="invoked_by",
            ),
            Edge(
                source="go_function:pipeline",
                target="go_function:answer",
                type="invoked_by",
            ),
            Edge(source="go_function:answer", target="llm:openai:gpt-5", type="invokes"),
        ],
    )
    projected = project_lineage_graph(graph)
    ids = {node.id for node in projected.nodes}
    assert ids == {
        "vector_store:qdrant",
        "go_function:retrieve",
        "go_function:pipeline",
        "go_function:answer",
        "llm:openai:gpt-5",
    }
    pairs = [(edge.source, edge.target) for edge in projected.edges]
    assert ("vector_store:qdrant", "go_function:retrieve") in pairs
    assert ("go_function:answer", "llm:openai:gpt-5") in pairs


def test_relevant_api_route_kept_unrelated_route_omitted() -> None:
    graph = _graph(
        [
            _node("llm:openai:gpt-5", "llm"),
            _node("python_function:app.ask", "python_function"),
            _node("python_function:app.health", "python_function"),
            _node("api_route:POST:/ask", "api_route"),
            _node("api_route:GET:/health", "api_route"),
        ],
        [
            Edge(
                source="python_function:app.ask",
                target="llm:openai:gpt-5",
                type="invokes",
            ),
            Edge(
                source="python_function:app.ask",
                target="api_route:POST:/ask",
                type="invoked_by",
            ),
            Edge(
                source="python_function:app.health",
                target="api_route:GET:/health",
                type="invoked_by",
            ),
        ],
    )
    projected = project_lineage_graph(graph)
    ids = {node.id for node in projected.nodes}
    assert "api_route:POST:/ask" in ids
    assert "python_function:app.ask" in ids
    assert "api_route:GET:/health" not in ids
    assert "python_function:app.health" not in ids


def test_projection_does_not_reverse_edges() -> None:
    graph = _graph(
        [
            _node("sql_table:raw_users", "sql_table"),
            _node("dbt_model:stg_users", "dbt_model"),
        ],
        [Edge(source="sql_table:raw_users", target="dbt_model:stg_users", type="feeds")],
    )
    projected = project_lineage_graph(graph)
    assert len(projected.edges) == 1
    edge = projected.edges[0]
    assert edge.source == "sql_table:raw_users"
    assert edge.target == "dbt_model:stg_users"


def test_projection_is_deterministic_and_idempotent() -> None:
    graph = _graph(
        [
            _node("llm:openai:gpt-5", "llm"),
            _node("python_function:app.ask", "python_function"),
            _node("python_function:app.noise", "python_function"),
            _node("api_route:POST:/ask", "api_route"),
        ],
        [
            Edge(
                source="python_function:app.ask",
                target="llm:openai:gpt-5",
                type="invokes",
            ),
            Edge(
                source="python_function:app.ask",
                target="api_route:POST:/ask",
                type="invoked_by",
            ),
        ],
    )
    first = project_lineage_graph(graph).to_canonical_dict()
    second = project_lineage_graph(graph).to_canonical_dict()
    again = project_lineage_graph(project_lineage_graph(graph)).to_canonical_dict()
    assert first == second == again


def test_python_parser_still_exposes_generic_symbols(tmp_path: Path) -> None:
    source = (FIXTURE / "utils.py").read_text(encoding="utf-8")
    path = tmp_path / "utils.py"
    path.write_text(source, encoding="utf-8")
    result = PythonParser().parse(
        path, ParseContext(root=tmp_path, config=default_config())
    )
    names = {node.name for node in result.nodes if node.type == "python_function"}
    assert "slugify" in names
    assert "unused_helper" in names


def test_lineage_fixture_scan_keeps_relevant_paths_only() -> None:
    report = Scanner().scan(FIXTURE)
    ids = {node.id for node in report.graph.nodes}
    assert "python_function:utils.slugify" not in ids
    assert "python_function:utils.unused_helper" not in ids
    assert "python_module:utils" not in ids
    assert "python_function:rag_app.format_date" not in ids
    assert "python_function:rag_app.calculate_discount" not in ids
    assert "python_function:rag_app.health" not in ids
    assert "api_route:GET:/health" not in ids
    assert "python_function:agent.unused_router" not in ids

    assert "python_function:rag_app.retrieve_documents" in ids
    assert "python_function:rag_app.answer_question" in ids
    assert "python_function:rag_app.ask" in ids
    assert "api_route:POST:/ask" in ids
    assert "llm:openai:gpt-5" in ids
    assert "vector_store:qdrant" in ids
    assert "vector_collection:company_docs" in ids
    assert "external_system:api.stripe.com" in ids
    assert "langgraph_node:retrieve" in ids
    assert "langgraph_node:generate" in ids
    assert "python_function:agent.retrieve" in ids
    assert "dbt_model:stg_docs" in ids
    assert "dbt_source:raw.docs" in ids

    pairs = {(edge.source, edge.target) for edge in report.graph.edges}
    assert ("vector_store:qdrant", "vector_collection:company_docs") in pairs
    # A store or model the function merely uses is an input to that function, so
    # it points at the function and impact flows outward from the dependency.
    assert ("vector_store:qdrant", "python_function:rag_app.retrieve_documents") in pairs
    assert ("llm:openai:gpt-5", "python_function:rag_app.answer_question") in pairs
    assert (
        "python_function:rag_app.retrieve_documents",
        "python_function:rag_app.answer_question",
    ) in pairs
    assert ("python_function:rag_app.answer_question", "python_function:rag_app.ask") in pairs
    assert ("python_function:rag_app.ask", "api_route:POST:/ask") in pairs
    assert ("langgraph_node:retrieve", "langgraph_node:generate") in pairs
    assert ("dbt_source:raw.docs", "dbt_model:stg_docs") in pairs
    for edge in report.graph.edges:
        assert edge.source in ids and edge.target in ids


def test_scan_without_daygent_yml_is_scoped(tmp_path: Path) -> None:
    (tmp_path / "noise.py").write_text(
        "def format_date(value):\n    return value\n",
        encoding="utf-8",
    )
    (tmp_path / "ai.py").write_text(
        "def answer():\n    return ChatOpenAI(model=\"gpt-5\")\n",
        encoding="utf-8",
    )
    assert not (tmp_path / "daygent.yml").exists()
    report = Scanner().scan(tmp_path)
    ids = {node.id for node in report.graph.nodes}
    assert "python_function:noise.format_date" not in ids
    assert "python_module:noise" not in ids
    assert "llm:openai:gpt-5" in ids
    assert "python_function:ai.answer" in ids


def test_fixture_rescan_is_deterministic() -> None:
    first = Scanner().scan(FIXTURE).graph.to_canonical_dict()
    second = Scanner().scan(FIXTURE).graph.to_canonical_dict()
    first["scan"]["timestamp"] = second["scan"]["timestamp"] = "stable"
    assert first == second
