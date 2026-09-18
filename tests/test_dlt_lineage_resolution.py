"""Regression tests for DLT/Spark lineage resolution, temp views, and projection.

Covers the fourteen cases that motivated the identity and viewer rework, plus
the `dlt_sensor_pipeline` fixture that must reconstruct bronze → silver → gold
across five files with no imports between the data producers.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest

from daygent.config import default_config
from daygent.graph.assets import ASSET_TYPES, project_asset_graph
from daygent.graph.identity import declared_datasets, resolve_dataset_identity
from daygent.graph.traversal import adjacency
from daygent.models import Graph, Node, NodeType
from daygent.parsers.base import ParseContext
from daygent.parsers.spark_parser import SparkParser
from daygent.scanner import Scanner
from daygent.viewer.html import render_html

FIXTURE = Path(__file__).parent / "fixtures" / "dlt_sensor_pipeline"

PIPELINE = "python_function:pipelines.observations"
JOB = "python_function:jobs.climate_report"
VIEWS = "temp_view:pipelines.observations"


def _parse(tmp_path: Path, source: str, name: str = "job.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return SparkParser().parse(path, ParseContext(root=tmp_path, config=default_config()))


def _pairs(result) -> set[tuple[str, str]]:
    return {(edge.source, edge.target) for edge in result.edges}


def _path(graph: Graph, start: str, end: str) -> list[str] | None:
    """Shortest downstream path, following the stored A → B direction."""
    fwd = adjacency(graph, forward=True)
    previous: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current == end:
            chain: list[str] = []
            cursor: str | None = current
            while cursor is not None:
                chain.append(cursor)
                cursor = previous[cursor]
            return list(reversed(chain))
        for nxt in fwd.get(current, ()):
            if nxt not in previous:
                previous[nxt] = current
                queue.append(nxt)
    return None


@pytest.fixture(scope="module")
def sensor_graph() -> Graph:
    return Scanner().scan(FIXTURE).graph


@pytest.fixture(scope="module")
def sensor_graph_unscoped() -> Graph:
    return Scanner().scan(FIXTURE, scope=False).graph


# -- 1. DLT view read resolves to the pipeline dataset ----------------------


def test_dlt_view_read_resolves_to_pipeline_dataset(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
import dlt
@dlt.view(name="v_widgets")
def v_widgets():
    return spark.read.table("bronze.raw_widgets")

@dlt.table(name="silver.widgets")
def widgets():
    return spark.read.table("v_widgets")
""",
    )
    ids = {node.id for node in result.nodes}
    assert "pipeline_dataset:v_widgets" in ids
    assert "sql_table:v_widgets" not in ids
    assert ("pipeline_dataset:v_widgets", "python_function:job.widgets") in _pairs(result)


def test_cross_file_view_reference_absorbs_into_declaration(tmp_path: Path) -> None:
    (tmp_path / "declare.py").write_text(
        'import dlt\n'
        '@dlt.view(name="v_shared")\n'
        'def v_shared():\n'
        '    return spark.read.table("bronze.raw")\n',
        encoding="utf-8",
    )
    (tmp_path / "consume.py").write_text(
        'def build():\n    return spark.read.table("v_shared")\n',
        encoding="utf-8",
    )
    graph = Scanner().scan(tmp_path, scope=False).graph
    ids = {node.id for node in graph.nodes}
    assert "pipeline_dataset:v_shared" in ids
    assert "sql_table:v_shared" not in ids
    assert ("pipeline_dataset:v_shared", "python_function:consume.build") in {
        (edge.source, edge.target) for edge in graph.edges
    }


# -- 2. Persisted table identity unifies with spark.read.table --------------


def test_persisted_dlt_table_unifies_with_spark_read(tmp_path: Path) -> None:
    (tmp_path / "producer.py").write_text(
        'import dlt\n'
        '@dlt.table(name="silver.widget_metrics")\n'
        'def widget_metrics():\n'
        '    return spark.read.table("bronze.widgets")\n',
        encoding="utf-8",
    )
    (tmp_path / "consumer.py").write_text(
        'def report():\n    return spark.read.table("silver.widget_metrics")\n',
        encoding="utf-8",
    )
    graph = Scanner().scan(tmp_path, scope=False).graph
    ids = {node.id for node in graph.nodes}
    assert "sql_table:silver.widget_metrics" in ids
    assert "pipeline_dataset:silver.widget_metrics" not in ids
    node = graph.node_index()["sql_table:silver.widget_metrics"]
    assert node.metadata["framework"] == "dlt"
    assert node.metadata["pipeline_dataset"] is True
    assert node.metadata["dataset_kind"] == "table"
    assert node.metadata["defining_function"] == "widget_metrics"
    assert _path(graph, "sql_table:bronze.widgets", "python_function:consumer.report")


