"""SQL lineage parser via sqlglot. Parse text only; never connect to a database."""

from __future__ import annotations

from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from daygent.models import Confidence, Edge, EdgeType, Node, NodeType
from daygent.models.ids import make_sql_table_id, normalize_sql_table_name, posix_relpath
from daygent.parsers.base import BaseParser, ParseContext, ParseResult
from daygent.parsers.evidence import evidence_metadata

_DIALECTS = (None, "postgres", "duckdb", "tsql", "mysql", "spark")


def looks_like_jinja_sql(source: str) -> bool:
    """Return True when SQL contains Jinja/dbt constructs sqlglot must not parse.

    Issue #13 will claim these files. The generic SQL parser must not emit
    malformed-SQL warnings for them.
    """
    return "{{" in source or "{%" in source or "{#" in source


def _table_name(table: exp.Table) -> str | None:
    """Return a dotted catalog.db.table name, or None if unnamed."""
    parts: list[str] = []
    catalog = table.catalog
    db = table.db
    name = table.name
    if catalog:
        parts.append(str(catalog))
    if db:
        parts.append(str(db))
    if name:
        parts.append(str(name))
    if not parts:
        return None
    try:
        return normalize_sql_table_name(".".join(parts))
    except ValueError:
        return None


def _write_target(statement: exp.Expression) -> str | None:
    """Return the physical table being created, inserted, updated, or merged."""
    table: exp.Table | None = None
    if isinstance(statement, exp.Create):
        this = statement.this
        if isinstance(this, exp.Schema):
            this = this.this
        if isinstance(this, exp.Table):
            table = this
    elif isinstance(statement, (exp.Insert, exp.Update, exp.Merge, exp.Delete)):
        this = statement.this
        if isinstance(this, exp.Table):
            table = this
        elif isinstance(this, exp.Schema) and isinstance(this.this, exp.Table):
            table = this.this
    if table is None:
        return None
    return _table_name(table)


def _cte_names(statement: exp.Expression) -> set[str]:
    """Collect WITH aliases so they are not emitted as physical tables."""
    names: set[str] = set()
    for cte in statement.find_all(exp.CTE):
        alias = cte.alias
        if not alias:
            continue
        try:
            names.add(normalize_sql_table_name(str(alias)))
        except ValueError:
            continue
    return names


def _read_tables(statement: exp.Expression, cte_names: set[str], target: str | None) -> list[str]:
    """Physical tables read by FROM/JOIN/USING, excluding CTEs and the write target."""
    found: list[str] = []
    seen: set[str] = set()
    for table in statement.find_all(exp.Table):
        name = _table_name(table)
        if name is None or name in cte_names or name == target or name in seen:
            continue
        seen.add(name)
        found.append(name)
    return found


def parse_sql_statements(sql: str) -> tuple[list[exp.Expression], str | None]:
    """Parse SQL with sqlglot, trying a few dialects. Last error message on failure."""
    last_error: str | None = None
    for dialect in _DIALECTS:
        try:
            statements = sqlglot.parse(sql, dialect=dialect)
        except (ParseError, TokenError, ValueError) as exc:
            last_error = str(exc).split("\n", maxsplit=1)[0]
            continue
        return [item for item in statements if item is not None], None
    return [], last_error or "unable to parse SQL"


def lineage_from_sql(
    sql: str,
    *,
    file_path: str,
) -> tuple[list[Node], list[Edge], list[str]]:
    """Build table nodes and read_by edges from SQL text."""
    statements, error = parse_sql_statements(sql)
    if error:
        return [], [], [f"Warning: unable to parse SQL in {file_path}: {error}"]
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    seen_edges: set[tuple[str, str]] = set()
    warnings: list[str] = []

    def ensure_table(name: str) -> Node:
        node_id = make_sql_table_id(name)
        existing = nodes.get(node_id)
        if existing:
            return existing
        node = Node(
            id=node_id,
            name=name.split(".")[-1],
            type=NodeType.SQL_TABLE,
            file_path=file_path,
            metadata={"sql_file": True, "table": name},
        )
        nodes[node_id] = node
        return node

    for statement in statements:
        ctes = _cte_names(statement)
        target = _write_target(statement)
        sources = _read_tables(statement, ctes, target)
        for source in sources:
            ensure_table(source)
        if target:
            target_node = ensure_table(target)
            for source in sources:
                source_id = make_sql_table_id(source)
                key = (source_id, target_node.id)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                line = getattr(statement, "line", None)
                edges.append(
                    Edge(
                        source=source_id,
                        target=target_node.id,
                        type=EdgeType.READ_BY,
                        confidence=Confidence.HIGH,
                        evidence="sqlglot",
                        metadata=evidence_metadata(
                            file_path=file_path,
                            line_number=line if isinstance(line, int) else None,
                        ),
                    )
                )
        elif not sources and not ctes:
            kind = statement.key or statement.__class__.__name__
            if kind not in {"set", "command", "semicolon"}:
                warnings.append(
                    f"Warning: unsupported or empty SQL statement in {file_path} ({kind})"
                )
    return list(nodes.values()), edges, warnings


class SqlParser(BaseParser):
    """Parse .sql files with sqlglot. No warehouse connections or SQL execution."""

    name = "sql"

    def supports(self, path: Path, context: ParseContext) -> bool:
        """Accept plain SQL files. Jinja/dbt models are left for a later parser."""
        if path.suffix.lower() != ".sql":
            return False
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            return True
        return not looks_like_jinja_sql(source)

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        """Extract table lineage from a SQL file."""
        relative = posix_relpath(path, context.root)
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            return ParseResult(warnings=[f"Warning: unable to read {relative}: {exc}"])
        if looks_like_jinja_sql(source):
            return ParseResult()
        nodes, edges, warnings = lineage_from_sql(source, file_path=relative)
        return ParseResult(nodes=nodes, edges=edges, warnings=warnings)
