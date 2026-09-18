"""Conservative Django ORM and raw-SQL lineage. No Django setup or imports."""

from __future__ import annotations

import ast
from pathlib import Path

from daygent.models import Confidence, EdgeType, Node, NodeType
from daygent.models.ids import django_model_qualname, make_django_model_id
from daygent.parsers.ast_literals import named_or_positional
from daygent.parsers.base import BaseParser, ParseContext, ParseResult
from daygent.parsers.python_static import (
    LineageSink,
    ScopeVisitor,
    attr_chain,
    imported_symbol,
    is_from_module,
    method_name,
    parse_python_source,
    static_string,
)
from daygent.parsers.sql_parser import looks_like_sql

_READS = frozenset(
    {
        "all",
        "filter",
        "exclude",
        "get",
        "first",
        "last",
        "exists",
        "count",
        "values",
        "values_list",
        "annotate",
        "aggregate",
        "select_related",
        "prefetch_related",
        "raw",
        "none",
        "iterator",
        "distinct",
        "order_by",
        "only",
        "defer",
    }
)
_WRITES = frozenset(
    {
        "create",
        "bulk_create",
        "bulk_update",
        "update",
        "get_or_create",
        "update_or_create",
        "delete",
    }
)


def _is_models_model(base: ast.expr, aliases: dict[str, str]) -> bool:
    """Return True when a class base is Django's models.Model."""
    chain = attr_chain(base)
    if not chain:
        return False
    if chain[-1] != "Model":
        return False
    root = chain[0]
    resolved = imported_symbol(aliases, root)
    if root == "models" or resolved.endswith(".models") or resolved.endswith(".Model"):
        return True
    return is_from_module(aliases, root, "django.db.models", "django.db")


def _meta_db_table(class_node: ast.ClassDef) -> str | None:
    """Return explicit Meta.db_table when it is a string literal."""
    for stmt in class_node.body:
        if not isinstance(stmt, ast.ClassDef) or stmt.name != "Meta":
            continue
        for item in stmt.body:
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(item, ast.Assign) and len(item.targets) == 1:
                target, value = item.targets[0], item.value
            elif isinstance(item, ast.AnnAssign) and item.value is not None:
                target, value = item.target, item.value
            if isinstance(target, ast.Name) and target.id == "db_table":
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    return value.value
    return None


def _objects_model_name(call: ast.Call) -> str | None:
    """Return Model from Model.objects.<method>(...)."""
    chain = attr_chain(call.func)
    if len(chain) < 3 or "objects" not in chain:
        return None
    objects_at = chain.index("objects")
    if objects_at == 0:
        return None
    return chain[objects_at - 1]


class _DjangoVisitor(ScopeVisitor):
    """Walk one module for Django model classes and manager calls."""

    def __init__(self, module: str, file_path: str, tree: ast.AST, sink: LineageSink) -> None:
        super().__init__(module, file_path, tree)
        self.sink = sink
        self.local_models: dict[str, str] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if any(_is_models_model(base, self.aliases) for base in node.bases):
            qual = django_model_qualname(self.module, node.name)
            model_id = self._ensure_model(qual, node.lineno)
            self.local_models[node.name] = model_id
            table = _meta_db_table(node)
            if table:
                table_id = self.sink.ensure_sql_table(table, node.lineno)
                self.sink.add_edge(
                    table_id,
                    model_id,
                    edge_type=EdgeType.READ_BY,
                    confidence=Confidence.HIGH,
                    evidence="django.Meta.db_table",
                    line=node.lineno,
                    operation="django.Meta.db_table",
                    framework="django",
                )
        super().visit_ClassDef(node)

    def visit_Call(self, node: ast.Call) -> None:
        self._handle_manager_call(node)
        self.generic_visit(node)

    def _model_id_for(self, class_name: str) -> str:
        if class_name in self.local_models:
            return self.local_models[class_name]
        imported = imported_symbol(self.aliases, class_name)
        if "." in imported:
            module, name = imported.rsplit(".", maxsplit=1)
            qual = django_model_qualname(module, name)
        else:
            qual = django_model_qualname(self.module, class_name)
        return self._ensure_model(qual, None)

    def _ensure_model(self, qualified: str, line: int | None) -> str:
        node_id = make_django_model_id(qualified)
        return self.sink.add_node(
            Node(
                id=node_id,
                name=qualified.rsplit(".", maxsplit=1)[-1],
                type=NodeType.DJANGO_MODEL,
                file_path=self.file_path,
                line_number=line,
                metadata={"qualified_name": qualified, "framework": "django"},
            )
        )

    def _handle_manager_call(self, node: ast.Call) -> None:
        name = method_name(node)
        model_name = _objects_model_name(node)
        if name is None or model_name is None or not model_name[:1].isupper():
            return
        if name == "raw":
            sql = static_string(named_or_positional(node, ("raw_query", "sql"), 0), self.constants)
            if sql and looks_like_sql(sql):
                self.sink.attach_sql(
                    sql, self, line=node.lineno, operation="django.objects.raw", framework="django"
                )
            model_id = self._model_id_for(model_name)
            transform = self.sink.ensure_transform(self, node.lineno)
            self.sink.add_edge(
                model_id,
                transform,
                edge_type=EdgeType.READ_BY,
                confidence=Confidence.MEDIUM,
                evidence="django.objects.raw",
                line=node.lineno,
                operation="django.objects.raw",
                framework="django",
            )
            return
        if name not in _READS and name not in _WRITES:
            return
        model_id = self._model_id_for(model_name)
        transform = self.sink.ensure_transform(self, node.lineno)
        if name in _WRITES:
            self.sink.add_edge(
                transform,
                model_id,
                edge_type=EdgeType.WRITES_TO,
                confidence=Confidence.MEDIUM,
                evidence=f"django.objects.{name}",
                line=node.lineno,
                operation=f"django.objects.{name}",
                framework="django",
            )
            return
        self.sink.add_edge(
            model_id,
            transform,
            edge_type=EdgeType.READ_BY,
            confidence=Confidence.MEDIUM,
            evidence=f"django.objects.{name}",
            line=node.lineno,
            operation=f"django.objects.{name}",
            framework="django",
        )


class DjangoParser(BaseParser):
    """Detect Django model classes and conservative ORM lineage from AST only."""

    name = "django"

    def supports(self, path: Path, context: ParseContext) -> bool:
        """Accept Python source files."""
        return path.suffix.lower() == ".py"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        """Extract Django model and ORM lineage from one module."""
        tree, module, relative, warnings = parse_python_source(path, context.root)
        if tree is None:
            return ParseResult(warnings=warnings)
        sink = LineageSink(module=module, file_path=relative, parser=self.name)
        _DjangoVisitor(module, relative, tree, sink).visit(tree)
        return ParseResult(nodes=list(sink.nodes.values()), edges=sink.edges, warnings=sink.warnings)
