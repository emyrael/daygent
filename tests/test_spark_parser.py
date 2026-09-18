"""PySpark, DLT, and Lakeflow static lineage tests. No Spark session."""

from __future__ import annotations

from pathlib import Path

from daygent.config import default_config
from daygent.parsers.base import ParseContext
from daygent.parsers.spark_parser import SparkParser
from daygent.scanner import Scanner
from daygent.parsers import default_registry


def _parse(tmp_path: Path, source: str, name: str = "job.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return SparkParser().parse(path, ParseContext(root=tmp_path, config=default_config()))


def _pairs(result) -> set[tuple[str, str]]:
    return {(edge.source, edge.target) for edge in result.edges}


def test_spark_table_and_read_table(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
def load():
    spark.table("catalog.schema.customers")
    spark.read.table("catalog.schema.customers")
    spark.readStream.table("catalog.schema.events")
""",
    )
    pairs = _pairs(result)
    assert ("sql_table:catalog.schema.customers", "python_function:job.load") in pairs
    assert ("sql_table:catalog.schema.events", "python_function:job.load") in pairs


def test_saveastable_and_writeto(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
def build_metrics():
    df = spark.read.table("silver.customers")
    df.write.mode("overwrite").saveAsTable("gold.customer_metrics")
    df.writeTo("gold.customer_metrics").createOrReplace()
""",
    )
    pairs = _pairs(result)
    assert ("sql_table:silver.customers", "python_function:job.build_metrics") in pairs
    assert ("python_function:job.build_metrics", "sql_table:gold.customer_metrics") in pairs


def test_spark_sql_select_and_ctas(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        '''
def load_customers():
    spark.sql("SELECT * FROM silver.customers")

def build():
    spark.sql("""
        CREATE TABLE gold.customer_metrics AS
        SELECT * FROM silver.customers
    """)
''',
    )
    pairs = _pairs(result)
    assert ("sql_table:silver.customers", "python_function:job.load_customers") in pairs
    assert ("sql_table:silver.customers", "python_function:job.build") in pairs
    assert ("python_function:job.build", "sql_table:gold.customer_metrics") in pairs


def test_same_module_constant_table(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
TABLE = "warehouse.orders"
def load():
    return spark.table(TABLE)
""",
    )
    assert ("sql_table:warehouse.orders", "python_function:job.load") in _pairs(result)


def test_dynamic_spark_targets_omitted(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
def skipped(table_name, query):
    spark.table(table_name)
    spark.read.table(os.getenv("TABLE"))
    spark.sql(query)
    spark.sql(f"SELECT * FROM {table_name}")
""",
    )
    assert result.edges == []
    assert not any(node.type == "sql_table" for node in result.nodes)


def test_dlt_explicit_and_implicit_names(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
import dlt
@dlt.table(name="silver_customers")
def customers():
    return dlt.read("bronze_customers")

@dlt.view
def clean_customers():
    return dlt.read_stream("bronze_events")
""",
    )
    pairs = _pairs(result)
    # @dlt.table is a persisted output, so it takes physical identity and can
    # unify with spark.read.table("silver_customers") elsewhere. An undeclared
    # dlt.read target falls back to a physical table too.
    assert ("sql_table:bronze_customers", "python_function:job.customers") in pairs
    assert ("python_function:job.customers", "sql_table:silver_customers") in pairs
    assert ("sql_table:bronze_events", "python_function:job.clean_customers") in pairs
    # @dlt.view stays logical.
    assert ("python_function:job.clean_customers", "pipeline_dataset:clean_customers") in pairs
    table = next(item for item in result.nodes if item.id == "sql_table:silver_customers")
    assert table.metadata["pipeline_dataset"] is True
    assert table.metadata["dataset_kind"] == "table"
    assert table.metadata["defining_function"] == "customers"
    view = next(item for item in result.nodes if item.id == "pipeline_dataset:clean_customers")
    assert view.metadata["dataset_declaration"] is True


def test_lakeflow_materialized_view(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
from pyspark import pipelines as dp
@dp.materialized_view(name="gold_customer_metrics")
def customer_metrics():
    return spark.read.table("silver.customers")
""",
    )
    pairs = _pairs(result)
    assert ("sql_table:silver.customers", "python_function:job.customer_metrics") in pairs
    assert (
        "python_function:job.customer_metrics",
        "sql_table:gold_customer_metrics",
    ) in pairs
    node = next(item for item in result.nodes if item.id == "sql_table:gold_customer_metrics")
    assert node.metadata["framework"] == "lakeflow"
    assert node.metadata["dataset_kind"] == "materialized_view"
    assert node.metadata["pipeline_dataset"] is True


def test_dlt_dynamic_name_omitted(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
import dlt
@dlt.table(name=get_name())
def skipped():
    return dlt.read(get_name())
""",
    )
    assert not any(node.id.startswith("pipeline_dataset:get_name") for node in result.nodes)


def test_spark_and_sql_file_share_table_identity(tmp_path: Path) -> None:
    (tmp_path / "job.py").write_text(
        'def load():\n    spark.read.table("warehouse.reporting.customers")\n',
        encoding="utf-8",
    )
    (tmp_path / "query.sql").write_text(
        "SELECT * FROM warehouse.reporting.customers;\n",
        encoding="utf-8",
    )
    report = Scanner(default_registry()).scan(tmp_path)
    tables = [node for node in report.graph.nodes if node.id == "sql_table:warehouse.reporting.customers"]
    assert len(tables) == 1