# -- 3. createOrReplaceTempView --------------------------------------------


def test_create_or_replace_temp_view_from_read(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
def stage():
    spark.read.table("silver.shipments").createOrReplaceTempView("tmp_shipments")
""",
    )
    assert ("sql_table:silver.shipments", "temp_view:job:tmp_shipments") in _pairs(result)
    view = next(node for node in result.nodes if node.type == NodeType.TEMP_VIEW)
    assert view.metadata["temp_view"] is True
    assert view.metadata["created_by"] == "stage"
    # The alias is not a table, and the producing side skips the function so the
    # consumer side cannot form a cycle.
    assert "sql_table:tmp_shipments" not in {node.id for node in result.nodes}
    assert ("sql_table:silver.shipments", "python_function:job.stage") not in _pairs(result)


# -- 4. SQL references to temp views ---------------------------------------


def test_sql_reference_resolves_to_temp_view(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
SQL = "select * from tmp_shipments where weight > 0"

def stage():
    spark.read.table("silver.shipments").createOrReplaceTempView("tmp_shipments")
    return spark.sql(SQL)
""",
    )
    pairs = _pairs(result)
    assert ("temp_view:job:tmp_shipments", "python_function:job.stage") in pairs
    assert "sql_table:tmp_shipments" not in {node.id for node in result.nodes}


def test_temp_view_resolution_is_order_independent(tmp_path: Path) -> None:
    """A view consumed by a function defined above its producer still links."""
    result = _parse(
        tmp_path,
        """
SQL = "select * from tmp_late"

def consume():
    return spark.sql(SQL)

def produce():
    spark.read.table("silver.shipments").createOrReplaceTempView("tmp_late")
""",
    )
    pairs = _pairs(result)
    assert ("temp_view:job:tmp_late", "python_function:job.consume") in pairs
    assert ("sql_table:silver.shipments", "temp_view:job:tmp_late") in pairs


# -- 5. Chained spark.sql(...).createOrReplaceTempView(...) ----------------


def test_sql_created_temp_view_propagates(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
SQL_A = "select * from bronze.telemetry"
SQL_B = "select count(*) from stage_a"

def build():
    spark.sql(SQL_A).createOrReplaceTempView("stage_a")
    return spark.sql(SQL_B)
""",
    )
    pairs = _pairs(result)
    assert ("sql_table:bronze.telemetry", "temp_view:job:stage_a") in pairs
    assert ("temp_view:job:stage_a", "python_function:job.build") in pairs
    # No f() → stage_a → f() cycle.
    assert ("python_function:job.build", "temp_view:job:stage_a") not in pairs


# -- 6. DataFrame alias propagation ----------------------------------------


def test_dataframe_alias_propagates_to_temp_view(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
def stage():
    df = spark.read.table("silver.shipments")
    df = df.filter("weight > 0").withColumn("x", lit(1))
    df.createOrReplaceTempView("tmp_shipments")
""",
    )
    assert ("sql_table:silver.shipments", "temp_view:job:tmp_shipments") in _pairs(result)


def test_dataframe_alias_write_and_chained_receiver(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
SQL = "select * from silver.shipments"

def publish():
    df = spark.sql(SQL)
    df.write.mode("overwrite").saveAsTable("gold.shipments")

def stage():
    spark.read.table("silver.parcels").join(other, "k").createOrReplaceTempView("tmp_parcels")
""",
    )
    pairs = _pairs(result)
    assert ("sql_table:silver.shipments", "python_function:job.publish") in pairs
    assert ("python_function:job.publish", "sql_table:gold.shipments") in pairs
    assert ("sql_table:silver.parcels", "temp_view:job:tmp_parcels") in pairs


# -- 7. Literal dict / list loops ------------------------------------------


def test_literal_dict_items_temp_view_loop(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
SQL = "select * from dim_Region join dim_Depot on 1=1"

def build():
    dimensions = {
        "dim_Region": "silver.region_lookup",
        "dim_Depot": "silver.depot_lookup",
    }
    for view, table in dimensions.items():
        spark.read.table(table).createOrReplaceTempView(view)
    return spark.sql(SQL)
""",
    )
    pairs = _pairs(result)
    assert ("sql_table:silver.region_lookup", "temp_view:job:dim_region") in pairs
    assert ("sql_table:silver.depot_lookup", "temp_view:job:dim_depot") in pairs
    assert ("temp_view:job:dim_region", "python_function:job.build") in pairs
    assert ("temp_view:job:dim_depot", "python_function:job.build") in pairs


