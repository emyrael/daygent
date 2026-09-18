"""Literal embedded SQL in Python DB-API, pandas, and text() calls. No DB access."""

from __future__ import annotations

import ast
from pathlib import Path

from daygent.models import Confidence, EdgeType
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
    sql_argument,
    static_string,
)
from daygent.parsers.sql_parser import looks_like_sql

_EXECUTE = frozenset({"execute", "executemany"})
_PANDAS_SQL = frozenset({"read_sql", "read_sql_query", "read_sql_table"})


def _is_pandas_call(call: ast.Call, aliases: dict[str, str]) -> str | None:
    """Return the pandas SQL helper name when the call is statically pandas."""
    name = method_name(call)
    if name not in _PANDAS_SQL:
        return None
    chain = attr_chain(call.func)
    if not chain:
        return None
    root = chain[0]
    if len(chain) == 1:
        return name if is_from_module(aliases, root, "pandas") else None
    if is_from_module(aliases, root, "pandas") or root in {"pd", "pandas"}:
        return name
    if imported_symbol(aliases, root) in {"pandas", "pandas.pandas"}:
        return name
    return None


class _PythonSqlVisitor(ScopeVisitor):
    """Walk one module for literal SQL arguments to common Python DB APIs."""

    def __init__(self, module: str, file_path: str, tree: ast.AST, sink: LineageSink) -> None:
        super().__init__(module, file_path, tree)
        self.sink = sink

    def visit_Call(self, node: ast.Call) -> None:
        self._handle_call(node)
        self.generic_visit(node)

    def _handle_call(self, node: ast.Call) -> None:
        pandas_fn = _is_pandas_call(node, self.aliases)
        if pandas_fn == "read_sql_table":
            self._pandas_table(node)
            return
        if pandas_fn in {"read_sql", "read_sql_query"}:
            sql = static_string(named_or_positional(node, ("sql",), 0), self.constants)
            if sql and looks_like_sql(sql):
                self.sink.attach_sql(
                    sql,
                    self,
                    line=node.lineno,
                    operation=f"pandas.{pandas_fn}",
                    framework="pandas",
                )
            return
        name = method_name(node)
        if name in _EXECUTE:
            sql = sql_argument(named_or_positional(node, ("sql", "query", "operation"), 0), self.constants)
            if sql:
                self.sink.attach_sql(
                    sql,
                    self,
                    line=node.lineno,
                    operation=f"cursor.{name}" if name == "executemany" else "cursor.execute",
                    framework="python-sql",
                )
            return
        if name == "text":
            sql = static_string(named_or_positional(node, ("text", "sql"), 0), self.constants)
            if sql and looks_like_sql(sql):
                self.sink.attach_sql(
                    sql, self, line=node.lineno, operation="text", framework="sqlalchemy"
                )

    def _pandas_table(self, node: ast.Call) -> None:
        table = static_string(named_or_positional(node, ("table_name", "table"), 0), self.constants)
        schema = static_string(named_or_positional(node, ("schema",), None), self.constants)
        if table is None:
            return
        qualified = f"{schema}.{table}" if schema else table
        transform = self.sink.ensure_transform(self, node.lineno)
        table_id = self.sink.ensure_sql_table(qualified, node.lineno)
        self.sink.add_edge(
            table_id,
            transform,
            edge_type=EdgeType.READ_BY,
            confidence=Confidence.HIGH,
            evidence="pandas.read_sql_table",
            line=node.lineno,
            operation="pandas.read_sql_table",
            framework="pandas",
        )


class PythonSqlParser(BaseParser):
    """Detect literal SQL in Python execute/text/pandas calls. No DB drivers."""

    name = "python_sql"

    def supports(self, path: Path, context: ParseContext) -> bool:
        """Accept Python source files."""
        return path.suffix.lower() == ".py"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        """Extract embedded-SQL lineage from one module."""
        tree, module, relative, warnings = parse_python_source(path, context.root)
        if tree is None:
            return ParseResult(warnings=warnings)
        sink = LineageSink(module=module, file_path=relative, parser=self.name)
        _PythonSqlVisitor(module, relative, tree, sink).visit(tree)
        return ParseResult(nodes=list(sink.nodes.values()), edges=sink.edges, warnings=sink.warnings)
