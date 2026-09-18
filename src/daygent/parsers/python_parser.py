"""Python parser using stdlib ast only. Never import or execute scanned modules."""

from __future__ import annotations

import ast
from pathlib import Path

from daygent.models import Confidence, Edge, EdgeType, Node, NodeType
from daygent.models.ids import make_python_function_id, make_python_module_id, posix_relpath
from daygent.parsers.base import BaseParser, ParseContext, ParseResult
from daygent.parsers.evidence import evidence_metadata


def module_name_from_path(path: Path, root: Path) -> str:
    """Turn a repo-relative .py path into a dotted module name.

    `services/recommend.py` → `services.recommend`, matching `import services.recommend`.
    """
    relative = posix_relpath(path, root)
    if relative.endswith(".py"):
        relative = relative[:-3]
    parts = [part for part in relative.split("/") if part]
    # src-layout packages import as `daygent.cli`, not `src.daygent.cli`.
    if len(parts) > 1 and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return path.stem if path.stem != "__init__" else path.parent.name or "app"
    return ".".join(parts)


def _qualified_function_name(class_stack: list[str], func_stack: list[str], name: str) -> str:
    """Build Class.outer.inner style names relative to the module."""
    return ".".join([*class_stack, *func_stack, name])


class _DefinitionCollector(ast.NodeVisitor):
    """First pass: module, class, and nested function definitions."""

    def __init__(self, module: str, file_path: str) -> None:
        self.module = module
        self.file_path = file_path
        self.class_stack: list[str] = []
        self.func_stack: list[str] = []
        self.nodes: list[Node] = []
        self.module_funcs: dict[str, str] = {}
        self.nested: dict[str, dict[str, str]] = {}
        self.class_methods: dict[str, dict[str, str]] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._register(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._register(node)

    def _register(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified = _qualified_function_name(self.class_stack, self.func_stack, node.name)
        func_id = make_python_function_id(self.module, qualified)
        self.nodes.append(
            Node(
                id=func_id,
                name=node.name,
                type=NodeType.PYTHON_FUNCTION,
                file_path=self.file_path,
                line_number=node.lineno,
                metadata={"qualified_name": qualified},
            )
        )
        if self.func_stack:
            parent = ".".join([*self.class_stack, *self.func_stack])
            self.nested.setdefault(parent, {})[node.name] = func_id
        elif self.class_stack:
            self.class_methods.setdefault(self.class_stack[-1], {})[node.name] = func_id
        else:
            self.module_funcs[node.name] = func_id
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()


class _CallCollector(ast.NodeVisitor):
    """Second pass: locally resolvable calls become callee → caller invoked_by."""

    def __init__(
        self,
        module_id: str,
        defs: _DefinitionCollector,
    ) -> None:
        self.module_id = module_id
        self.defs = defs
        self.class_stack: list[str] = []
        self.func_stack: list[str] = []
        self.edges: list[Edge] = []
        self._seen: set[tuple[str, str]] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        callee = self._resolve_call(node.func)
        caller = self._current_caller()
        if callee and caller and callee != caller:
            key = (callee, caller)
            if key not in self._seen:
                self._seen.add(key)
                self.edges.append(
                    Edge(
                        source=callee,
                        target=caller,
                        type=EdgeType.INVOKED_BY,
                        confidence=Confidence.HIGH,
                        evidence="ast.Call",
                        metadata=evidence_metadata(
                            file_path=self.defs.file_path,
                            line_number=node.lineno,
                        ),
                    )
                )
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    def _current_caller(self) -> str:
        if not self.func_stack:
            return self.module_id
        qualified = ".".join([*self.class_stack, *self.func_stack])
        return make_python_function_id(self.defs.module, qualified)

    def _resolve_call(self, func: ast.expr) -> str | None:
        """Resolve only static local names and self.method. Omit dynamic calls."""
        if isinstance(func, ast.Name):
            return self._resolve_name(func.id)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "self" and self.class_stack:
                return self.defs.class_methods.get(self.class_stack[-1], {}).get(func.attr)
            return None
        return None

    def _resolve_name(self, name: str) -> str | None:
        if self.func_stack:
            parent = ".".join([*self.class_stack, *self.func_stack])
            nested = self.defs.nested.get(parent, {}).get(name)
            if nested:
                return nested
        if self.class_stack:
            method = self.defs.class_methods.get(self.class_stack[-1], {}).get(name)
            if method:
                return method
        return self.defs.module_funcs.get(name)


def _import_module_name(current_module: str, node: ast.ImportFrom) -> str | None:
    """Resolve an ImportFrom target module, including relative imports."""
    package = current_module.split(".")[:-1]
    if node.level:
        if node.level - 1 > len(package):
            return None
        base = package[: len(package) - (node.level - 1)]
        if node.module:
            return ".".join([*base, *node.module.split(".")]) if base else node.module
        return ".".join(base) if base else None
    return node.module


def collect_import_edges(
    tree: ast.AST,
    module: str,
    module_id: str,
    file_path: str,
) -> tuple[list[Node], list[Edge]]:
    """Emit imported-module nodes and imported → importer edges."""
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        imported: list[str] = []
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names if alias.name]
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            resolved = _import_module_name(module, node)
            if node.module is None and node.level and resolved is not None:
                imported = [
                    f"{resolved}.{alias.name}" if resolved else alias.name
                    for alias in node.names
                    if alias.name
                ]
            elif resolved:
                imported = [resolved]
        for name in imported:
            if name == "__future__":
                continue
            imported_id = make_python_module_id(name)
            nodes[imported_id] = Node(
                id=imported_id,
                name=name.rsplit(".", maxsplit=1)[-1],
                type=NodeType.PYTHON_MODULE,
                metadata={"imported": True, "module": name},
            )
            key = (imported_id, module_id)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                Edge(
                    source=imported_id,
                    target=module_id,
                    type=EdgeType.IMPORTED_BY,
                    confidence=Confidence.HIGH,
                    evidence="ast.Import",
                    metadata=evidence_metadata(file_path=file_path),
                )
            )
    return list(nodes.values()), edges


class PythonParser(BaseParser):
    """Parse .py files with stdlib ast. No eval, exec, or importlib."""

    name = "python"

    def supports(self, path: Path, context: ParseContext) -> bool:
        """Accept Python source files."""
        return path.suffix.lower() == ".py"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        """Extract modules, functions, imports, and local calls."""
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
        module_id = make_python_module_id(module)
        module_node = Node(
            id=module_id,
            name=module.rsplit(".", maxsplit=1)[-1],
            type=NodeType.PYTHON_MODULE,
            file_path=relative,
            line_number=1,
            metadata={"python_file": True, "module": module},
        )
        defs = _DefinitionCollector(module, relative)
        defs.visit(tree)
        calls = _CallCollector(module_id, defs)
        calls.visit(tree)
        imported_nodes, import_edges = collect_import_edges(tree, module, module_id, relative)
        return ParseResult(
            nodes=[module_node, *defs.nodes, *imported_nodes],
            edges=[*import_edges, *calls.edges],
        )