def test_literal_sequence_loop(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
TABLES = ["bronze.a", "bronze.b"]

def load():
    for table in TABLES:
        spark.read.table(table)
""",
    )
    pairs = _pairs(result)
    assert ("sql_table:bronze.a", "python_function:job.load") in pairs
    assert ("sql_table:bronze.b", "python_function:job.load") in pairs


def test_dynamic_loop_emits_nothing(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        """
def load(tables):
    for table in tables:
        spark.read.table(table)
""",
    )
    assert not any(node.type == NodeType.SQL_TABLE for node in result.nodes)


# -- 8/9/10. Fixture chains across files -----------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("sql_table:bronze.bronze_unit_conversion", "pipeline_dataset:v_unit_conversion"),
        ("pipeline_dataset:v_unit_conversion", "pipeline_dataset:v_readings_normalized"),
        (
            "pipeline_dataset:v_readings_normalized",
            "pipeline_dataset:v_station_readings_all",
        ),
        ("pipeline_dataset:v_station_readings_all", "sql_table:silver.silver_sensor_hourly"),
        (
            "sql_table:silver.silver_sensor_daily",
            "sql_table:silver.silver_observation_metrics",
        ),
        (
            "sql_table:silver.silver_sensor_hourly",
            "sql_table:silver.silver_observation_metrics",
        ),
        (
            "sql_table:silver.silver_station_quality",
            "sql_table:silver.silver_observation_metrics",
        ),
        ("sql_table:bronze.bronze_unit_conversion", "sql_table:gold.gold_station_leaderboard"),
        ("sql_table:bronze.bronze_station_audit", "sql_table:gold.gold_observation_archive"),
    ],
)
def test_sensor_pipeline_chains(sensor_graph: Graph, start: str, end: str) -> None:
    assert _path(sensor_graph, start, end) is not None, f"no path {start} -> {end}"


def test_sensor_pipeline_helper_dependency_propagates(sensor_graph: Graph) -> None:
    """A helper's own table read must reach the pipeline output."""
    chain = _path(
        sensor_graph,
        "sql_table:bronze.bronze_calibration_offsets",
        "sql_table:silver.silver_sensor_hourly",
    )
    assert chain is not None
    assert f"{PIPELINE}._apply_calibration" in chain


def test_sensor_pipeline_cross_file_import_call(sensor_graph_unscoped: Graph) -> None:
    helper = "python_function:shared.hashing.build_row_hash"
    callers = {edge.target for edge in sensor_graph_unscoped.edges if edge.source == helper}
    assert f"{JOB}._write_leaderboard" in callers
    assert f"{PIPELINE}.silver_observation_metrics" in callers


def test_sensor_pipeline_class_import_call_is_dropped(sensor_graph_unscoped: Graph) -> None:
    """An imported class is not a function, so the provisional edge vanishes."""
    ids = {node.id for node in sensor_graph_unscoped.nodes}
    assert "python_function:shared.hashing.RowHasher" not in ids
    assert not any(
        "Dropped dangling edge" in warning for warning in sensor_graph_unscoped.scan.warnings
    )


# -- 11. No fake physical tables for temp views ----------------------------


def test_sensor_pipeline_has_no_fake_tables(sensor_graph: Graph) -> None:
    fake_prefixes = ("rn_", "sr_", "sd_", "om_", "sq_", "cr_")
    for node in sensor_graph.nodes:
        if node.type != NodeType.SQL_TABLE:
            continue
        name = node.id.split(":", 1)[1]
        assert not name.startswith(fake_prefixes), f"temp view leaked as a table: {node.id}"
    # Logical DLT views never become physical tables either.
    ids = {node.id for node in sensor_graph.nodes}
    for view in ("v_unit_conversion", "v_readings_normalized", "v_station_readings_all"):
        assert f"pipeline_dataset:{view}" in ids
        assert f"sql_table:{view}" not in ids


def test_sensor_pipeline_temp_views_are_module_scoped(sensor_graph: Graph) -> None:
    views = {node.id for node in sensor_graph.nodes if node.type == NodeType.TEMP_VIEW}
    assert f"{VIEWS}:rn_readings_normalized" in views
    assert "temp_view:jobs.climate_report:cr_metrics_ranked" in views


def test_sensor_pipeline_tolerates_unparseable_sql(sensor_graph: Graph) -> None:
    warnings = sensor_graph.scan.warnings
    assert any("unable to parse SQL" in warning for warning in warnings)
    # The failure is contained: the rest of the module still produced lineage.
    assert "pipeline_dataset:v_readings_normalized" in {node.id for node in sensor_graph.nodes}


def test_sensor_pipeline_skips_dynamic_dataset_name(sensor_graph: Graph) -> None:
    assert not any("regional_partition" in node.id for node in sensor_graph.nodes)


# -- 12/13. Asset projection and detailed graph ----------------------------


def test_asset_projection_contracts_implementation_nodes(sensor_graph: Graph) -> None:
    projection = project_asset_graph(sensor_graph)
    assert projection.nodes
    assert all(node.type in ASSET_TYPES for node in projection.nodes)
    assert len(projection.nodes) < len(sensor_graph.nodes)

    contracted = projection.edge_index()
    key = (
        "sql_table:silver.silver_sensor_hourly",
        "sql_table:silver.silver_observation_metrics",
    )
    assert key in contracted
    edge = contracted[key]
    assert edge.metadata["projection"] == "asset"
    via = edge.metadata["via"]
    assert f"{VIEWS}:om_sensor_hourly" in via
    assert edge.metadata["hops"] == len(via)
    assert edge.metadata["via_labels"]


def test_asset_projection_keeps_direct_asset_edges(sensor_graph: Graph) -> None:
    contracted = project_asset_graph(sensor_graph).edge_index()
    key = (
        "sql_table:silver.silver_observation_metrics",
        "sql_table:gold.gold_observation_archive",
    )
    assert key in contracted
    assert contracted[key].metadata["via"] == []


def test_detailed_graph_survives_projection(sensor_graph: Graph) -> None:
    """Projection is display-only: the detailed graph keeps every hop."""
    projection = project_asset_graph(sensor_graph)
    assert any(node.type == NodeType.PYTHON_FUNCTION for node in sensor_graph.nodes)
    assert any(node.type == NodeType.TEMP_VIEW for node in sensor_graph.nodes)
    assert not any(
        node.type in {NodeType.PYTHON_FUNCTION, NodeType.TEMP_VIEW}
        for node in projection.nodes
    )


def test_asset_projection_is_deterministic(sensor_graph: Graph) -> None:
    first = project_asset_graph(sensor_graph)
    second = project_asset_graph(sensor_graph)
    assert [edge.key for edge in first.edges] == [edge.key for edge in second.edges]
    assert [edge.metadata["via"] for edge in first.edges] == [
        edge.metadata["via"] for edge in second.edges
    ]


# -- 14. Viewer defaults, filter-with-context, component layout ------------


def test_viewer_payload_carries_asset_projection(sensor_graph: Graph) -> None:
    html = render_html(sensor_graph)
    assert '"asset_edges"' in html
    assert '"asset_types"' in html
    assert 'id="show-details"' in html
    assert 'var showDetails = false;' in html


def test_viewer_filters_with_context_by_default(sensor_graph: Graph) -> None:
    html = render_html(sensor_graph)
    assert 'id="filter-mode"' in html
    assert '<option value="context">' in html
    assert 'var filterMode = "context";' in html
    # Context mode keeps a filtered type's neighbours instead of deleting them.
    assert "function contextOf(" in html
    assert 'filterMode === "only" ? matching : contextOf(matching, edgeList)' in html


def test_viewer_layout_is_component_aware(sensor_graph: Graph) -> None:
    html = render_html(sensor_graph)
    # Disconnected roots must not all land in one column: layout splits weakly
    # connected components, ranks each on its own, and shelf-packs the blocks.
    for marker in (
        "function components(",
        "function rankComponent(",
        "function layoutComponent(",
        "function gridBlock(",
        "MAX_ROW_HEIGHT",
        "COMPONENT_GAP_Y",
    ):
        assert marker in html, marker


def test_viewer_has_focus_and_namespace_support(sensor_graph: Graph) -> None:
    html = render_html(sensor_graph)
    for marker in (
        'id="btn-focus"',
        'id="btn-clear-focus"',
        "function focusLineage(",
        "function namespaceOf(",
        "function stepSearch(",
        "function renderViaPath(",
    ):
        assert marker in html, marker


# -- identity stage unit behaviour ----------------------------------------


def test_resolve_dataset_identity_is_a_no_op_without_declarations() -> None:
    graph = Graph(
        nodes=[
            Node(id="sql_table:a", name="a", type=NodeType.SQL_TABLE),
            Node(id="sql_table:b", name="b", type=NodeType.SQL_TABLE),
        ],
        edges=[],
    )
    assert resolve_dataset_identity(graph).nodes == graph.sorted().nodes


def test_declared_datasets_matches_case_insensitively() -> None:
    graph = Graph(
        nodes=[
            Node(
                id="pipeline_dataset:V_Mixed_Case",
                name="V_Mixed_Case",
                type=NodeType.PIPELINE_DATASET,
                metadata={"dataset_declaration": True},
            )
        ],
        edges=[],
    )
    assert declared_datasets(graph) == {"v_mixed_case": "pipeline_dataset:V_Mixed_Case"}
