"""SQL lineage parser tests using sqlglot. No dbt ref/source, no DB connections."""

from __future__ import annotations

from pathlib import Path

from daygent.config import default_config
from daygent.parsers import SqlParser, default_registry, looks_like_jinja_sql
from daygent.parsers.base import ParseContext
from daygent.scanner import Scanner


def _parse(tmp_path: Path, source: str, name: str = "query.sql"):
    """Write SQL and parse it."""
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    context = ParseContext(root=tmp_path, config=default_config())
    return SqlParser().parse(path, context)


def _edge_pairs(result) -> set[tuple[str, str]]:
    return {(edge.source, edge.target) for edge in result.edges}


def test_select_from_emits_table_nodes(tmp_path: Path) -> None:
    result = _parse(tmp_path, "SELECT * FROM customers;")
    ids = {node.id for node in result.nodes}
    assert "sql_table:customers" in ids
    assert result.edges == []


def test_join_and_create_table_as(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
        CREATE TABLE customer_summary AS
        SELECT *
        FROM customers c
        JOIN orders o ON c.id = o.customer_id;
        """,
    )
    pairs = _edge_pairs(result)
    assert ("sql_table:customers", "sql_table:customer_summary") in pairs
    assert ("sql_table:orders", "sql_table:customer_summary") in pairs
    assert all(edge.type == "read_by" for edge in result.edges)


def test_create_view(tmp_path: Path) -> None:
    result = _parse(tmp_path, "CREATE VIEW active AS SELECT * FROM users;")
    assert ("sql_table:users", "sql_table:active") in _edge_pairs(result)


def test_insert_update_merge(tmp_path: Path) -> None:
    insert = _parse(tmp_path, "INSERT INTO dest SELECT * FROM src;", "insert.sql")
    assert ("sql_table:src", "sql_table:dest") in _edge_pairs(insert)
    update = _parse(
        tmp_path,
        "UPDATE dest SET x = src.x FROM src WHERE dest.id = src.id;",
        "update.sql",
    )
    assert ("sql_table:src", "sql_table:dest") in _edge_pairs(update)
    merge = _parse(
        tmp_path,
        """
        MERGE INTO dest USING src ON dest.id = src.id
        WHEN MATCHED THEN UPDATE SET x = src.x
        WHEN NOT MATCHED THEN INSERT (id, x) VALUES (src.id, src.x);
        """,
        "merge.sql",
    )
    assert ("sql_table:src", "sql_table:dest") in _edge_pairs(merge)


def test_cte_is_not_a_physical_table(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
        CREATE TABLE summary AS
        WITH active_users AS (
            SELECT * FROM users
        )
        SELECT * FROM active_users;
        """,
    )
    ids = {node.id for node in result.nodes}
    assert "sql_table:users" in ids
    assert "sql_table:summary" in ids
    assert "sql_table:active_users" not in ids
    assert ("sql_table:users", "sql_table:summary") in _edge_pairs(result)


def test_malformed_sql_warns(tmp_path: Path) -> None:
    result = _parse(tmp_path, "SELECT FROM !!!")
    assert result.nodes == []
    assert any("unable to parse SQL" in warning for warning in result.warnings)


def test_jinja_sql_is_left_for_dbt_parser(tmp_path: Path) -> None:
    source = "select * from {{ ref('users') }}\n"
    assert looks_like_jinja_sql(source)
    path = tmp_path / "stg_users.sql"
    path.write_text(source, encoding="utf-8")
    context = ParseContext(root=tmp_path, config=default_config())
    parser = SqlParser()
    assert parser.supports(path, context) is False
    result = parser.parse(path, context)
    assert result.nodes == []
    assert result.edges == []
    assert result.warnings == []
    report = Scanner(default_registry()).scan(tmp_path)
    assert not any("unable to parse SQL" in warning for warning in report.graph.scan.warnings)
    assert not any("malformed" in warning.lower() for warning in report.graph.scan.warnings)


def test_scanner_integration(tmp_path: Path) -> None:
    (tmp_path / "orders.sql").write_text(
        "CREATE TABLE customer_summary AS SELECT * FROM customers JOIN orders;",
        encoding="utf-8",
    )
    report = Scanner(default_registry()).scan(tmp_path)
    ids = {node.id for node in report.graph.nodes}
    assert "sql_table:customers" in ids
    assert "sql_table:orders" in ids
    assert "sql_table:customer_summary" in ids
    assert any(
        edge.source == "sql_table:customers" and edge.target == "sql_table:customer_summary"
        for edge in report.graph.edges
    )
