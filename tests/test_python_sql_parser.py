"""Embedded SQL and pandas tests. No database drivers or connections."""

from __future__ import annotations

from pathlib import Path

from daygent.config import default_config
from daygent.parsers.base import ParseContext
from daygent.parsers.python_sql_parser import PythonSqlParser


def _parse(tmp_path: Path, source: str, name: str = "job.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return PythonSqlParser().parse(path, ParseContext(root=tmp_path, config=default_config()))


def _pairs(result) -> set[tuple[str, str]]:
    return {(edge.source, edge.target) for edge in result.edges}


def test_cursor_execute_select(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        '''
def get_orders():
    cursor.execute("""SELECT * FROM warehouse.orders""")
''',
    )
    assert ("sql_table:warehouse.orders", "python_function:job.get_orders") in _pairs(result)
    assert not any(edge.target.startswith("sql_table:") and edge.source.startswith("python_") for edge in result.edges)


def test_insert_select_and_ctas(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        '''
def build_report():
    cursor.execute("""
        INSERT INTO analytics.daily_sales
        SELECT * FROM warehouse.orders
    """)

def build_summary():
    connection.execute("""
        CREATE TABLE analytics.summary AS
        SELECT * FROM warehouse.orders
    """)
''',
    )
    pairs = _pairs(result)
    assert ("sql_table:warehouse.orders", "python_function:job.build_report") in pairs
    assert ("python_function:job.build_report", "sql_table:analytics.daily_sales") in pairs
    assert ("python_function:job.build_summary", "sql_table:analytics.summary") in pairs


def test_session_execute_text_and_executemany(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        '''
from sqlalchemy import text
def load():
    session.execute(text("SELECT * FROM warehouse.orders"))
    cursor.executemany("INSERT INTO analytics.daily_sales SELECT * FROM warehouse.orders")
''',
    )
    pairs = _pairs(result)
    assert ("sql_table:warehouse.orders", "python_function:job.load") in pairs


def test_cte_is_not_physical(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        '''
def summarize():
    cursor.execute("""
        CREATE TABLE summary AS
        WITH active_users AS (SELECT * FROM users)
        SELECT * FROM active_users
    """)
''',
    )
    ids = {node.id for node in result.nodes}
    assert "sql_table:users" in ids
    assert "sql_table:summary" in ids
    assert "sql_table:active_users" not in ids


def test_dynamic_sql_omitted(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
def skipped(query, table):
    cursor.execute(query)
    cursor.execute(f"SELECT * FROM {table}")
""",
    )
    assert not any(node.type == "sql_table" for node in result.nodes)


def test_pandas_sql_helpers(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
import pandas as pd
def load():
    pd.read_sql("SELECT * FROM warehouse.orders", connection)
    pd.read_sql_query("SELECT * FROM warehouse.orders", connection)
    pd.read_sql_table("orders", connection, schema="warehouse")
""",
    )
    pairs = _pairs(result)
    assert ("sql_table:warehouse.orders", "python_function:job.load") in pairs
    assert any(
        str(edge.metadata.get("operation", "")).startswith("pandas.read_sql")
        for edge in result.edges
    )


def test_pandas_dynamic_omitted(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
import pandas as pd
def skipped(sql):
    pd.read_sql(sql, connection)
    pd.read_sql_table(table_name, connection)
""",
    )
    assert not any(node.type == "sql_table" for node in result.nodes)
