"""AI-stack detectors: LangGraph, LLMs, embeddings, and vector stores."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from daygent.models import Confidence, Edge, EdgeType, Node, NodeType
from daygent.models.ids import make_langgraph_node_id, make_python_function_id, posix_relpath
from daygent.parsers.ai_constructors import ConstructorDetector
from daygent.parsers.ast_literals import keyword_value, literal_str_dict, literal_string
from daygent.parsers.base import BaseParser, ParseContext, ParseResult
from daygent.parsers.evidence import evidence_metadata
from daygent.parsers.python_parser import module_name_from_path


def _langgraph_node(name: str, file_path: str, line: int | None, **meta: object) -> Node:
    """Build a langgraph_node vertex."""
    return Node(
        id=make_langgraph_node_id(name),
        name=name,
        type=NodeType.LANGGRAPH_NODE,
        file_path=file_path,
        line_number=line if line and line > 0 else None,
        metadata={"langgraph": True, **meta},
    )


def _flow_edge(
    source: str, target: str, evidence: str, *, file_path: str | None = None, **meta: object
) -> Edge:
    """A → B means B depends on A (control flows from source to target)."""
    extra = dict(meta) if meta else {}
    extra.update(evidence_metadata(file_path=file_path))
    return Edge(
        source=make_langgraph_node_id(source),
        target=make_langgraph_node_id(target),
        type=EdgeType.ROUTES_TO,
        confidence=Confidence.HIGH if evidence == "langgraph_add_edge" else Confidence.MEDIUM,
        evidence=evidence,
        metadata=extra,
    )


@dataclass
class LangGraphDetector:
    """Detect add_node / add_edge / add_conditional_edges from a Python AST."""

    module: str
    file_path: str
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _edge_keys: set[tuple[str, ...]] = field(default_factory=set)

    def detect(self, tree: ast.AST) -> ParseResult:
        """Walk the tree and emit LangGraph nodes and edges."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                if attr == "add_node":
                    self._add_node(node)
                elif attr == "add_edge":
                    self._add_edge(node)
                elif attr == "add_conditional_edges":
                    self._add_conditional_edges(node)
        return ParseResult(
            nodes=list(self.nodes.values()),
            edges=self.edges,
            warnings=self.warnings,
        )

    def _ensure(self, name: str, line: int | None, **meta: object) -> Node:
        node = _langgraph_node(name, self.file_path, line, **meta)
        existing = self.nodes.get(node.id)
        if existing is None:
            self.nodes[node.id] = node
            return node
        if meta:
            existing.metadata.update(meta)
        return existing

    def _add_flow(self, source: str, target: str, evidence: str, **meta: object) -> None:
        self._ensure(source, None)
        self._ensure(target, None)
        edge = _flow_edge(source, target, evidence, file_path=self.file_path, **meta)
        if (edge.key + (evidence,)) in self._edge_keys:
            return
        self._edge_keys.add(edge.key + (evidence,))
        self.edges.append(edge)

    def _add_node(self, call: ast.Call) -> None:
        name = None
        if call.args:
            name = literal_string(call.args[0])
        else:
            name_node = keyword_value(call, "node") or keyword_value(call, "key")
            name = literal_string(name_node) if name_node is not None else None
        if name is None:
            return
        line = getattr(call, "lineno", None)
        graph_node = self._ensure(name, line)
        action = None
        if len(call.args) >= 2 and isinstance(call.args[1], ast.Name):
            action = call.args[1].id
        else:
            action_node = keyword_value(call, "action")
            if isinstance(action_node, ast.Name):
                action = action_node.id
        if action:
            handler_id = make_python_function_id(self.module, action)
            edge = Edge(
                source=handler_id,
                target=graph_node.id,
                type=EdgeType.INVOKED_BY,
                confidence=Confidence.HIGH,
                evidence="langgraph_add_node",
                metadata=evidence_metadata(
                    file_path=self.file_path,
                    line_number=line if isinstance(line, int) else None,
                    reference=name,
                ),
            )
            if edge.key not in self._edge_keys:
                self._edge_keys.add(edge.key)
                self.edges.append(edge)

    def _add_edge(self, call: ast.Call) -> None:
        source = literal_string(call.args[0]) if call.args else None
        target = literal_string(call.args[1]) if len(call.args) >= 2 else None
        if source is None:
            start = keyword_value(call, "start_key")
            source = literal_string(start) if start is not None else None
        if target is None:
            end = keyword_value(call, "end_key")
            target = literal_string(end) if end is not None else None
        if source is None or target is None:
            return
        self._add_flow(source, target, "langgraph_add_edge")

    def _add_conditional_edges(self, call: ast.Call) -> None:
        source = literal_string(call.args[0]) if call.args else None
        if source is None:
            source_node = keyword_value(call, "source")
            source = literal_string(source_node) if source_node is not None else None
        if source is None:
            return
        mapping_node = call.args[2] if len(call.args) >= 3 else keyword_value(call, "path_map")
        mapping, complete = literal_str_dict(mapping_node)
        line = getattr(call, "lineno", None)
        meta: dict[str, object] = {"conditional": True}
        if not complete or mapping_node is None:
            meta["conditional_unresolved"] = True
        self._ensure(source, line, **meta)
        for destination in mapping.values():
            self._add_flow(
                source,
                destination,
                "langgraph_conditional_edge",
                conditional=True,
            )


class AIParser(BaseParser):
    """Pluggable AI detectors: LangGraph plus table-driven constructors."""

    name = "ai"

    def __init__(self) -> None:
        self.detectors = (LangGraphDetector, ConstructorDetector)

    def supports(self, path: Path, context: ParseContext) -> bool:
        """Accept Python files so LangGraph can coexist with PythonParser."""
        return path.suffix.lower() == ".py"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        """Run registered AI detectors on one file."""
        relative = posix_relpath(path, context.root)
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            return ParseResult(warnings=[f"Warning: unable to read {relative}: {exc}"])
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            loc = f"{relative}:{exc.lineno}" if exc.lineno else relative
            return ParseResult(warnings=[f"Warning: Python syntax error in {loc}"])

        module = module_name_from_path(path, context.root)
        nodes: list[Node] = []
        edges: list[Edge] = []
        warnings: list[str] = []
        for detector_cls in self.detectors:
            result = detector_cls(module=module, file_path=relative).detect(tree)
            nodes.extend(result.nodes)
            edges.extend(result.edges)
            warnings.extend(result.warnings)
        return ParseResult(nodes=nodes, edges=edges, warnings=warnings)
