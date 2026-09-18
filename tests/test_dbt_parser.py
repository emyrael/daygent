"""dbt parser tests. No dbt CLI, warehouse, or compile."""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest

from daygent.parsers import default_registry
from daygent.parsers.dbt_parser import extract_refs, extract_sources, find_refs, find_sources
from daygent.parsers.evidence import evidence_metadata, first_line_matching
from daygent.scanner import Scanner

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mini_dbt"
PARSER_SRC = Path(__file__).resolve().parents[1] / "src" / "daygent" / "parsers" / "dbt_parser.py"


def test_extract_ref_and_source_helpers() -> None:
    sql = "select * from {{ ref('stg_users') }} join {{ source('raw', 'users') }}"
    assert extract_refs(sql) == ["stg_users"]
    assert extract_sources(sql) == [("raw", "users")]


@pytest.mark.parametrize(
    "macro",
    [
        '{{ ref("orders") }}',
        '{{ref("orders")}}',
        '{{ ref ("orders") }}',
        '{{ref ("orders")}}',
        '{{ ref( "orders" ) }}',
        '{{ ref ( "orders" ) }}',
        '{{ ref (\n    "orders"\n) }}',
        '{{ ref(\n    "orders"\n) }}',
        "{{ ref('orders') }}",
        "{{ref ('orders')}}",
        '{{- ref("orders") -}}',
        '{{ ref("orders",) }}',
        '{{ REF("orders") }}',
    ],
)
def test_ref_whitespace_matrix(macro: str) -> None:
    """Jinja allows arbitrary whitespace; extraction must not assume a spelling."""
    assert extract_refs(f"select * from {macro}") == ["orders"]


@pytest.mark.parametrize(
    "macro",
    [
        '{{ ref("package_name", "orders") }}',
        '{{ref("package_name","orders")}}',
        '{{ ref ("package_name", "orders") }}',
        '{{ ref(\n    "package_name",\n    "orders"\n) }}',
    ],
)
def test_package_qualified_ref_uses_final_argument(macro: str) -> None:
    assert extract_refs(f"select * from {macro}") == ["orders"]


@pytest.mark.parametrize(
    "macro",
    [
        '{{ source("raw", "orders") }}',
        '{{source("raw", "orders")}}',
        '{{source("raw","orders")}}',
        '{{ source ("raw", "orders") }}',
        '{{ source( "raw", "orders" ) }}',
        '{{ source (\n    "raw",\n    "orders"\n) }}',
        '{{ source(\n    "raw",\n    "orders"\n) }}',
        "{{ source('raw', 'orders') }}",
        '{{- source("raw", "orders") -}}',
        '{{ source("raw", "orders",) }}',
    ],
)
def test_source_whitespace_matrix(macro: str) -> None:
    assert extract_sources(f"select * from {macro}") == [("raw", "orders")]


def test_multiple_refs_in_one_file_with_ctes() -> None:
    sql = """
with request as (
    select * from {{ref ("raw_data_request")}}
),
packages as (
    select * from {{ ref("raw_data_credit_packages") }}
),
notes as (
    select * from {{ source ("app", "customer_notes") }}
)
select * from request join packages using (id_organization)
"""
    assert extract_refs(sql) == ["raw_data_request", "raw_data_credit_packages"]
    assert extract_sources(sql) == [("app", "customer_notes")]


def test_evidence_line_comes_from_matched_macro_position() -> None:
    """Line numbers must follow the real match, not a canonical rendering."""
    sql = (
        'select 1\n\nfrom {{ref ("raw_data_request")}}\n'
        'join {{ ref (\n    "raw_data_credit_packages"\n) }}\n'
    )
    assert find_refs(sql) == [("raw_data_request", 3), ("raw_data_credit_packages", 4)]
    source_sql = 'select 1\nfrom {{source ("app", "requests")}}\n'
    assert find_sources(source_sql) == [("app", "requests", 2)]


def test_jinja_comments_are_not_refs() -> None:
    sql = '{# {{ ref("commented_out") }} #}\nselect * from {{ ref("real_model") }}'
    assert extract_refs(sql) == ["real_model"]
    assert find_refs(sql) == [("real_model", 2)]
    multiline = '{#\n{{ ref("a") }}\n{{ source("s", "t") }}\n#}\nselect * from {{ ref("b") }}\n'
    assert extract_refs(multiline) == ["b"]
    assert extract_sources(multiline) == []
    # Masking preserves offsets, so the surviving ref keeps its true line.
    assert find_refs(multiline) == [("b", 5)]


def test_real_world_subscription_ref_pattern_regression(tmp_path: Path) -> None:
    """Permanent guard for the exact pattern that failed in the wild."""
    (tmp_path / "dbt_project.yml").write_text("name: subs\n", encoding="utf-8")
    models = tmp_path / "models"
    models.mkdir()
    (models / "raw_data_request.sql").write_text("select 1 as id_organization\n", encoding="utf-8")
    (models / "raw_data_credit_packages.sql").write_text(
        "select 1 as id_organization\n", encoding="utf-8"
    )
    (models / "subscription_info.sql").write_text(
        "with r as (\n"
        '    select * from {{ref ("raw_data_request")}}\n'
        "),\n"
        "p as (\n"
        '    select * from {{ref ("raw_data_credit_packages")}}\n'
        ")\n"
        "select * from r join p using (id_organization)\n",
        encoding="utf-8",
    )
    report = Scanner(default_registry()).scan(tmp_path)
    pairs = {(edge.source, edge.target) for edge in report.graph.edges}
    assert ("dbt_model:raw_data_request", "dbt_model:subscription_info") in pairs
    assert ("dbt_model:raw_data_credit_packages", "dbt_model:subscription_info") in pairs


