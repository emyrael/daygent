"""Conservative SQLAlchemy model and DML lineage. No engine or metadata reflection."""

from __future__ import annotations

import ast
from pathlib import Path

from daygent.models import Confidence, EdgeType, Node, NodeType
from daygent.models.ids import make_sqlalchemy_model_id
from daygent.parsers.ast_literals import literal_str_dict, named_or_positional
from daygent.parsers.base import BaseParser, ParseContext, ParseResult
from daygent.parsers.python_static import (
    LineageSink,
    ScopeVisitor,
    attr_chain,
    imported_symbol,
    is_from_module,
    method_name,
    parse_python_source,
)

_READS = frozenset({"select", "query"})
_WRITES = frozenset({"insert", "update", "delete"})


def _tablename(class_node: ast.ClassDef) -> str | None:
    """Return __tablename__ when it is a string literal."""
    for stmt in class_node.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        if isinstance(target, ast.Name) and target.id == "__tablename__":
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    return None


def _table_schema(class_node: ast.ClassDef) -> str | None:
    """Return schema from a literal __table_args__ dict when present."""
    for stmt in class_node.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        if not isinstance(target, ast.Name) or target.id != "__table_args__":
            continue
        mapping, _complete = literal_str_dict(value)
        if "schema" in mapping:
            return mapping["schema"]
        if isinstance(value, ast.Tuple) and value.elts:
            mapping, _complete = literal_str_dict(value.elts[0])
            if "schema" in mapping:
                return mapping["schema"]
    return None


# `Model` is an ambiguous base name: SQLAlchemy declarative bases and Django's
# models.Model both end in it. Django classes are already claimed by the Django
# parser, and double-claiming them produces a duplicate orphan asset.
_DJANGO_MODEL_BASES = frozenset(
    {"models.Model", "db.models.Model", "django.db.models.Model"}
)
_DECLARATIVE_BASES = frozenset({"Base", "DeclarativeBase", "Model"})


def _looks_like_declarative(class_node: ast.ClassDef) -> bool:
    """Return True when the class looks like a SQLAlchemy declarative model."""
    # Django never uses __tablename__, so this alone is decisive.
    if _tablename(class_node) is not None:
        return True
    if class_node.name in _DECLARATIVE_BASES:
        return False
    for base in class_node.bases:
        chain = attr_chain(base)
        if not chain or chain[-1] not in _DECLARATIVE_BASES:
            continue
        if ".".join(chain) in _DJANGO_MODEL_BASES:
            continue
        return True
    return False


def _model_arg(call: ast.Call) -> str | None:
    """Return a simple class name passed to select/query/insert/update/delete."""
    arg = named_or_positional(call, ("entity", "table"), 0)
    if isinstance(arg, ast.Name):
        return arg.id
    if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
        # select(Customer.id) still refers to Customer
        if arg.value.id[:1].isupper():
            return arg.value.id
        return None
    return None


class _SqlAlchemyVisitor(ScopeVisitor):
    """Walk one module for declarative models and obvious ORM/DML calls."""

    def __init__(self, module: str, file_path: str, tree: ast.AST, sink: LineageSink) -> None:
        super().__init__(module, file_path, tree)
        self.sink = sink
        self.local_models: dict[str, str] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if _looks_like_declarative(node):
            qualified = f"{self.module}.{node.name}"
            model_id = self._ensure_model(qualified, node.lineno)
            self.local_models[node.name] = model_id
            table = _tablename(node)
            if table:
                schema = _table_schema(node)
                physical = f"{schema}.{table}" if schema else table
                table_id = self.sink.ensure_sql_table(physical, node.lineno)
                self.sink.add_edge(
                    table_id,
                    model_id,
                    edge_type=EdgeType.READ_BY,
                    confidence=Confidence.HIGH,
                    evidence="sqlalchemy.__tablename__",
                    line=node.lineno,
                    operation="sqlalchemy.__tablename__",
                    framework="sqlalchemy",
                )
        super().visit_ClassDef(node)

    def visit_Call(self, node: ast.Call) -> None:
        self._handle_call(node)
        self.generic_visit(node)

    def _ensure_model(self, qualified: str, line: int | None) -> str:
        node_id = make_sqlalchemy_model_id(qualified)
        return self.sink.add_node(
            Node(
                id=node_id,
                name=qualified.rsplit(".", maxsplit=1)[-1],
                type=NodeType.SQLALCHEMY_MODEL,
                file_path=self.file_path,
                line_number=line,
                metadata={"qualified_name": qualified, "framework": "sqlalchemy"},
            )
        )

    def _model_id_for(self, class_name: str) -> str:
        if class_name in self.local_models:
            return self.local_models[class_name]
        imported = imported_symbol(self.aliases, class_name)
        if "." in imported and not imported.startswith("sqlalchemy"):
            qualified = imported
        else:
            qualified = f"{self.module}.{class_name}"
        return self._ensure_model(qualified, None)

    def _handle_call(self, node: ast.Call) -> None:
        name = method_name(node)
        if name not in _READS and name not in _WRITES:
            return
        if name in {"select", "insert", "update", "delete"}:
            if isinstance(node.func, ast.Name) and not is_from_module(
                self.aliases, node.func.id, "sqlalchemy", "sqlalchemy.orm"
            ):
                # Bare names that were not imported from sqlalchemy are omitted.
                if node.func.id not in self.aliases:
                    return
        if name == "query":
            chain = attr_chain(node.func)
            if len(chain) < 2:
                return
        model_name = _model_arg(node)
        if model_name is None or not model_name[:1].isupper():
            return
        model_id = self._model_id_for(model_name)
        transform = self.sink.ensure_transform(self, node.lineno)
        operation = f"sqlalchemy.{name}"
        if name in _WRITES:
            self.sink.add_edge(
                transform,
                model_id,
                edge_type=EdgeType.WRITES_TO,
                confidence=Confidence.MEDIUM,
                evidence=operation,
                line=node.lineno,
                operation=operation,
                framework="sqlalchemy",
            )
            return
        self.sink.add_edge(
            model_id,
            transform,
            edge_type=EdgeType.READ_BY,
            confidence=Confidence.MEDIUM,
            evidence=operation,
            line=node.lineno,
            operation=operation,
            framework="sqlalchemy",
        )


class SqlAlchemyParser(BaseParser):
    """Detect SQLAlchemy declarative models and conservative select/DML calls."""

    name = "sqlalchemy"

    def supports(self, path: Path, context: ParseContext) -> bool:
        """Accept Python source files."""
        return path.suffix.lower() == ".py"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        """Extract SQLAlchemy model lineage from one module."""
        tree, module, relative, warnings = parse_python_source(path, context.root)
        if tree is None:
            return ParseResult(warnings=warnings)
        sink = LineageSink(module=module, file_path=relative, parser=self.name)
        _SqlAlchemyVisitor(module, relative, tree, sink).visit(tree)
        return ParseResult(nodes=list(sink.nodes.values()), edges=sink.edges, warnings=sink.warnings)
