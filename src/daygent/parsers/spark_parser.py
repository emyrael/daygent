"""Static PySpark, DLT, and Lakeflow lineage. No Spark session or workspace calls."""

from __future__ import annotations

import ast
from pathlib import Path

from daygent.models import Confidence, EdgeType, Node, NodeType
from daygent.models.ids import make_pipeline_dataset_id
from daygent.parsers.ast_literals import keyword_value, named_or_positional
from daygent.parsers.base import BaseParser, ParseContext, ParseResult
from daygent.parsers.python_static import (
    LineageSink,
    ScopeVisitor,
    attr_chain,
    is_from_module,
    method_name,
    module_of,
    parse_python_source,
    receiver,
    static_string,
)
from daygent.parsers.sql_parser import looks_like_sql

_SPARK_ROOTS = frozenset({"spark", "spark_session"})
_WRITE_METHODS = frozenset({"saveAsTable", "writeTo"})
_DLT_READS = frozenset({"read", "read_stream"})
_PIPELINE_KINDS = {
    "table": "table",
    "view": "view",
    "materialized_view": "materialized_view",
    "temporary_view": "temporary_view",
}


def _is_spark_table_call(call: ast.Call, aliases: dict[str, str]) -> bool:
    """Return True for spark.table / spark.read.table / spark.readStream.table."""
    if method_name(call) != "table":
        return False
    chain = attr_chain(call.func)
    if "read" in chain or "readStream" in chain:
        return True
    root = chain[0] if chain else None
    if root is None:
        return False
    resolved = module_of(aliases, root)
    return root in _SPARK_ROOTS or resolved.endswith("SparkSession")


def _is_spark_sql_call(call: ast.Call, aliases: dict[str, str]) -> bool:
    """Return True for spark.sql(...) style calls."""
    if method_name(call) != "sql":
        return False
    chain = attr_chain(call.func)
    root = chain[0] if chain else None
    if root in _SPARK_ROOTS:
        return True
    if root is None:
        return False
    resolved = module_of(aliases, root)
    return "pyspark" in resolved or resolved.endswith("SparkSession")


def _pipeline_framework(aliases: dict[str, str], root: str) -> str | None:
    """Return dlt or lakeflow when `root` refers to those APIs."""
    resolved = module_of(aliases, root)
    if root == "dlt" or resolved == "dlt" or resolved.startswith("dlt."):
        return "dlt"
    if (
        root in {"dp", "pipelines"}
        or "pyspark.pipelines" in resolved
        or resolved.endswith(".pipelines")
    ):
        return "lakeflow"
    return None


