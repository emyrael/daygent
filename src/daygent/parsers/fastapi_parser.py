"""FastAPI route detection from stdlib ast. Never imports FastAPI at runtime."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from daygent.models import Confidence, Edge, EdgeType, Node, NodeType
from daygent.models.ids import make_api_route_id, make_python_function_id, posix_relpath
from daygent.parsers.ast_literals import call_function_name, keyword_value, literal_string
from daygent.parsers.base import BaseParser, ParseContext, ParseResult
from daygent.parsers.evidence import evidence_metadata
from daygent.parsers.python_parser import module_name_from_path

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


def _join_paths(prefix: str, path: str) -> str:
    """Join a router prefix and a route path into one POSIX-style path."""
    left = prefix.strip() or "/"
    right = path.strip() or "/"
    if not left.startswith("/"):
        left = f"/{left}"
    if not right.startswith("/"):
        right = f"/{right}"
    if left == "/":
        return right
    return f"{left.rstrip('/')}{right}"


def _path_from_decorator(call: ast.Call) -> str | None:
    """Return a literal route path from a decorator Call, or None if dynamic."""
    if call.args:
        return literal_string(call.args[0])
    path_node = keyword_value(call, "path")
    return literal_string(path_node) if path_node is not None else None


@dataclass
class _RouteSpec:
    owner: str
    method: str
    path: str
    handler_id: str
    handler_name: str
    line_number: int


@dataclass
class _FastAPICollector(ast.NodeVisitor):
    """Collect FastAPI/APIRouter owners, literal routes, and include_router calls."""

    module: str
    file_path: str
    class_stack: list[str] = field(default_factory=list)
    func_stack: list[str] = field(default_factory=list)
    prefixes: dict[str, str] = field(default_factory=dict)
    routes: list[_RouteSpec] = field(default_factory=list)
    includes: list[tuple[str, str, str | None]] = field(default_factory=list)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._record_owner(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None and isinstance(node.target, ast.Name):
            self._record_owner([node.target], node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "include_router"
            and isinstance(node.func.value, ast.Name)
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            prefix_node = keyword_value(node, "prefix")
            prefix = literal_string(prefix_node) if prefix_node is not None else None
            if prefix_node is not None and prefix is None:
                prefix = ""
            self.includes.append((node.func.value.id, node.args[0].id, prefix))
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified = ".".join([*self.class_stack, *self.func_stack, node.name])
        handler_id = make_python_function_id(self.module, qualified)
        for decorator in node.decorator_list:
            spec = self._route_from_decorator(decorator, handler_id, node)
            if spec is not None:
                self.routes.append(spec)
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    def _record_owner(self, targets: list[ast.expr], value: ast.expr) -> None:
        if not isinstance(value, ast.Call):
            return
        callee = call_function_name(value.func)
        if callee not in {"FastAPI", "APIRouter"}:
            return
        prefix_node = keyword_value(value, "prefix")
        prefix = literal_string(prefix_node) if prefix_node is not None else None
        for target in targets:
            if isinstance(target, ast.Name):
                if prefix:
                    self.prefixes[target.id] = prefix

    def _route_from_decorator(
        self,
        decorator: ast.expr,
        handler_id: str,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> _RouteSpec | None:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            return None
        method = decorator.func.attr.lower()
        if method not in HTTP_METHODS:
            return None
        if not isinstance(decorator.func.value, ast.Name):
            return None
        path = _path_from_decorator(decorator)
        if path is None:
            return None
        return _RouteSpec(
            owner=decorator.func.value.id,
            method=method.upper(),
            path=path,
            handler_id=handler_id,
            handler_name=function.name,
            line_number=function.lineno,
        )


def _route_node(method: str, path: str, file_path: str, line_number: int, **meta: object) -> Node:
    """Build an api_route node with required method/path metadata."""
    normalized = path if path.startswith("/") else f"/{path}"
    return Node(
        id=make_api_route_id(method, normalized),
        name=f"{method.upper()} {normalized}",
        type=NodeType.API_ROUTE,
        file_path=file_path,
        line_number=line_number,
        metadata={"method": method.upper(), "path": normalized, **meta},
    )


def _handler_edge(handler_id: str, route_id: str, *, file_path: str, line_number: int) -> Edge:
    """Handler function is upstream of the route it serves."""
    return Edge(
        source=handler_id,
        target=route_id,
        type=EdgeType.INVOKED_BY,
        confidence=Confidence.HIGH,
        evidence="fastapi_decorator",
        metadata=evidence_metadata(file_path=file_path, line_number=line_number),
    )


class FastAPIParser(BaseParser):
    """Detect FastAPI/APIRouter HTTP routes from Python AST."""

    name = "fastapi"

    def supports(self, path: Path, context: ParseContext) -> bool:
        """Accept Python files; detection is heuristic and AST-only."""
        return path.suffix.lower() == ".py"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        """Extract literal routes, handlers, and static include_router mounts."""
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
        collector = _FastAPICollector(module=module, file_path=relative)
        collector.visit(tree)

        nodes: dict[str, Node] = {}
        edges: list[Edge] = []
        seen_edges: set[tuple[str, str, str]] = set()

        def add_route(spec: _RouteSpec, path: str, extra: dict[str, object] | None = None) -> Node:
            node = _route_node(spec.method, path, relative, spec.line_number, **(extra or {}))
            nodes[node.id] = node
            edge = _handler_edge(
                spec.handler_id, node.id, file_path=relative, line_number=spec.line_number
            )
            if edge.key not in seen_edges:
                seen_edges.add(edge.key)
                edges.append(edge)
            return node

        for spec in collector.routes:
            owner_prefix = collector.prefixes.get(spec.owner)
            declared = _join_paths(owner_prefix, spec.path) if owner_prefix else spec.path
            add_route(spec, declared, {"owner": spec.owner})

        for _app_name, router_name, prefix in collector.includes:
            router_routes = [item for item in collector.routes if item.owner == router_name]
            if prefix == "":
                continue
            for spec in router_routes:
                owner_prefix = collector.prefixes.get(spec.owner, "")
                base = _join_paths(owner_prefix, spec.path) if owner_prefix else spec.path
                if prefix:
                    mounted = _join_paths(prefix, base)
                    add_route(
                        spec,
                        mounted,
                        {"owner": spec.owner, "include_prefix": prefix},
                    )

        return ParseResult(nodes=list(nodes.values()), edges=edges)
