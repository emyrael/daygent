"""Literal external HTTP host detection. AST only; no network calls."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from daygent.models import Confidence, Edge, EdgeType, Node, NodeType
from daygent.models.ids import (
    make_external_system_id,
    make_python_function_id,
    posix_relpath,
)
from daygent.parsers.ast_literals import literal_string, named_or_positional
from daygent.parsers.base import BaseParser, ParseContext, ParseResult
from daygent.parsers.evidence import evidence_metadata
from daygent.parsers.python_parser import module_name_from_path

HTTP_SCHEMES = frozenset({"http", "https"})
SIMPLE_HTTP_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)
REQUEST_METHODS = frozenset({"request"})


@dataclass(frozen=True)
class HttpCallSpec:
    """How to find a URL argument on a Call."""

    methods: frozenset[str]
    url_position: int
    url_keys: tuple[str, ...] = ("url",)


HTTP_CALL_SPECS: tuple[HttpCallSpec, ...] = (
    HttpCallSpec(SIMPLE_HTTP_METHODS, url_position=0),
    HttpCallSpec(REQUEST_METHODS, url_position=1),
)


def hostname_from_url(url: str) -> str | None:
    """Extract a hostname from a literal absolute http(s) URL. No I/O."""
    text = url.strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme.lower() not in HTTP_SCHEMES:
        return None
    host = parsed.hostname
    if not host:
        return None
    return host.lower()


def _url_expression(call: ast.Call, spec: HttpCallSpec) -> ast.expr | None:
    """Return the URL argument expression for a matching HTTP call spec."""
    return named_or_positional(call, spec.url_keys, spec.url_position)


def _spec_for_attr(attr: str) -> HttpCallSpec | None:
    """Return the HTTP call spec for an attribute name, if any."""
    for spec in HTTP_CALL_SPECS:
        if attr in spec.methods:
            return spec
    return None


def _url_from_call(call: ast.Call) -> str | None:
    """Return a literal absolute URL from get/post/request-style calls."""
    if not isinstance(call.func, ast.Attribute):
        return None
    spec = _spec_for_attr(call.func.attr)
    if spec is None:
        return None
    node = _url_expression(call, spec)
    if node is None:
        return None
    return literal_string(node)


@dataclass
class LiteralHttpDetector:
    """Create external_system nodes from literal absolute HTTP URLs."""

    module: str
    file_path: str
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _edge_keys: set[tuple[str, str, str]] = field(default_factory=set)
    class_stack: list[str] = field(default_factory=list)
    func_stack: list[str] = field(default_factory=list)

    def detect(self, tree: ast.AST) -> ParseResult:
        """Walk the tree and emit hosts plus function → host edges."""
        self.visit(tree)
        return ParseResult(
            nodes=list(self.nodes.values()),
            edges=self.edges,
            warnings=self.warnings,
        )

    def visit(self, node: ast.AST) -> None:
        method = getattr(self, f"visit_{type(node).__name__}", self.generic_visit)
        method(node)

    def generic_visit(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            self.visit(child)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        url = _url_from_call(node)
        if url is not None:
            host = hostname_from_url(url)
            if host is not None:
                self._emit_host(host, node, url)
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    def _current_function_id(self) -> str | None:
        if not self.func_stack:
            return None
        qualified = ".".join([*self.class_stack, *self.func_stack])
        return make_python_function_id(self.module, qualified)

    def _emit_host(self, host: str, call: ast.Call, url: str) -> None:
        line = getattr(call, "lineno", None)
        node_id = make_external_system_id(host)
        existing = self.nodes.get(node_id)
        host_node = Node(
            id=node_id,
            name=host,
            type=NodeType.EXTERNAL_SYSTEM,
            file_path=self.file_path,
            line_number=line if line and line > 0 else None,
            metadata={"hostname": host, "scheme": urlparse(url).scheme.lower()},
        )
        self.nodes[node_id] = existing.merge(host_node) if existing else host_node
        func_id = self._current_function_id()
        if func_id is None:
            return
        qualified = ".".join([*self.class_stack, *self.func_stack])
        stub = Node(
            id=func_id,
            name=self.func_stack[-1],
            type=NodeType.PYTHON_FUNCTION,
            file_path=self.file_path,
            metadata={"qualified_name": qualified},
        )
        current = self.nodes.get(func_id)
        self.nodes[func_id] = current.merge(stub) if current else stub
        edge = Edge(
            source=func_id,
            target=node_id,
            type=EdgeType.DEPENDS_ON,
            confidence=Confidence.MEDIUM,
            evidence="http_call",
            metadata=evidence_metadata(file_path=self.file_path, line_number=line),
        )
        if edge.key not in self._edge_keys:
            self._edge_keys.add(edge.key)
            self.edges.append(edge)


class HttpParser(BaseParser):
    """Detect literal external HTTP hosts. Provider detectors can be appended."""

    name = "http"

    def __init__(self) -> None:
        self.detectors = (LiteralHttpDetector,)

    def supports(self, path: Path, context: ParseContext) -> bool:
        """Accept Python files so HTTP detection coexists with other parsers."""
        return path.suffix.lower() == ".py"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        """Run registered HTTP detectors on one file. Never fetches URLs."""
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
