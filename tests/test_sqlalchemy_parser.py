"""SQLAlchemy static lineage tests. No engine or metadata reflection."""

from __future__ import annotations

from pathlib import Path

from daygent.config import default_config
from daygent.parsers.base import ParseContext
from daygent.parsers.sqlalchemy_parser import SqlAlchemyParser
from daygent.parsers.python_sql_parser import PythonSqlParser


def _parse(tmp_path: Path, source: str, name: str = "models.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    context = ParseContext(root=tmp_path, config=default_config())
    orm = SqlAlchemyParser().parse(path, context)
    sql = PythonSqlParser().parse(path, context)
    return orm, sql


def _pairs(*results) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for result in results:
        pairs.update((edge.source, edge.target) for edge in result.edges)
    return pairs


def test_tablename_schema_and_select(tmp_path: Path) -> None:
    orm, _sql = _parse(
        tmp_path,
        """
from sqlalchemy import select
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = {"schema": "crm"}

def load():
    return select(Customer)
""",
    )
    pairs = _pairs(orm)
    model = "sqlalchemy_model:models.Customer"
    assert ("sql_table:crm.customers", model) in pairs
    assert (model, "python_function:models.load") in pairs


def test_query_and_dml(tmp_path: Path) -> None:
    orm, _sql = _parse(
        tmp_path,
        """
from sqlalchemy import delete, insert, update
from sqlalchemy.orm import DeclarativeBase

class Customer(DeclarativeBase):
    __tablename__ = "customers"

def load(session):
    return session.query(Customer)

def write():
    insert(Customer)
    update(Customer)
    delete(Customer)
""",
    )
    pairs = _pairs(orm)
    model = "sqlalchemy_model:models.Customer"
    assert (model, "python_function:models.load") in pairs
    assert ("python_function:models.write", model) in pairs


def test_text_execute(tmp_path: Path) -> None:
    _orm, sql = _parse(
        tmp_path,
        '''
from sqlalchemy import text
def load(session):
    session.execute(text("SELECT * FROM warehouse.orders"))
''',
        name="job.py",
    )
    assert ("sql_table:warehouse.orders", "python_function:job.load") in _pairs(sql)