class _SparkVisitor(ScopeVisitor):
    """Walk one module for Spark table I/O and pipeline declarations."""

    def __init__(self, module: str, file_path: str, tree: ast.AST, sink: LineageSink) -> None:
        super().__init__(module, file_path, tree)
        self.sink = sink

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_pipeline_function(node)
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_pipeline_function(node)
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        self._handle_call(node)
        self.generic_visit(node)

    def _handle_pipeline_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            parsed = self._pipeline_decorator(decorator)
            if parsed is None:
                continue
            framework, kind, explicit = parsed
            dataset = explicit or node.name
            self.func_stack.append(node.name)
            transform = self.sink.ensure_transform(self, node.lineno)
            dataset_id = self._ensure_dataset(
                dataset, kind, framework, node.lineno, node.name
            )
            self.sink.add_edge(
                transform,
                dataset_id,
                edge_type=EdgeType.WRITES_TO,
                confidence=Confidence.HIGH,
                evidence=f"{framework}.{kind}",
                line=node.lineno,
                operation=f"@{framework}.{kind}",
                framework=framework,
            )
            self.func_stack.pop()

    def _pipeline_decorator(
        self,
        decorator: ast.expr,
    ) -> tuple[str, str, str | None] | None:
        call = decorator if isinstance(decorator, ast.Call) else None
        func = call.func if call is not None else decorator
        chain = attr_chain(func)
        if len(chain) < 2:
            return None
        root, kind = chain[0], chain[-1]
        if kind not in _PIPELINE_KINDS:
            return None
        framework = _pipeline_framework(self.aliases, root)
        if framework is None:
            return None
        name: str | None = None
        if call is not None:
            name_node = keyword_value(call, "name")
            if name_node is not None:
                name = static_string(name_node, self.constants)
                if name is None:
                    return None
        return framework, _PIPELINE_KINDS[kind], name

    def _ensure_dataset(
        self,
        name: str,
        kind: str,
        framework: str,
        line: int,
        function: str,
    ) -> str:
        node_id = make_pipeline_dataset_id(name)
        return self.sink.add_node(
            Node(
                id=node_id,
                name=name.split(".")[-1],
                type=NodeType.PIPELINE_DATASET,
                file_path=self.file_path,
                line_number=line,
                metadata={
                    "framework": framework,
                    "dataset_kind": kind,
                    "defining_function": function,
                },
            )
        )

    def _handle_call(self, node: ast.Call) -> None:
        constants = self.constants
        name = method_name(node)
        if name in _WRITE_METHODS:
            table = static_string(named_or_positional(node, ("name", "table"), 0), constants)
            if table:
                self._write_table(table, node, f"spark.{name}")
            return
        if _is_spark_table_call(node, self.aliases):
            table = static_string(named_or_positional(node, ("tableName", "table"), 0), constants)
            if table:
                op = "spark.read.table" if "read" in attr_chain(node.func) else "spark.table"
                if "readStream" in attr_chain(node.func):
                    op = "spark.readStream.table"
                self._read_table(table, node, op)
            return
        if _is_spark_sql_call(node, self.aliases):
            sql = static_string(named_or_positional(node, ("sqlQuery", "sql"), 0), constants)
            if sql and looks_like_sql(sql):
                self.sink.attach_sql(
                    sql, self, line=node.lineno, operation="spark.sql", framework="pyspark"
                )
            return
        recv = receiver(node)
        if name in _DLT_READS and isinstance(recv, ast.Name):
            framework = _pipeline_framework(self.aliases, recv.id)
            if framework != "dlt":
                return
            dataset = static_string(named_or_positional(node, ("name",), 0), constants)
            if not dataset:
                return
            transform = self.sink.ensure_transform(self, node.lineno)
            source_id = self._ensure_dataset(
                dataset, "table", "dlt", node.lineno, self.transform_name()
            )
            self.sink.add_edge(
                source_id,
                transform,
                edge_type=EdgeType.READ_BY,
                confidence=Confidence.HIGH,
                evidence=f"dlt.{name}",
                line=node.lineno,
                operation=f"dlt.{name}",
                framework="dlt",
            )

    def _read_table(self, table: str, call: ast.Call, operation: str) -> None:
        transform = self.sink.ensure_transform(self, call.lineno)
        table_id = self.sink.ensure_sql_table(table, call.lineno)
        self.sink.add_edge(
            table_id,
            transform,
            edge_type=EdgeType.READ_BY,
            confidence=Confidence.HIGH,
            evidence=operation,
            line=call.lineno,
            operation=operation,
            framework="pyspark",
        )

    def _write_table(self, table: str, call: ast.Call, operation: str) -> None:
        transform = self.sink.ensure_transform(self, call.lineno)
        table_id = self.sink.ensure_sql_table(table, call.lineno)
        self.sink.add_edge(
            transform,
            table_id,
            edge_type=EdgeType.WRITES_TO,
            confidence=Confidence.HIGH,
            evidence=operation,
            line=call.lineno,
            operation=operation,
            framework="pyspark",
        )


class SparkParser(BaseParser):
    """Detect PySpark table I/O and DLT/Lakeflow declarations from AST only."""

    name = "spark"

    def supports(self, path: Path, context: ParseContext) -> bool:
        """Accept Python source files."""
        return path.suffix.lower() == ".py"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        """Extract Spark/DLT/Lakeflow lineage from one module."""
        tree, module, relative, warnings = parse_python_source(path, context.root)
        if tree is None:
            return ParseResult(warnings=warnings)
        sink = LineageSink(module=module, file_path=relative, parser=self.name)
        _SparkVisitor(module, relative, tree, sink).visit(tree)
        return ParseResult(nodes=list(sink.nodes.values()), edges=sink.edges, warnings=sink.warnings)
