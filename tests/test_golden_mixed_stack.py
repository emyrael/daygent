"""Golden mixed-stack fixture: locks v0.1 scan, graph, impact, and scoping.

AC-001–AC-018 plus scan-broadly/graph-narrowly. Cross-file Python calls are a
known v0.1 limitation and are asserted as absent, not faked.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from daygent.analysis import get_ancestors, get_descendants
from daygent.cli import app
from daygent.graph.store import canonical_graph_dict
from daygent.models import Graph
from daygent.scanner import Scanner
from daygent.viewer.html import viewer_payload
from daygent.viewer.source import MAX_EXCERPT_LINES

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "golden_mixed_stack"
SRC = Path(__file__).resolve().parents[1] / "src" / "daygent"
runner = CliRunner()

SCOPED_OUT = {
    "python_function:utils.format_date",
    "python_function:utils.unrelated_helper",
    "python_module:utils",
    "python_function:app.health",
    "api_route:GET:/health",
    "python_function:agent.unused_router",
}

MUST_KEEP = {
    "dbt_source:raw.users",
    "dbt_model:stg_users",
    "dbt_model:user_features",
    "sql_table:customers",
    "sql_table:orders",
    "sql_table:customer_summary",
    "python_function:app.retrieve_documents",
    "python_function:app.answer_question",
    "python_function:app.ask",
    "python_function:app.recommend",
    "python_function:services.recommend",
    "api_route:POST:/ask",
    "api_route:POST:/recommend",
    "langgraph_node:retrieve",
    "langgraph_node:generate",
    "python_function:agent.retrieve",
    "python_function:agent.generate",
    "llm:openai:gpt-5",
    "llm:openai:dynamic",
    "embedding_model:text-embedding-3-small",
    "vector_store:qdrant",
    "vector_collection:company_docs",
    "external_system:api.stripe.com",
}


def _output(result) -> str:
    """Combine captured stdout/stderr for assertions."""
    stderr = getattr(result, "stderr", "") or ""
    return f"{result.stdout}{stderr}{result.output}"


def _copy_fixture(tmp_path: Path) -> Path:
    """Copy the golden repo so CLI commands can write .daygent/."""
    dest = tmp_path / "golden_mixed_stack"
    shutil.copytree(FIXTURE, dest, ignore=shutil.ignore_patterns("__pycache__", ".daygent"))
    return dest


def _ids(graph: Graph) -> set[str]:
    """Return node ids in `graph`."""
    return {node.id for node in graph.nodes}


def _pairs(graph: Graph) -> set[tuple[str, str, str]]:
    """Return (source, target, type) triples."""
    return {(edge.source, edge.target, edge.type) for edge in graph.edges}


def _payload_from_html(html: str) -> dict:
    """Parse the embedded viewer JSON document."""
    start = html.index('id="daygent-data">') + len('id="daygent-data">')
    end = html.index("</script>", start)
    return json.loads(html[start:end])


@pytest.fixture(scope="module")
def golden_graph() -> Graph:
    """Scan the committed fixture once for library assertions."""
    return Scanner().scan(FIXTURE).graph


def test_ac001_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, _output(result)
    text = result.stdout
    assert "scan" in text
    assert "graph" in text
    assert "impact" in text


def test_ac002_scan_writes_graph(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    result = runner.invoke(app, ["scan", str(root)])
    assert result.exit_code == 0, _output(result)
    text = _output(result)
    assert "nodes" in text.lower()
    assert "edges" in text.lower()
    graph_path = root / ".daygent" / "graph.json"
    assert graph_path.is_file()
    assert str(graph_path) in text.replace("\n", "")
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    assert payload["version"] == "0.1"
    assert payload["nodes"] == sorted(payload["nodes"], key=lambda node: node["id"])
    assert payload["edges"] == sorted(
        payload["edges"], key=lambda edge: (edge["source"], edge["target"], edge["type"])
    )
    assert "scan" in payload
    assert "timestamp" in payload["scan"]


def test_ac003_ignore_venv_and_malformed_sql(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    ignored = root / ".venv" / "lib"
    ignored.mkdir(parents=True)
    (ignored / "ignored.py").write_text(
        'ChatOpenAI(model="must-not-scan")\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["scan", str(root)])
    assert result.exit_code == 0, _output(result)
    text = _output(result)
    assert "Warning" in text
    assert "broken.sql" in text
    payload = json.loads((root / ".daygent" / "graph.json").read_text(encoding="utf-8"))
    ids = {node["id"] for node in payload["nodes"]}
    assert "llm:openai:must-not-scan" not in ids
    assert "dbt_model:stg_users" in ids
    assert "sql_table:customer_summary" in ids


def test_ac004_graph_direction_on_justified_edges(golden_graph: Graph) -> None:
    pairs = _pairs(golden_graph)
    assert ("dbt_source:raw.users", "dbt_model:stg_users", "source") in pairs
    assert ("dbt_model:stg_users", "dbt_model:user_features", "ref") in pairs
    assert (
        "python_function:app.recommend",
        "api_route:POST:/recommend",
        "invoked_by",
    ) in pairs
    # v0.1 cannot statically join dbt models to Python callers.
    assert ("dbt_model:user_features", "python_function:app.recommend", "feeds") not in pairs
    assert ("dbt_model:user_features", "python_function:app.recommend", "ref") not in pairs
    for edge in golden_graph.edges:
        if edge.evidence in {"dbt_ref", "dbt_source"}:
            assert edge.confidence == "high"


def test_ac005_graph_json_mermaid_and_html(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    assert runner.invoke(app, ["scan", str(root)]).exit_code == 0
    human = runner.invoke(app, ["graph", "--root", str(root)])
    assert human.exit_code == 0, _output(human)
    assert "stg_users" in human.stdout
    assert "user_features" in human.stdout
    assert "↓" in human.stdout
    dumped = runner.invoke(app, ["graph", "--json", "--root", str(root)])
    assert dumped.exit_code == 0, _output(dumped)
    payload = json.loads(dumped.stdout)
    assert payload["version"] == "0.1"
    assert {node["id"] for node in payload["nodes"]} >= MUST_KEEP
    mermaid = runner.invoke(app, ["graph", "--mermaid", "--root", str(root)])
    assert mermaid.exit_code == 0, _output(mermaid)
    assert mermaid.stdout.startswith("graph LR")
    assert "-->" in mermaid.stdout
    html = runner.invoke(app, ["graph", "--html", "--root", str(root)])
    assert html.exit_code == 0, _output(html)
    html_path = root / ".daygent" / "graph.html"
    assert html_path.is_file()
    text = html_path.read_text(encoding="utf-8")
    assert text.lstrip().startswith("<!DOCTYPE html>")
    assert "cdn." not in text.lower()
    assert "fetch(" not in text
    viewer = _payload_from_html(text)
    assert viewer["includes_source"] is False
    assert viewer["convention"] == "A → B means B depends on A"
    assert 'id="direction-legend"' in text
    dbt_edge = next(
        edge
        for edge in viewer["edges"]
        if edge["source"] == "dbt_model:stg_users"
        and edge["target"] == "dbt_model:user_features"
    )
    assert dbt_edge["evidence"] == "dbt_ref"
    assert dbt_edge["metadata"]["reference"] == "stg_users"
    assert "source_excerpt" not in dbt_edge


def test_ac006_impact_blast_radius(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    assert runner.invoke(app, ["scan", str(root)]).exit_code == 0
    result = runner.invoke(app, ["impact", "stg_users", "--root", str(root)])
    assert result.exit_code == 0, _output(result)
    text = result.stdout
    assert "Impact: stg_users" in text
    direct_block = text.split("Downstream:")[0]
    assert "user_features" in direct_block
    recommend = runner.invoke(
        app,
        ["impact", "python_function:app.recommend", "--root", str(root)],
    )
    assert recommend.exit_code == 0, _output(recommend)
    assert "POST /recommend" in recommend.stdout


def test_ac007_impact_depth_and_json(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    assert runner.invoke(app, ["scan", str(root)]).exit_code == 0
    limited = runner.invoke(
        app, ["impact", "stg_users", "--depth", "1", "--json", "--root", str(root)]
    )
    assert limited.exit_code == 0, _output(limited)
    payload = json.loads(limited.stdout)
    assert payload["resolved_id"] == "dbt_model:stg_users"
    depths = [item["depth"] for item in payload["direct"] + payload["downstream"]]
    assert depths and max(depths) <= 1
    names = {item["name"] for item in payload["direct"]}
    assert "user_features" in names


def test_ac008_ambiguous_recommend(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    assert runner.invoke(app, ["scan", str(root)]).exit_code == 0
    result = runner.invoke(app, ["impact", "recommend", "--root", str(root)])
    assert result.exit_code == 1
    text = _output(result)
    assert "Ambiguous" in text
    assert "python_function:app.recommend" in text
    assert "python_function:services.recommend" in text
    assert "Direct:" not in result.stdout


def test_ac009_sql_cte_and_create_table(golden_graph: Graph) -> None:
    ids = _ids(golden_graph)
    assert "sql_table:customers" in ids
    assert "sql_table:orders" in ids
    assert "sql_table:customer_summary" in ids
    assert "sql_table:active_users" not in ids
    pairs = _pairs(golden_graph)
    assert ("sql_table:customers", "sql_table:customer_summary", "read_by") in pairs
    assert ("sql_table:orders", "sql_table:customer_summary", "read_by") in pairs


def test_ac010_dbt_without_manifest(golden_graph: Graph) -> None:
    assert not (FIXTURE / "dbt" / "target" / "manifest.json").exists()
    pairs = _pairs(golden_graph)
    assert ("dbt_source:raw.users", "dbt_model:stg_users", "source") in pairs
    assert ("dbt_model:stg_users", "dbt_model:user_features", "ref") in pairs
    by_key = {(edge.source, edge.target, edge.type): edge for edge in golden_graph.edges}
    source_edge = by_key[("dbt_source:raw.users", "dbt_model:stg_users", "source")]
    ref_edge = by_key[("dbt_model:stg_users", "dbt_model:user_features", "ref")]
    assert source_edge.evidence == "dbt_source"
    assert ref_edge.evidence == "dbt_ref"
    assert source_edge.confidence == "high"
    assert ref_edge.confidence == "high"
    assert ref_edge.metadata.get("reference") == "stg_users"
    assert str(ref_edge.metadata.get("file_path", "")).endswith("user_features.sql")


def test_ac011_langgraph_literal_edges(golden_graph: Graph) -> None:
    edge = next(
        item
        for item in golden_graph.edges
        if item.source == "langgraph_node:retrieve"
        and item.target == "langgraph_node:generate"
    )
    assert edge.confidence == "high"
    assert edge.evidence == "langgraph_add_edge"
    assert edge.type == "routes_to"


def test_ac012_llm_literal_vs_env(
    golden_graph: Graph, monkeypatch: pytest.MonkeyPatch
) -> None:
    literal = next(node for node in golden_graph.nodes if node.id == "llm:openai:gpt-5")
    assert literal.metadata["provider"] == "openai"
    assert literal.metadata["model"] == "gpt-5"
    dynamic = next(node for node in golden_graph.nodes if node.id == "llm:openai:dynamic")
    assert dynamic.metadata.get("dynamic") is True
    assert dynamic.metadata.get("model") != "gpt-5"
    monkeypatch.setenv("MODEL", "invented-from-env")
    rescanned = Scanner().scan(FIXTURE).graph
    ids = _ids(rescanned)
    assert "llm:openai:invented-from-env" not in ids
    assert "llm:openai:dynamic" in ids
    assert os.environ.get("MODEL") == "invented-from-env"


def test_ac013_vector_collection_literal(golden_graph: Graph) -> None:
    ids = _ids(golden_graph)
    assert "vector_store:qdrant" in ids
    assert "vector_collection:company_docs" in ids
    assert (
        "vector_store:qdrant",
        "vector_collection:company_docs",
        "feeds",
    ) in _pairs(golden_graph)


def test_ac014_external_http_literal(golden_graph: Graph) -> None:
    edge = next(
        item
        for item in golden_graph.edges
        if item.target == "external_system:api.stripe.com"
    )
    assert edge.source == "python_function:app.retrieve_documents"
    assert edge.type == "depends_on"
    assert edge.confidence == "medium"
    assert edge.evidence == "http_call"


def test_ac015_core_modules_do_not_import_typer() -> None:
    for path in SRC.rglob("*.py"):
        if path.name == "cli.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".", maxsplit=1)[0] != "typer"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", maxsplit=1)[0]
                assert root != "typer"
                assert node.module != "daygent.cli"


def test_ac016_lineage_api_direction(golden_graph: Graph) -> None:
    down = [node.id for node in get_descendants(golden_graph, "dbt_model:stg_users")]
    up = [node.id for node in get_ancestors(golden_graph, "dbt_model:user_features")]
    assert "dbt_model:user_features" in down
    assert "dbt_source:raw.users" in up
    assert "dbt_model:stg_users" in up


def test_ac017_deterministic_rescan() -> None:
    first = canonical_graph_dict(Scanner().scan(FIXTURE).graph)
    second = canonical_graph_dict(Scanner().scan(FIXTURE).graph)
    first["scan"]["timestamp"] = second["scan"]["timestamp"] = "stable"
    assert first == second
    assert first["nodes"]
    assert first["edges"]


def test_ac018_missing_graph_artifact(tmp_path: Path) -> None:
    graph = runner.invoke(app, ["graph", "--root", str(tmp_path)])
    assert graph.exit_code == 1
    assert "daygent scan" in _output(graph)
    impact = runner.invoke(app, ["impact", "stg_users", "--root", str(tmp_path)])
    assert impact.exit_code == 1
    assert "daygent scan" in _output(impact)


def test_scoping_drops_unrelated_python(golden_graph: Graph) -> None:
    ids = _ids(golden_graph)
    assert SCOPED_OUT.isdisjoint(ids)
    assert MUST_KEEP <= ids


def test_include_and_exclude_globs(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    generated = root / "generated"
    generated.mkdir()
    (generated / "noise.sql").write_text(
        "CREATE TABLE secret_noise AS SELECT * FROM hidden_src;\n",
        encoding="utf-8",
    )
    (root / "daygent.yml").write_text(
        "include:\n  - dbt/**\n  - sql/**\nexclude:\n  - generated/**\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["scan", str(root)])
    assert result.exit_code == 0, _output(result)
    payload = json.loads((root / ".daygent" / "graph.json").read_text(encoding="utf-8"))
    ids = {node["id"] for node in payload["nodes"]}
    assert "sql_table:secret_noise" not in ids
    assert "dbt_model:stg_users" in ids
    assert "sql_table:customer_summary" in ids
    assert "api_route:POST:/ask" not in ids
    assert "llm:openai:gpt-5" not in ids


def test_html_include_source_is_bounded(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    assert runner.invoke(app, ["scan", str(root)]).exit_code == 0
    default_html = (root / ".daygent" / "graph.html")
    runner.invoke(app, ["graph", "--html", "--root", str(root)])
    default_text = default_html.read_text(encoding="utf-8")
    assert "where email is not null" not in default_text
    assert "This HTML contains source-code excerpts." not in default_text.split(
        "<script>", 1
    )[0]
    sourced = runner.invoke(
        app, ["graph", "--html", "--include-source", "--root", str(root)]
    )
    assert sourced.exit_code == 0, _output(sourced)
    text = default_html.read_text(encoding="utf-8")
    assert "This HTML contains source-code excerpts." in text
    payload = _payload_from_html(text)
    assert payload["includes_source"] is True
    edge = next(
        item
        for item in payload["edges"]
        if item.get("metadata", {}).get("reference") == "stg_users"
        and item["type"] == "ref"
    )
    excerpt = edge["source_excerpt"]
    assert "ref('stg_users')" in excerpt["text"]
    assert excerpt["end_line"] - excerpt["start_line"] < MAX_EXCERPT_LINES
    assert viewer_payload(Scanner().scan(root).graph)["includes_source"] is False


def test_v01_does_not_resolve_cross_file_python_calls(golden_graph: Graph) -> None:
    pairs = {(edge.source, edge.target) for edge in golden_graph.edges}
    assert ("python_function:services.recommend", "python_function:app.ask") not in pairs
    assert ("python_function:app.ask", "python_function:services.recommend") not in pairs
    assert ("dbt_model:user_features", "python_function:app.recommend") not in pairs
    assert ("python_function:app.recommend", "dbt_model:user_features") not in pairs


def test_fixture_contains_no_secrets() -> None:
    forbidden = ("sk-", "AKIA", "BEGIN PRIVATE KEY", "api_key=", "secret_key=")
    for path in FIXTURE.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for token in forbidden:
            assert token.lower() not in text, f"{path} contains {token}"
