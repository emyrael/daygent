"""LangGraph AST detector tests. No LangGraph runtime execution."""

from __future__ import annotations

from pathlib import Path

from daygent.config import default_config
from daygent.parsers import AIParser, PythonParser, default_registry
from daygent.parsers.base import ParseContext, ParserRegistry
from daygent.scanner import Scanner

GRAPH_SOURCE = '''
from langgraph.graph import StateGraph

def retrieve(state):
    return state

def generate(state):
    return state

def route(state):
    return "generate"

graph = StateGraph(dict)
graph.add_node("retrieve", retrieve)
graph.add_node("generate", generate)
graph.add_edge("retrieve", "generate")
graph.add_conditional_edges(
    "retrieve",
    route,
    {"continue": "generate", "again": "retrieve"},
)
dynamic = "skip_me"
graph.add_node(dynamic, retrieve)
graph.add_edge(dynamic, "generate")
'''


def _parse(tmp_path: Path, source: str = GRAPH_SOURCE):
    path = tmp_path / "graph.py"
    path.write_text(source, encoding="utf-8")
    context = ParseContext(root=tmp_path, config=default_config())
    return AIParser().parse(path, context)


def test_add_node_and_literal_add_edge(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    ids = {node.id for node in result.nodes}
    assert "langgraph_node:retrieve" in ids
    assert "langgraph_node:generate" in ids
    edge = next(
        item
        for item in result.edges
        if item.source == "langgraph_node:retrieve"
        and item.target == "langgraph_node:generate"
        and item.evidence == "langgraph_add_edge"
    )
    assert edge.confidence == "high"
    assert edge.type == "routes_to"


def test_conditional_edges_emit_mapping_and_metadata(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    retrieve = next(node for node in result.nodes if node.id == "langgraph_node:retrieve")
    assert retrieve.metadata.get("conditional") is True
    destinations = {
        edge.target
        for edge in result.edges
        if edge.source == "langgraph_node:retrieve" and edge.evidence == "langgraph_conditional_edge"
    }
    assert "langgraph_node:generate" in destinations


def test_unresolved_conditional_keeps_metadata(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
graph.add_node("retrieve", retrieve)
graph.add_conditional_edges("retrieve", route)
""",
    )
    retrieve = next(node for node in result.nodes if node.id == "langgraph_node:retrieve")
    assert retrieve.metadata.get("conditional_unresolved") is True


def test_dynamic_names_are_not_invented(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    ids = {node.id for node in result.nodes}
    assert "langgraph_node:skip_me" not in ids
    assert not any(node.id.endswith("dynamic") for node in result.nodes)


def test_coexists_with_python_parser(tmp_path: Path) -> None:
    (tmp_path / "graph.py").write_text(GRAPH_SOURCE, encoding="utf-8")
    context = ParseContext(root=tmp_path, config=default_config())
    registry = ParserRegistry([PythonParser(), AIParser()])
    names = [parser.name for parser in registry.matching(tmp_path / "graph.py", context)]
    assert names == ["python", "ai"]
    report = Scanner(default_registry()).scan(tmp_path)
    ids = {node.id for node in report.graph.nodes}
    assert "python_function:graph.retrieve" in ids
    assert "langgraph_node:retrieve" in ids
    assert any(
        edge.source == "python_function:graph.retrieve"
        and edge.target == "langgraph_node:retrieve"
        for edge in report.graph.edges
    )
