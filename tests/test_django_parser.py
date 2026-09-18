"""Django ORM and raw SQL tests. No Django setup or database."""

from __future__ import annotations

from pathlib import Path

from daygent.config import default_config
from daygent.parsers.base import ParseContext
from daygent.parsers.django_parser import DjangoParser


def _parse(tmp_path: Path, source: str, name: str = "models.py"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return DjangoParser().parse(path, ParseContext(root=tmp_path, config=default_config()))


def _pairs(result) -> set[tuple[str, str]]:
    return {(edge.source, edge.target) for edge in result.edges}


def test_model_db_table_and_qualified_id(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
from django.db import models
class Customer(models.Model):
    class Meta:
        db_table = "crm_customer"
""",
        name="customers/models.py",
    )
    ids = {node.id for node in result.nodes}
    assert "django_model:customers.Customer" in ids
    assert ("sql_table:crm_customer", "django_model:customers.Customer") in _pairs(result)


def test_orm_reads_and_writes_keep_direction(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
from django.db import models
class Customer(models.Model):
    pass

def get_active_customers():
    Customer.objects.filter(active=True)
    Customer.objects.get(id=1)
    Customer.objects.all()
    Customer.objects.annotate()
    Customer.objects.select_related("account")

def create_customer():
    Customer.objects.create(name="Ada")
    Customer.objects.bulk_create([])
    Customer.objects.update(active=True)
""",
        name="services.py",
    )
    pairs = _pairs(result)
    model = "django_model:services.Customer"
    assert (model, "python_function:services.get_active_customers") in pairs
    assert ("python_function:services.create_customer", model) in pairs
    assert (model, "python_function:services.create_customer") not in pairs


def test_objects_raw_sql(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        '''
class Customer:
    class objects:
        pass

def active_customers():
    return Customer.objects.raw("SELECT * FROM app_customer WHERE active = true")
''',
        name="services.py",
    )
    # Without models.Model this is not a declared model class, but objects.raw still binds.
    assert ("sql_table:app_customer", "python_function:services.active_customers") in _pairs(result)


def test_dynamic_model_lookup_omitted(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
def skipped():
    get_model("Customer").objects.filter(active=True)
    apps.get_model("customers", "Customer").objects.all()
""",
        name="services.py",
    )
    assert not any(node.type == "django_model" for node in result.nodes)
