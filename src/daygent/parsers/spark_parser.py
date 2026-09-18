"""Static PySpark, DLT, and Lakeflow lineage. No Spark session or workspace calls.

Identity rules (locked):
- `@dlt.table` / `@dp.table` / `@dp.materialized_view` are persisted outputs and
  get the canonical physical identity `sql_table:<name>`, so another file doing
  `spark.read.table("<name>")` lands on the same node.
- `@dlt.view` and temporary views are logical and get `pipeline_dataset:<name>`.
- A relation name read from code is a reference, resolved against temp views,
  then locally declared logical datasets, then treated as a physical table.
  Repository-level resolution finishes the job across files.

Temp views are alias nodes, not tables. The producing side of a temp view
bypasses the enclosing function so that
`spark.sql(A).createOrReplaceTempView("s"); spark.sql(B) FROM s` yields
`sources(A) → s → f()` rather than a cycle through `f()`.
"""

from __future__ import annotations

import ast
from pathlib import Path

from daygent.models import Confidence, EdgeType, Node, NodeType
from daygent.models.ids import make_pipeline_dataset_id, make_sql_table_id
from daygent.parsers.ast_literals import keyword_value, named_or_positional
from daygent.parsers.base import BaseParser, ParseContext, ParseResult
from daygent.parsers.python_static import (
    LineageSink,
    ScopeVisitor,
    attr_chain,
    literal_collection,
    method_name,
    module_of,
    parse_python_source,
    receiver,
    relation_key,
    static_string,
)
from daygent.parsers.sql_parser import SPARK_DIALECTS, looks_like_sql

_SPARK_ROOTS = frozenset({"spark", "spark_session"})
_WRITE_METHODS = frozenset({"saveAsTable", "writeTo"})
_TEMP_VIEW_METHODS = frozenset(
    {
        "createOrReplaceTempView",
        "createTempView",
        "createGlobalTempView",
        "createOrReplaceGlobalTempView",
    }
)
_DLT_READS = frozenset({"read", "read_stream"})
_PIPELINE_KINDS = {
    "table": "table",
    "view": "view",
    "materialized_view": "materialized_view",
    "temporary_view": "temporary_view",
}
# Kinds that land in the catalog as queryable tables.
_PERSISTED_KINDS = frozenset({"table", "materialized_view"})

