"""Shared static AST helpers for Python data-lineage parsers. Never eval or exec."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from daygent.models import Confidence, Edge, EdgeType, Node, NodeType
from daygent.models.ids import (
    make_python_function_id,
    make_python_module_id,
    make_sql_table_id,
    make_temp_view_id,
    normalize_sql_table_name,
    posix_relpath,
)
from daygent.parsers.ast_literals import call_function_name, named_or_positional
from daygent.parsers.evidence import evidence_metadata
from daygent.parsers.python_parser import module_name_from_path
from daygent.parsers.sql_parser import SqlLineageFact, extract_sql_lineage, looks_like_sql

# A statically known literal: a string, a sequence of strings, or a str→str map.
LiteralValue = str | tuple[str, ...] | dict[str, str]

# Resolve a relation name appearing in code or SQL to a graph node id.
RelationResolver = Callable[[str, int | None], str]


def method_name(call: ast.Call) -> str | None:
    """Return the called attribute or name, if statically known."""
    return call_function_name(call.func)


def receiver(call: ast.Call) -> ast.expr | None:
    """Return the object a method is called on, if any."""
    if isinstance(call.func, ast.Attribute):
        return call.func.value
    return None


def attr_chain(node: ast.AST) -> list[str]:
    """Return attribute names from a Name/Attribute/Call chain, outer-first."""
    parts: list[str] = []
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        elif isinstance(current, ast.Call):
            current = current.func
        elif isinstance(current, ast.Name):
            parts.append(current.id)
            break
        else:
            break
    parts.reverse()
    return parts


def collect_import_aliases(tree: ast.AST) -> dict[str, str]:
    """Map local names to imported modules or qualified symbols."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", maxsplit=1)[0]
                aliases[local] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                aliases[local] = f"{node.module}.{alias.name}"
    return aliases


def module_of(aliases: dict[str, str], name: str) -> str:
    """Return the imported module path for a local name, else the name itself."""
    return aliases.get(name, name)


def imported_symbol(aliases: dict[str, str], name: str) -> str:
    """Return a fully qualified imported symbol when known."""
    return aliases.get(name, name)


def is_from_module(aliases: dict[str, str], name: str, *modules: str) -> bool:
    """Return True if `name` was imported from one of `modules`."""
    resolved = imported_symbol(aliases, name)
    for module in modules:
        if resolved == module or resolved.startswith(f"{module}."):
            return True
    return False


def root_name(node: ast.AST) -> str | None:
    """Return the leftmost Name in an attribute/call chain."""
    chain = attr_chain(node)
    return chain[0] if chain else None


def collect_string_constants(body: list[ast.stmt]) -> dict[str, str]:
    """Collect `NAME = \"literal\"` assignments in a statement list."""
    constants: dict[str, str] = {}
    for stmt in body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        if not isinstance(target, ast.Name):
            continue
        literal = _literal_join(value, {})
        if literal is not None:
            constants[target.id] = literal
    return constants


