"""Golden mixed Python-data stack: Spark, DLT, Django, SQLAlchemy, pandas, SQL."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from daygent.cli import app
from daygent.parsers import default_registry
from daygent.scanner import Scanner

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "python_data_stack"
runner = CliRunner()

SCOPED_OUT = {
    "python_function:unused_helpers.format_date",
    "python_function:unused_helpers.unused_helper",
}


def _scan():
    return Scanner(default_registry()).scan(FIXTURE)


def test_python_data_stack_core_chain() -> None:
    graph = _scan().graph
    ids = {node.id for node in graph.nodes}
    pairs = {(edge.source, edge.target) for edge in graph.edges}
    assert "sql_table:raw.orders" in ids
    assert "sql_table:silver.orders" in ids
    assert (
        "sql_table:raw.orders",
        "python_function:spark_job.transform_orders",
    ) in pairs
    assert (
        "python_function:spark_job.transform_orders",
        "sql_table:silver.orders",
    ) in pairs
    assert (
        "sql_table:silver.orders",
        "python_function:lakeflow_pipeline.order_metrics",
    ) in pairs
    assert (
        "python_function:lakeflow_pipeline.order_metrics",
        "pipeline_dataset:gold.order_metrics",
    ) in pairs


def test_python_data_stack_helpers_scoped_out() -> None:
    graph = _scan().graph
    ids = {node.id for node in graph.nodes}
    assert SCOPED_OUT.isdisjoint(ids)


def test_python_data_stack_django_and_embedded_sql() -> None:
    graph = _scan().graph
    pairs = {(edge.source, edge.target) for edge in graph.edges}
    assert (
        "sql_table:crm_customer",
        "django_model:django_app.Customer",
    ) in pairs
    assert (
        "django_model:django_app.Customer",
        "python_function:django_app.services.get_active_customers",
    ) in pairs
    assert (
        "python_function:django_app.services.create_customer",
        "django_model:django_app.Customer",
    ) in pairs
    assert (
        "sql_table:warehouse.orders",
        "python_function:embedded_sql.build_report",
    ) in pairs
    assert (
        "python_function:embedded_sql.build_report",
        "sql_table:analytics.daily_sales",
    ) in pairs


def test_python_data_stack_shared_sql_table_identity() -> None:
    graph = _scan().graph
    raw = [node for node in graph.nodes if node.id == "sql_table:raw.orders"]
    warehouse = [node for node in graph.nodes if node.id == "sql_table:warehouse.orders"]
    assert len(raw) == 1
    assert len(warehouse) == 1


def test_python_data_stack_cli_scan_graph_impact(tmp_path: Path) -> None:
    import shutil

    stack = tmp_path / "stack"
    shutil.copytree(FIXTURE, stack)
    result = runner.invoke(app, ["scan", str(stack)])
    assert result.exit_code == 0, result.output
    html = runner.invoke(app, ["graph", "--html", "--root", str(stack)])
    assert html.exit_code == 0, html.output
    page = (stack / ".daygent" / "graph.html").read_text(encoding="utf-8")
    assert "pipeline dataset" in page or "gold.order_metrics" in page
    impact = runner.invoke(app, ["impact", "raw.orders", "--json", "--root", str(stack)])
    assert impact.exit_code == 0, impact.output
    payload = json.loads(impact.stdout)
    ids = {item["id"] for item in payload["direct"] + payload["downstream"]}
    assert "python_function:spark_job.transform_orders" in ids
    assert "sql_table:silver.orders" in ids


def test_python_data_stack_dynamic_omitted() -> None:
    graph = _scan().graph
    ids = {node.id for node in graph.nodes}
    assert "python_function:spark_job.skipped_dynamic" not in ids
    assert "python_function:embedded_sql.skipped_dynamic" not in ids
    assert "python_function:legacy_dlt.skipped_dynamic_name" not in ids