# A literal loop is unrolled only while it stays small; beyond this a repo is
# doing something generated and the lineage would be noise.
MAX_LOOP_UNROLL = 64


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
    """Walk one module for Spark table I/O, temp views, and pipeline declarations.

    Runs twice over the same tree: the first pass only fills `temp_views` and
    `declared` so that resolution does not depend on statement order, and the
    second pass emits lineage.
    """

    def __init__(
        self,
        module: str,
        file_path: str,
        tree: ast.AST,
        sink: LineageSink,
        *,
        temp_views: dict[str, str],
        declared: dict[str, str],
        emit: bool,
    ) -> None:
        super().__init__(module, file_path, tree)
        self.sink = sink
        self.temp_views = temp_views
        self.declared = declared
        self.emit = emit
        self._consumed: set[int] = set()
        self._alias_stack: list[dict[str, tuple[str, ...]]] = [{}]

    # -- scope tracking -----------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_pipeline_function(node)
        self._alias_stack.append({})
        self._visit_function(node)
        self._alias_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_pipeline_function(node)
        self._alias_stack.append({})
        self._visit_function(node)
        self._alias_stack.pop()

    @property
    def aliases_local(self) -> dict[str, tuple[str, ...]]:
        """DataFrame-producing local names in the innermost function."""
        return self._alias_stack[-1]

    # -- statements ---------------------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        if self.emit and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            origins = self._expr_origins(node.value)
            if origins:
                self.aliases_local[node.targets[0].id] = origins
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        bindings = self._loop_bindings(node)
        if bindings is None:
            self.generic_visit(node)
            return
        for binding in bindings:
            self.push_bindings(binding)
            for stmt in node.body:
                self.visit(stmt)
            self.pop_bindings()
        for stmt in node.orelse:
            self.visit(stmt)

    def _loop_bindings(self, node: ast.For) -> list[dict[str, str]] | None:
        """Unroll `for k, v in LITERAL.items()` and `for x in LITERAL` only."""
        constants = self.constants
        collections = self.collections
        iterated = node.iter
        accessor: str | None = None
        if isinstance(iterated, ast.Call):
            accessor = method_name(iterated)
            if accessor not in {"items", "keys", "values"}:
                return None
            iterated = receiver(iterated) or iterated
        literal = literal_collection(iterated, constants, collections)
        if literal is None or isinstance(literal, str):
            return None

        pairs: list[tuple[str, ...]]
        if isinstance(literal, dict):
            if accessor == "keys":
                pairs = [(key,) for key in literal]
            elif accessor == "values":
                pairs = [(value,) for value in literal.values()]
            else:
                pairs = [(key, value) for key, value in literal.items()]
        else:
            if accessor in {"items", "keys", "values"}:
                return None
            pairs = [(item,) for item in literal]

        if len(pairs) > MAX_LOOP_UNROLL:
            self.sink.warnings.append(
                f"Warning: skipped literal loop over {len(pairs)} items in "
                f"{self.file_path}:{node.lineno} (limit {MAX_LOOP_UNROLL})"
            )
            return None

        targets = node.target.elts if isinstance(node.target, ast.Tuple) else [node.target]
        names = [item.id for item in targets if isinstance(item, ast.Name)]
        if len(names) != len(targets):
            return None
        bindings: list[dict[str, str]] = []
        for pair in pairs:
            if len(names) != len(pair):
                return None
            bindings.append(dict(zip(names, pair, strict=True)))
        return bindings

    def visit_Call(self, node: ast.Call) -> None:
        if id(node) not in self._consumed:
            self._handle_call(node)
        self.generic_visit(node)

    # -- pipeline declarations ---------------------------------------------

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
            if kind not in _PERSISTED_KINDS:
                key = relation_key(dataset)
                if key:
                    self.declared[key] = dataset
            if not self.emit:
                continue
            self.func_stack.append(node.name)
            transform = self.sink.ensure_transform(self, node.lineno)
            dataset_id = self._ensure_dataset(dataset, kind, framework, node.lineno, node.name)
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
        """Create the declaration node, physical for tables, logical for views."""
        metadata: dict[str, object] = {
            "framework": framework,
            "pipeline_dataset": True,
            "dataset_kind": kind,
            "defining_function": function,
        }
        if kind in _PERSISTED_KINDS:
            node_id = make_sql_table_id(name)
            node_type = NodeType.SQL_TABLE
            metadata["table"] = name
        else:
            node_id = make_pipeline_dataset_id(name)
            node_type = NodeType.PIPELINE_DATASET
            metadata["dataset_declaration"] = True
        return self.sink.add_node(
            Node(
                id=node_id,
                name=name.split(".")[-1],
                type=node_type,
                file_path=self.file_path,
                line_number=line,
                metadata=metadata,
            )
        )

    # -- relation resolution ------------------------------------------------

    def _resolve_relation(self, name: str, line: int | None) -> str:
        """Map a relation name to a temp view, a declared dataset, or a table."""
        key = relation_key(name)
        if key is not None:
            view = self.temp_views.get(key)
            if view is not None:
                return self.sink.ensure_temp_view(self.module, view, line)
            dataset = self.declared.get(key)
            if dataset is not None:
                return self.sink.add_node(
                    Node(
                        id=make_pipeline_dataset_id(dataset),
                        name=dataset.split(".")[-1],
                        type=NodeType.PIPELINE_DATASET,
                        file_path=self.file_path,
                        line_number=line,
                        metadata={"dataset_declaration": True},
                    )
                )
        return self.sink.ensure_sql_table(name, line, relation_ref=True)

    def _expr_origins(self, expr: ast.expr | None, *, consume: bool = False) -> tuple[str, ...]:
        """Upstream relation ids feeding an expression, conservatively.

        Recognizes `spark.read.table(...)`, `spark.sql(...)`, `dlt.read(...)`,
        and local names previously bound to one of those. Everything else in the
        expression (withColumn, filter, joins, helper calls) is transparent.
        """
        if expr is None:
            return ()
        found: list[str] = []
        aliases = self.aliases_local
        for node in ast.walk(expr):
            if isinstance(node, ast.Name):
                found.extend(aliases.get(node.id, ()))
                continue
            if not isinstance(node, ast.Call):
                continue
            origins = self._call_origins(node)
            if origins is None:
                continue
            found.extend(origins)
            if consume:
                self._consumed.add(id(node))
        seen: set[str] = set()
        ordered: list[str] = []
        for node_id in found:
            if node_id in seen:
                continue
            seen.add(node_id)
            ordered.append(node_id)
        return tuple(ordered)

    def _call_origins(self, node: ast.Call) -> tuple[str, ...] | None:
        """Relations produced by one call, or None when it is not a read."""
        constants = self.constants
        if _is_spark_table_call(node, self.aliases):
            table = static_string(named_or_positional(node, ("tableName", "table"), 0), constants)
            if not table:
                return None
            return (self._resolve_relation(table, node.lineno),)
        if _is_spark_sql_call(node, self.aliases):
            sql = static_string(named_or_positional(node, ("sqlQuery", "sql"), 0), constants)
            if not sql or not looks_like_sql(sql):
                return None
            sources: list[str] = []
            for fact in self.sink.sql_facts(sql, dialects=SPARK_DIALECTS):
                for source in fact.sources:
                    sources.append(self._resolve_relation(source, node.lineno))
            return tuple(sources)
        recv = receiver(node)
        if method_name(node) in _DLT_READS and isinstance(recv, ast.Name):
            if _pipeline_framework(self.aliases, recv.id) != "dlt":
                return None
            dataset = static_string(named_or_positional(node, ("name",), 0), constants)
            if not dataset:
                return None
            return (self._resolve_relation(dataset, node.lineno),)
        return None

    # -- calls --------------------------------------------------------------

    def _handle_call(self, node: ast.Call) -> None:
        name = method_name(node)
        constants = self.constants
        if name in _TEMP_VIEW_METHODS:
            self._handle_temp_view(node)
            return
        if not self.emit:
            return
        if name in _WRITE_METHODS:
            table = static_string(named_or_positional(node, ("name", "table"), 0), constants)
            if table:
                self._write_table(table, node, f"spark.{name}")
            return
        if _is_spark_table_call(node, self.aliases):
            table = static_string(named_or_positional(node, ("tableName", "table"), 0), constants)
            if table:
                chain = attr_chain(node.func)
                if "readStream" in chain:
                    operation = "spark.readStream.table"
                elif "read" in chain:
                    operation = "spark.read.table"
                else:
                    operation = "spark.table"
                self._read_relation(table, node, operation)
            return
        if _is_spark_sql_call(node, self.aliases):
            sql = static_string(named_or_positional(node, ("sqlQuery", "sql"), 0), constants)
            if sql and looks_like_sql(sql):
                self.sink.attach_sql(
                    sql,
                    self,
                    line=node.lineno,
                    operation="spark.sql",
                    framework="pyspark",
                    resolve=self._resolve_relation,
                    dialects=SPARK_DIALECTS,
                )
            return
        recv = receiver(node)
        if name in _DLT_READS and isinstance(recv, ast.Name):
            framework = _pipeline_framework(self.aliases, recv.id)
            if framework != "dlt":
                return
            dataset = static_string(named_or_positional(node, ("name",), 0), constants)
            if dataset:
                self._read_relation(dataset, node, f"dlt.{name}")

    def _handle_temp_view(self, node: ast.Call) -> None:
        """Register a temp view and route its producing lineage into the alias."""
        view = static_string(named_or_positional(node, ("name", "viewName"), 0), self.constants)
        if not view:
            return
        key = relation_key(view)
        if key is None:
            return
        self.temp_views[key] = view
        if not self.emit:
            return
        origins = self._expr_origins(receiver(node), consume=True)
        view_id = self.sink.ensure_temp_view(
            self.module, view, node.lineno, created_by=self.transform_name()
        )
        for origin in origins:
            self.sink.add_edge(
                origin,
                view_id,
                edge_type=EdgeType.READ_BY,
                confidence=Confidence.HIGH,
                evidence=f"spark.{method_name(node)}",
                line=node.lineno,
                operation=f"spark.{method_name(node)}",
                framework="pyspark",
            )

    def _read_relation(self, table: str, call: ast.Call, operation: str) -> None:
        transform = self.sink.ensure_transform(self, call.lineno)
        self.sink.add_edge(
            self._resolve_relation(table, call.lineno),
            transform,
            edge_type=EdgeType.READ_BY,
            confidence=Confidence.HIGH,
            evidence=operation,
            line=call.lineno,
            operation=operation,
            framework="dlt" if operation.startswith("dlt.") else "pyspark",
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
        temp_views: dict[str, str] = {}
        declared: dict[str, str] = {}
        scratch = LineageSink(module=module, file_path=relative, parser=self.name)
        _SparkVisitor(
            module,
            relative,
            tree,
            scratch,
            temp_views=temp_views,
            declared=declared,
            emit=False,
        ).visit(tree)
        sink = LineageSink(module=module, file_path=relative, parser=self.name)
        _SparkVisitor(
            module,
            relative,
            tree,
            sink,
            temp_views=temp_views,
            declared=declared,
            emit=True,
        ).visit(tree)
        return ParseResult(nodes=list(sink.nodes.values()), edges=sink.edges, warnings=sink.warnings)
