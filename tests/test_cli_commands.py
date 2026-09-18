"""CLI scan, graph, and impact command tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from daygent.cli import app
from daygent.graph.store import save_graph
from daygent.models import Edge, Graph, Node, ScanMetadata

runner = CliRunner()


def _output(result) -> str:
    """Combine captured stdout/stderr for assertions."""
    stderr = getattr(result, "stderr", "") or ""
    return f"{result.stdout}{stderr}{result.output}"


def _chain_graph() -> Graph:
    """raw_users → stg_users → recommend() → POST /recommend plus a twin recommend."""
    return Graph(
        nodes=[
            Node(id="sql_table:raw_users", name="raw_users", type="sql_table"),
            Node(id="dbt_model:stg_users", name="stg_users", type="dbt_model"),
            Node(id="dbt_model:user_features", name="user_features", type="dbt_model"),
            Node(
                id="python_function:app.recommend",
                name="recommend",
                type="python_function",
                file_path="app.py",
            ),
            Node(
                id="python_function:other.recommend",
                name="recommend",
                type="python_function",
                file_path="other.py",
            ),
            Node(
                id="api_route:POST:/recommend",
                name="POST /recommend",
                type="api_route",
            ),
        ],
        edges=[
            Edge(source="sql_table:raw_users", target="dbt_model:stg_users", type="feeds"),
            Edge(
                source="dbt_model:stg_users",
                target="dbt_model:user_features",
                type="ref",
            ),
            Edge(
                source="dbt_model:user_features",
                target="python_function:app.recommend",
                type="feeds",
            ),
            Edge(
                source="python_function:app.recommend",
                target="api_route:POST:/recommend",
                type="invoked_by",
            ),
        ],
        scan=ScanMetadata(timestamp="2026-09-18T10:00:00Z", files_scanned=2),
    )


def _save_chain(tmp_path: Path) -> Path:
    path = tmp_path / ".daygent" / "graph.json"
    save_graph(_chain_graph(), path)
    return path


def test_scan_writes_graph_json(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    result = runner.invoke(app, ["scan", str(tmp_path)])
    assert result.exit_code == 0, _output(result)
    graph_path = tmp_path / ".daygent" / "graph.json"
    assert graph_path.is_file()
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    assert payload["version"] == "0.1"
    assert "nodes" in payload and "edges" in payload and "scan" in payload
    text = _output(result)
    assert "Python files" in text
    assert "dbt models" in text
    assert "FastAPI routes" in text
    assert "LangGraph nodes" in text
    assert "External systems" in text
    assert "nodes" in text.lower()
    assert "edges" in text.lower()
    assert ".daygent/graph.json" in text.replace("\n", "")


def test_scan_summary_counts_dbt(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    (tmp_path / "dbt_project.yml").write_text("name: demo\n", encoding="utf-8")
    (models / "stg_users.sql").write_text(
        "select * from {{ source('raw', 'users') }}\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["scan", str(tmp_path)])
    assert result.exit_code == 0, _output(result)
    assert "dbt models" in _output(result)


def test_malformed_source_still_exits_0(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def broken(\n", encoding="utf-8")
    result = runner.invoke(app, ["scan", str(tmp_path)])
    assert result.exit_code == 0, _output(result)
    assert (tmp_path / ".daygent" / "graph.json").is_file()
    assert "Warning" in _output(result)
    assert "bad.py" in _output(result)


def test_invalid_config_fails(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    config = tmp_path / "broken.yml"
    config.write_text("include: not-a-list\n", encoding="utf-8")
    result = runner.invoke(app, ["scan", str(tmp_path), "--config", str(config)])
    assert result.exit_code != 0
    assert "Invalid" in _output(result) or "invalid" in _output(result).lower()


def test_unwritable_output_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")

    def boom(*_args: object, **_kwargs: object) -> Path:
        raise OSError("read-only file system")

    monkeypatch.setattr("daygent.cli.save_graph", boom)
    result = runner.invoke(app, ["scan", str(tmp_path)])
    assert result.exit_code == 1
    assert "Unable to write graph" in _output(result)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_unwritable_directory_fails(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    locked = tmp_path / "locked"
    locked.mkdir()
    (tmp_path / "daygent.yml").write_text("output: locked/graph.json\n", encoding="utf-8")
    locked.chmod(0o555)
    try:
        result = runner.invoke(app, ["scan", str(tmp_path)])
        if result.exit_code == 0:
            pytest.skip("filesystem ignored directory chmod")
        assert result.exit_code == 1
        assert "Unable to write graph" in _output(result)
    finally:
        locked.chmod(0o755)


def test_verbose_output_has_diagnostics_not_contents(tmp_path: Path) -> None:
    secret = "sk-not-a-real-secret-value"
    (tmp_path / "mod.py").write_text(
        f'API_KEY = "{secret}"\n\ndef run():\n    return API_KEY\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["scan", str(tmp_path), "--verbose"])
    assert result.exit_code == 0, _output(result)
    text = _output(result)
    assert "Parsing" in text
    assert secret not in text


def test_scan_interrupt_does_not_write_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr("daygent.cli.Scanner.scan", boom)
    result = runner.invoke(app, ["scan", str(tmp_path)])
    assert result.exit_code != 0
    assert not (tmp_path / ".daygent" / "graph.json").exists()


def test_graph_positional_path_matches_scan_target(tmp_path: Path) -> None:
    """`daygent graph PATH` must open PATH/.daygent, not the current directory."""
    nested = tmp_path / "example"
    nested.mkdir()
    _save_chain(nested)
    result = runner.invoke(app, ["graph", str(nested)])
    assert result.exit_code == 0, _output(result)
    assert "raw_users" in result.stdout
    assert "POST /recommend" in result.stdout
    _save_chain(tmp_path)
    result = runner.invoke(app, ["graph", "--root", str(tmp_path)])
    assert result.exit_code == 0, _output(result)
    text = result.stdout
    assert "raw_users" in text
    assert "stg_users" in text
    assert "recommend()" in text
    assert "POST /recommend" in text
    assert "↓" in text
    chain = next(
        block
        for block in text.split("\n\n")
        if "raw_users" in block and "POST /recommend" in block
    )
    assert chain.index("raw_users") < chain.index("stg_users")
    assert chain.index("stg_users") < chain.index("recommend()")
    assert chain.index("recommend()") < chain.index("POST /recommend")


def test_graph_json_is_complete_document(tmp_path: Path) -> None:
    _save_chain(tmp_path)
    result = runner.invoke(app, ["graph", "--json", "--root", str(tmp_path)])
    assert result.exit_code == 0, _output(result)
    payload = json.loads(result.stdout)
    assert payload["version"] == "0.1"
    assert {node["id"] for node in payload["nodes"]} >= {
        "sql_table:raw_users",
        "api_route:POST:/recommend",
    }
    assert payload["edges"]
    assert "scan" in payload


def test_graph_mermaid_direction_and_safe_ids(tmp_path: Path) -> None:
    _save_chain(tmp_path)
    result = runner.invoke(app, ["graph", "--mermaid", "--root", str(tmp_path)])
    assert result.exit_code == 0, _output(result)
    text = result.stdout
    assert text.startswith("graph LR")
    assert "-->" in text
    assert ":" not in "".join(
        line.split("[", maxsplit=1)[0] for line in text.splitlines() if "-->" in line
    )
    assert "sql_table:raw_users" not in text.split("-->")[0]
    assert "stg_users" in text
    src_line = next(
        line
        for line in text.splitlines()
        if "raw_users" in line and "-->" not in line
    )
    tgt_line = next(
        line
        for line in text.splitlines()
        if "stg_users" in line and "-->" not in line
    )
    src_id = src_line.strip().split("[", maxsplit=1)[0].strip()
    tgt_id = tgt_line.strip().split("[", maxsplit=1)[0].strip()
    assert f"{src_id} --> {tgt_id}" in text
    for token in src_id, tgt_id:
        assert token.replace("_", "").isalnum()


def test_graph_missing_tells_user_to_scan(tmp_path: Path) -> None:
    result = runner.invoke(app, ["graph", "--root", str(tmp_path)])
    assert result.exit_code == 1
    text = _output(result)
    assert "daygent scan" in text
    assert "Graph not found" in text


def test_impact_direct_and_transitive(tmp_path: Path) -> None:
    _save_chain(tmp_path)
    result = runner.invoke(app, ["impact", "stg_users", "--root", str(tmp_path)])
    assert result.exit_code == 0, _output(result)
    text = result.stdout
    assert "Impact: stg_users" in text
    assert "Direct:" in text
    assert "user_features" in text
    assert "Downstream:" in text
    assert "recommend" in text
    assert "POST /recommend" in text
    direct_block = text.split("Downstream:")[0]
    downstream_block = text.split("Downstream:")[1]
    assert "user_features" in direct_block
    assert "POST /recommend" in downstream_block
    assert "user_features" not in downstream_block


def test_impact_depth_limit(tmp_path: Path) -> None:
    _save_chain(tmp_path)
    result = runner.invoke(
        app, ["impact", "stg_users", "--depth", "1", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0, _output(result)
    text = result.stdout
    assert "user_features" in text
    assert "POST /recommend" not in text
    assert "recommend()" not in text


def test_impact_exact_and_ambiguous_resolution(tmp_path: Path) -> None:
    _save_chain(tmp_path)
    exact = runner.invoke(
        app, ["impact", "python_function:app.recommend", "--root", str(tmp_path)]
    )
    assert exact.exit_code == 0, _output(exact)
    assert "POST /recommend" in exact.stdout
    ambiguous = runner.invoke(app, ["impact", "recommend", "--root", str(tmp_path)])
    assert ambiguous.exit_code == 1
    text = _output(ambiguous)
    assert "Ambiguous" in text
    assert "python_function:app.recommend" in text
    assert "python_function:other.recommend" in text
    assert "Direct:" not in ambiguous.stdout


def test_impact_unknown_node_suggestions(tmp_path: Path) -> None:
    _save_chain(tmp_path)
    result = runner.invoke(app, ["impact", "stgg_users", "--root", str(tmp_path)])
    assert result.exit_code == 1
    text = _output(result)
    assert "stg_users" in text
    assert "Did you mean" in text or "suggestions" in text.lower()


def test_impact_json(tmp_path: Path) -> None:
    _save_chain(tmp_path)
    result = runner.invoke(
        app, ["impact", "stg_users", "--depth", "2", "--json", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0, _output(result)
    payload = json.loads(result.stdout)
    assert payload["query"] == "stg_users"
    assert payload["resolved_id"] == "dbt_model:stg_users"
    assert payload["direct"][0]["name"] == "user_features"
    assert payload["direct"][0]["depth"] == 1
    depths = [item["depth"] for item in payload["direct"] + payload["downstream"]]
    assert depths and max(depths) <= 2
    assert all(item["depth"] <= 2 for item in payload["downstream"])
    assert payload["affected_count"] == len(payload["direct"]) + len(payload["downstream"])
    names = {item["name"] for item in payload["downstream"]}
    assert "POST /recommend" not in names
    assert "recommend" in names


def test_impact_missing_graph(tmp_path: Path) -> None:
    result = runner.invoke(app, ["impact", "stg_users", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "daygent scan" in _output(result)


def test_impact_json_ambiguous(tmp_path: Path) -> None:
    _save_chain(tmp_path)
    result = runner.invoke(
        app, ["impact", "recommend", "--json", "--root", str(tmp_path)]
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"] == "ambiguous_node"
    assert len(payload["candidates"]) == 2