def test_mini_dbt_fixture_refs_and_sources() -> None:
    report = Scanner(default_registry()).scan(FIXTURE)
    ids = {node.id for node in report.graph.nodes}
    assert "dbt_model:stg_users" in ids
    assert "dbt_model:customer_features" in ids
    assert "dbt_model:stg_orders" in ids
    assert "dbt_source:raw.users" in ids
    edges = {(edge.source, edge.target, edge.type, edge.evidence) for edge in report.graph.edges}
    assert (
        "dbt_model:stg_users",
        "dbt_model:customer_features",
        "ref",
        "dbt_ref",
    ) in edges
    assert (
        "dbt_model:stg_orders",
        "dbt_model:customer_features",
        "ref",
        "dbt_ref",
    ) in edges
    assert (
        "dbt_source:raw.users",
        "dbt_model:stg_users",
        "source",
        "dbt_source",
    ) in edges
    assert all(
        edge.confidence == "high"
        for edge in report.graph.edges
        if edge.evidence in {"dbt_ref", "dbt_source"}
    )
    ref_edge = next(
        edge
        for edge in report.graph.edges
        if edge.source == "dbt_model:stg_users"
        and edge.target == "dbt_model:customer_features"
    )
    assert ref_edge.metadata.get("reference") == "stg_users"
    assert ref_edge.metadata.get("file_path", "").endswith("customer_features.sql")
    assert ref_edge.metadata.get("line_number") == 4


def test_evidence_metadata_only_keeps_known_fields() -> None:
    assert evidence_metadata() == {}
    assert evidence_metadata(
        file_path="models/marts/customer_features.sql",
        line_number=8,
        reference="stg_users",
    ) == {
        "file_path": "models/marts/customer_features.sql",
        "line_number": 8,
        "reference": "stg_users",
    }
    source = "select 1\nfrom {{ ref('stg_users') }}\n"
    assert first_line_matching(source, ["ref('stg_users')"]) == 2
    assert first_line_matching(source, ["missing"]) is None


def test_missing_manifest_still_succeeds(tmp_path: Path) -> None:
    dest = tmp_path / "mini"
    shutil.copytree(FIXTURE, dest)
    assert not (dest / "target" / "manifest.json").exists()
    report = Scanner(default_registry()).scan(dest)
    assert any(node.id == "dbt_model:stg_users" for node in report.graph.nodes)
    assert not any("manifest" in warning.lower() for warning in report.graph.scan.warnings)


def test_optional_manifest_merge(tmp_path: Path) -> None:
    dest = tmp_path / "mini"
    shutil.copytree(FIXTURE, dest)
    (dest / "target").mkdir()
    (dest / "target" / "manifest.json").write_text(
        json.dumps(
            {
                "nodes": {
                    "model.mini.customer_features": {
                        "resource_type": "model",
                        "name": "customer_features",
                        "depends_on": {
                            "nodes": [
                                "model.mini.stg_users",
                                "source.mini.raw.users",
                            ]
                        },
                    }
                },
                "sources": {
                    "source.mini.raw.users": {
                        "source_name": "raw",
                        "name": "users",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    report = Scanner(default_registry()).scan(dest)
    assert any(edge.evidence == "dbt_manifest" for edge in report.graph.edges)
    assert any(
        edge.source == "dbt_source:raw.users"
        and edge.target == "dbt_model:customer_features"
        for edge in report.graph.edges
    )


def test_broken_jinja_warning(tmp_path: Path) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: mini\n", encoding="utf-8")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "broken.sql").write_text(
        "select * from {{ ref('stg_users')\n",
        encoding="utf-8",
    )
    report = Scanner(default_registry()).scan(tmp_path)
    assert any("broken Jinja" in warning for warning in report.graph.scan.warnings)


def test_dbt_parser_does_not_execute_dbt() -> None:
    tree = ast.parse(PARSER_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".", maxsplit=1)[0] != "subprocess" for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".", maxsplit=1)[0] != "subprocess"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"eval", "exec"}
    text = PARSER_SRC.read_text(encoding="utf-8")
    assert "dbt compile" not in text
    assert "dbt deps" not in text


def test_jinja_sql_uses_dbt_not_generic_sql(tmp_path: Path) -> None:
    (tmp_path / "only.sql").write_text("select * from {{ ref('stg_users') }}", encoding="utf-8")
    report = Scanner(default_registry()).scan(tmp_path)
    assert "dbt_model:only" in {node.id for node in report.graph.nodes}
    assert not any("unable to parse SQL" in warning for warning in report.graph.scan.warnings)
    registry = default_registry()
    from daygent.config import default_config
    from daygent.parsers.base import ParseContext

    context = ParseContext(root=tmp_path, config=default_config())
    names = [parser.name for parser in registry.matching(tmp_path / "only.sql", context)]
    assert "dbt" in names
    assert "sql" not in names