def _literal_join(node: ast.AST | None, constants: dict[str, str]) -> str | None:
    """Join statically known string pieces. Omit f-strings with interpolations."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_join(node.left, constants)
        right = _literal_join(node.right, constants)
        if left is not None and right is not None:
            return left + right
    return None


def static_string(node: ast.AST | None, constants: dict[str, str]) -> str | None:
    """Resolve a string from a literal, same-scope constant, or concatenation."""
    return _literal_join(node, constants)


def literal_collection(
    node: ast.AST | None,
    constants: dict[str, str],
    collections: dict[str, LiteralValue] | None = None,
) -> LiteralValue | None:
    """Resolve a literal string, str sequence, or str→str mapping.

    Only fully static values are returned. A single non-literal element makes
    the whole collection unknown, because a partially known loop would emit
    lineage that the code does not have.
    """
    if node is None:
        return None
    if isinstance(node, ast.Name) and collections is not None:
        known = collections.get(node.id)
        if known is not None:
            return known
    direct = _literal_join(node, constants)
    if direct is not None:
        return direct
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        items: list[str] = []
        for element in node.elts:
            value = _literal_join(element, constants)
            if value is None:
                return None
            items.append(value)
        return tuple(items)
    if isinstance(node, ast.Dict):
        mapping: dict[str, str] = {}
        for key, value in zip(node.keys, node.values, strict=False):
            if key is None:
                return None
            key_text = _literal_join(key, constants)
            value_text = _literal_join(value, constants)
            if key_text is None or value_text is None:
                return None
            mapping[key_text] = value_text
        return mapping
    return None


def collect_literal_collections(
    body: list[ast.stmt],
    constants: dict[str, str],
) -> dict[str, LiteralValue]:
    """Collect `NAME = {...}` / `NAME = [...]` literal assignments in a body."""
    found: dict[str, LiteralValue] = {}
    for stmt in body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        if not isinstance(target, ast.Name) or value is None:
            continue
        if isinstance(value, ast.Constant) or isinstance(value, ast.JoinedStr):
            continue
        literal = literal_collection(value, constants, found)
        if isinstance(literal, (tuple, dict)):
            found[target.id] = literal
    return found


def relation_key(name: str) -> str | None:
    """Case-folded relation name used to match code references against SQL."""
    try:
        return normalize_sql_table_name(name)
    except ValueError:
        return None


def sql_argument(node: ast.AST | None, constants: dict[str, str]) -> str | None:
    """Return literal SQL from a string or `text(\"...\")` argument."""
    direct = static_string(node, constants)
    if direct is not None:
        return direct if looks_like_sql(direct) else None
    if not isinstance(node, ast.Call):
        return None
    if call_function_name(node.func) != "text":
        return None
    inner = named_or_positional(node, ("text", "sql"), 0)
    value = static_string(inner, constants)
    if value is None or not looks_like_sql(value):
        return None
    return value


def parse_python_source(
    path: Path,
    root: Path,
) -> tuple[ast.AST | None, str, str, list[str]]:
    """Parse a .py file into an AST. Returns warnings on read/syntax errors."""
    relative = posix_relpath(path, root)
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, "", relative, [f"Warning: unable to read {relative}: {exc}"]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        loc = f"{relative}:{exc.lineno}" if exc.lineno else relative
        return None, "", relative, [f"Warning: Python syntax error in {loc}"]
    module = module_name_from_path(path, root)
    return tree, module, relative, []


class ScopeVisitor(ast.NodeVisitor):
    """Track class/function scope and same-scope string constants."""

    def __init__(self, module: str, file_path: str, tree: ast.AST) -> None:
        self.module = module
        self.module_id = make_python_module_id(module)
        self.file_path = file_path
        self.aliases = collect_import_aliases(tree)
        self.class_stack: list[str] = []
        self.func_stack: list[str] = []
        module_body = tree.body if isinstance(tree, ast.Module) else []
        module_constants = collect_string_constants(module_body)
        self._const_stack: list[dict[str, str]] = [module_constants]
        self._coll_stack: list[dict[str, LiteralValue]] = [
            collect_literal_collections(module_body, module_constants)
        ]

    @property
    def constants(self) -> dict[str, str]:
        """Merged constants, inner scope winning."""
        merged: dict[str, str] = {}
        for layer in self._const_stack:
            merged.update(layer)
        return merged

    @property
    def collections(self) -> dict[str, LiteralValue]:
        """Merged literal collections, inner scope winning."""
        merged: dict[str, LiteralValue] = {}
        for layer in self._coll_stack:
            merged.update(layer)
        return merged

    def push_bindings(self, bindings: dict[str, str]) -> None:
        """Push statically known name→string bindings, e.g. unrolled loop vars."""
        self._const_stack.append(dict(bindings))

    def pop_bindings(self) -> None:
        """Drop the innermost binding layer."""
        self._const_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self._push_scope(node.body)
        self.generic_visit(node)
        self._pop_scope()
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.func_stack.append(node.name)
        self._push_scope(node.body)
        self.generic_visit(node)
        self._pop_scope()
        self.func_stack.pop()

    def _push_scope(self, body: list[ast.stmt]) -> None:
        constants = collect_string_constants(body)
        self._const_stack.append(constants)
        self._coll_stack.append(collect_literal_collections(body, self.constants))

    def _pop_scope(self) -> None:
        self._coll_stack.pop()
        self._const_stack.pop()

    def transform_id(self) -> str:
        """Return the enclosing function id, else the module id."""
        if not self.func_stack:
            return self.module_id
        qualified = ".".join([*self.class_stack, *self.func_stack])
        return make_python_function_id(self.module, qualified)

    def transform_name(self) -> str:
        """Return the innermost function name or the module stem."""
        if self.func_stack:
            return self.func_stack[-1]
        return self.module.rsplit(".", maxsplit=1)[-1]


@dataclass
class LineageSink:
    """Collect data-lineage nodes and edges for one Python file."""

    module: str
    file_path: str
    parser: str
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _edge_keys: set[tuple[str, str, str]] = field(default_factory=set)

    @property
    def module_id(self) -> str:
        """Module node id for this file."""
        return make_python_module_id(self.module)

    def add_node(self, node: Node) -> str:
        """Merge `node` into the sink and return its id."""
        existing = self.nodes.get(node.id)
        self.nodes[node.id] = existing.merge(node) if existing else node
        return node.id

    def add_edge(
        self,
        source: str,
        target: str,
        *,
        edge_type: str,
        confidence: Confidence,
        evidence: str,
        line: int | None,
        operation: str,
        framework: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        """Append a deduplicated directed edge with static evidence."""
        if source == target:
            return
        key = (source, target, edge_type)
        meta = evidence_metadata(
            file_path=self.file_path,
            line_number=line,
            operation=operation,
            framework=framework,
            parser=self.parser,
        )
        if extra:
            meta.update(extra)
        edge = Edge(
            source=source,
            target=target,
            type=edge_type,
            confidence=confidence,
            evidence=evidence,
            metadata=meta,
        )
        if key in self._edge_keys:
            current = next(item for item in self.edges if item.key == key)
            self.edges[self.edges.index(current)] = current.merge(edge)
            return
        self._edge_keys.add(key)
        self.edges.append(edge)

    def ensure_transform(self, visitor: ScopeVisitor, line: int | None) -> str:
        """Ensure the enclosing function or module node exists."""
        node_id = visitor.transform_id()
        if node_id == self.module_id:
            node = Node(
                id=node_id,
                name=visitor.transform_name(),
                type=NodeType.PYTHON_MODULE,
                file_path=self.file_path,
                line_number=1,
                metadata={"python_file": True, "module": self.module},
            )
        else:
            qualified = ".".join([*visitor.class_stack, *visitor.func_stack])
            node = Node(
                id=node_id,
                name=visitor.transform_name(),
                type=NodeType.PYTHON_FUNCTION,
                file_path=self.file_path,
                line_number=line,
                metadata={"qualified_name": qualified},
            )
        return self.add_node(node)

    def ensure_sql_table(
        self,
        name: str,
        line: int | None,
        *,
        relation_ref: bool = False,
    ) -> str:
        """Ensure a canonical physical table node.

        `relation_ref` marks a name read from code rather than a declaration, so
        repository-level identity resolution can absorb it into a logical
        pipeline dataset declared in another file.
        """
        node_id = make_sql_table_id(name)
        short = name.split(".")[-1]
        metadata: dict[str, object] = {"table": name}
        if relation_ref:
            metadata["relation_ref"] = True
        return self.add_node(
            Node(
                id=node_id,
                name=short,
                type=NodeType.SQL_TABLE,
                file_path=self.file_path,
                line_number=line,
                metadata=metadata,
            )
        )

    def ensure_temp_view(
        self,
        module: str,
        name: str,
        line: int | None,
        *,
        created_by: str | None = None,
    ) -> str:
        """Ensure a Spark temp-view alias node. Not a physical table."""
        node_id = make_temp_view_id(module, name)
        metadata: dict[str, object] = {"temp_view": True, "view": name}
        if created_by:
            metadata["created_by"] = created_by
        return self.add_node(
            Node(
                id=node_id,
                name=name,
                type=NodeType.TEMP_VIEW,
                file_path=self.file_path,
                line_number=line,
                metadata=metadata,
            )
        )

    def sql_facts(
        self,
        sql: str,
        *,
        dialects: tuple[str | None, ...] | None = None,
    ) -> list[SqlLineageFact]:
        """Parse embedded SQL, recording a warning instead of raising."""
        facts, error = extract_sql_lineage(sql, dialects=dialects)
        if error:
            self.warnings.append(f"Warning: unable to parse SQL in {self.file_path}: {error}")
            return []
        return facts

    def attach_sql(
        self,
        sql: str,
        visitor: ScopeVisitor,
        *,
        line: int | None,
        operation: str,
        framework: str | None = None,
        resolve: RelationResolver | None = None,
        consumer: str | None = None,
        dialects: tuple[str | None, ...] | None = None,
    ) -> None:
        """Attach sqlglot lineage through a consuming node.

        `resolve` maps a relation name to a node id, so a parser can send temp
        views to alias nodes instead of fabricating physical tables. `consumer`
        overrides the enclosing Python transform, which is how a
        `spark.sql(...).createOrReplaceTempView(...)` chain routes its sources
        into the temp view rather than back through its own function.
        """
        facts = self.sql_facts(sql, dialects=dialects)
        if not facts:
            return
        relation = resolve or (lambda name, at: self.ensure_sql_table(name, at))
        target_node = consumer or self.ensure_transform(visitor, line)
        extra = {"sql": "embedded"}
        for fact in facts:
            for source in fact.sources:
                self.add_edge(
                    relation(source, line),
                    target_node,
                    edge_type=EdgeType.READ_BY,
                    confidence=Confidence.HIGH,
                    evidence=operation,
                    line=line,
                    operation=operation,
                    framework=framework,
                    extra=extra,
                )
            if fact.target:
                self.add_edge(
                    target_node,
                    relation(fact.target, line),
                    edge_type=EdgeType.WRITES_TO,
                    confidence=Confidence.HIGH,
                    evidence=operation,
                    line=line,
                    operation=operation,
                    framework=framework,
                    extra=extra,
                )
