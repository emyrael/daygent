"""Offline HTML graph viewer tests. No browser, no network."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from daygent.cli import app
from daygent.graph.store import save_graph
from daygent.models import Edge, Graph, Node, ScanMetadata
from daygent.viewer import html_path_for_graph, render_html, write_html
from daygent.viewer.html import viewer_payload
from daygent.viewer.source import excerpt_around

runner = CliRunner()
CDN_MARKERS = (
    "cdn.",
    "jsdelivr",
    "unpkg.com",
    "cdnjs",
    "googleapis.com",
    "<script src=\"http",
    "<script src='http",
    'src="https://',
    "fetch(",
    "XMLHttpRequest",
    "navigator.sendBeacon",
)


def _output(result) -> str:
    """Combine captured stdout/stderr for assertions."""
    stderr = getattr(result, "stderr", "") or ""
    return f"{result.stdout}{stderr}{result.output}"


def _graph() -> Graph:
    """Tiny A → B graph with metadata that must not break HTML."""
    return Graph(
        nodes=[
            Node(
                id="sql_table:raw_users",
                name="raw_users",
                type="sql_table",
                file_path="raw.sql",
                line_number=3,
                metadata={"kind": "table"},
            ),
            Node(
                id="dbt_model:stg_users",
                name="stg_users",
                type="dbt_model",
                file_path="stg_users.sql",
                metadata={
                    "note": "</script><script>alert(1)</script>",
                    "label": "A & B <C>",
                },
            ),
        ],
        edges=[
            Edge(
                source="sql_table:raw_users",
                target="dbt_model:stg_users",
                type="ref",
                confidence="high",
                evidence="dbt_ref",
                metadata={
                    "file_path": "stg_users.sql",
                    "line_number": 8,
                    "reference": "raw_users",
                },
            )
        ],
        scan=ScanMetadata(timestamp="2026-09-18T11:00:00Z", files_scanned=1),
    )


def _save(tmp_path: Path) -> Path:
    path = tmp_path / ".daygent" / "graph.json"
    save_graph(_graph(), path)
    return path


def _payload(html: str) -> dict:
    match = re.search(
        r'<script type="application/json" id="daygent-data">(.*?)</script>',
        html,
        re.S,
    )
    assert match, "embedded graph payload missing"
    return json.loads(match.group(1))


def test_html_file_generated(tmp_path: Path) -> None:
    _save(tmp_path)
    result = runner.invoke(app, ["graph", "--html", "--root", str(tmp_path)])
    assert result.exit_code == 0, _output(result)
    html_path = tmp_path / ".daygent" / "graph.html"
    assert html_path.is_file()
    assert str(html_path) in _output(result)
    text = html_path.read_text(encoding="utf-8")
    assert text.lstrip().startswith("<!DOCTYPE html>")


def test_graph_data_embedded_without_cdn_or_fetch(tmp_path: Path) -> None:
    html = render_html(_graph())
    lowered = html.lower()
    for marker in CDN_MARKERS:
        assert marker.lower() not in lowered
    assert "fetch(\"./graph.json\")" not in html
    assert "./graph.json" not in html
    payload = _payload(html)
    ids = {node["id"] for node in payload["nodes"]}
    assert ids == {"sql_table:raw_users", "dbt_model:stg_users"}
    edge = payload["edges"][0]
    assert edge["source"] == "sql_table:raw_users"
    assert edge["target"] == "dbt_model:stg_users"
    assert payload["convention"] == "A → B means B depends on A"


def test_escaping_prevents_script_injection() -> None:
    html = render_html(_graph())
    data_block = re.search(
        r'<script type="application/json" id="daygent-data">(.*?)</script>',
        html,
        re.S,
    )
    assert data_block is not None
    raw = data_block.group(1)
    assert "</script>" not in raw
    assert "<script>" not in raw
    payload = json.loads(raw)
    stg = next(node for node in payload["nodes"] if node["id"] == "dbt_model:stg_users")
    assert stg["metadata"]["note"] == "</script><script>alert(1)</script>"
    assert html.count("<script") == 2


def test_write_html_uses_graph_sibling_path(tmp_path: Path) -> None:
    json_path = _save(tmp_path)
    html_path = html_path_for_graph(json_path)
    assert html_path == tmp_path / ".daygent" / "graph.html"
    written = write_html(_graph(), html_path)
    assert written == html_path
    assert "raw_users" in written.read_text(encoding="utf-8")


def test_custom_root_writes_html(tmp_path: Path) -> None:
    _save(tmp_path)
    result = runner.invoke(app, ["graph", "--html", "--root", str(tmp_path)])
    assert result.exit_code == 0, _output(result)
    assert (tmp_path / ".daygent" / "graph.html").is_file()


def test_missing_graph_html_uses_scan_hint(tmp_path: Path) -> None:
    result = runner.invoke(app, ["graph", "--html", "--root", str(tmp_path)])
    assert result.exit_code == 1
    text = _output(result)
    assert "daygent scan" in text
    assert "Graph not found" in text


def test_open_browser_is_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _save(tmp_path)
    opened: list[str] = []
    monkeypatch.setattr(
        "daygent.cli.webbrowser.open",
        lambda url: opened.append(url) or True,
    )
    result = runner.invoke(app, ["graph", "--html", "--open", "--root", str(tmp_path)])
    assert result.exit_code == 0, _output(result)
    assert len(opened) == 1
    assert opened[0].startswith("file:")
    assert opened[0].endswith("graph.html")
    assert (tmp_path / ".daygent" / "graph.html").is_file()


def test_open_without_html_fails(tmp_path: Path) -> None:
    _save(tmp_path)
    result = runner.invoke(app, ["graph", "--open", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "--html" in _output(result)


def test_html_defaults_to_dark_mode_with_theme_toggle() -> None:
    html = render_html(_graph())
    assert 'data-theme="dark"' in html
    assert 'id="theme-toggle"' in html
    assert "daygent-theme" in html
    assert "tooltip" in html


def test_html_hides_python_functions_by_default() -> None:
    html = render_html(_graph())
    assert 'id="hide-functions"' in html
    assert re.search(r'id="hide-functions"[^>]*checked', html)


def test_inspector_structure_and_direction_legend() -> None:
    html = render_html(_graph())
    assert 'id="direction-legend"' in html
    assert "A → B means B depends on A" in html
    assert 'id="details-head"' in html
    assert 'id="details-connections"' in html
    assert 'id="details-source"' in html
    assert 'id="details-meta"' in html
    assert 'id="inspect-actions"' in html
    assert 'id="graph-controls"' in html
    assert "Direct upstream" in html
    assert "Blast radius" in html
    assert "visible ·" in html or "visible" in html
    assert "addKv" in html or "kv-key" in html
    assert "shouldShowEdgeLabel" in html
    assert "selectEdge" in html
    assert "moveNode" in html
    assert "data-type" in html
    assert "connectionLabel" in html
    assert "prettyValue" in html
    assert "relationshipLabel" in html
    assert 'id="source-banner" hidden' in html
    payload = _payload(html)
    assert payload["includes_source"] is False


def test_payload_includes_structured_edge_evidence() -> None:
    payload = viewer_payload(_graph())
    edge = payload["edges"][0]
    assert edge["evidence"] == "dbt_ref"
    assert edge["metadata"]["reference"] == "raw_users"
    assert edge["metadata"]["file_path"] == "stg_users.sql"
    assert edge["metadata"]["line_number"] == 8
    assert "source_excerpt" not in edge
    assert payload["includes_source"] is False


def test_default_html_does_not_embed_source_contents(tmp_path: Path) -> None:
    secret = "UNIQUE_SOURCE_BODY_SHOULD_NOT_LEAK"
    models = tmp_path / "models"
    models.mkdir()
    (models / "stg.sql").write_text(
        "\n".join([secret] + [f"select {i}" for i in range(40)]),
        encoding="utf-8",
    )
    graph = Graph(
        nodes=[
            Node(
                id="dbt_model:stg",
                name="stg",
                type="dbt_model",
                file_path="models/stg.sql",
                line_number=10,
            )
        ],
        scan=ScanMetadata(timestamp="2026-09-18T12:00:00Z", files_scanned=1),
    )
    html = render_html(graph, source_root=tmp_path)
    assert secret not in html
    payload = _payload(html)
    assert payload["includes_source"] is False
    assert "source_excerpt" not in payload["nodes"][0]


def test_include_source_embeds_bounded_excerpt_only(tmp_path: Path) -> None:
    start_marker = "KEEP_AWAY_FILE_START"
    focus = "FOCUS_LINE_FOR_EXCERPT"
    end_marker = "KEEP_AWAY_FILE_END"
    lines = [start_marker] + [f"-- pad {i}" for i in range(2, 20)] + [focus]
    lines.extend(f"-- tail {i}" for i in range(22, 80))
    lines.append(end_marker)
    path = tmp_path / "models" / "marts" / "customer_features.sql"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    focus_line = lines.index(focus) + 1
    graph = Graph(
        nodes=[
            Node(
                id="dbt_model:customer_features",
                name="customer_features",
                type="dbt_model",
                file_path="models/marts/customer_features.sql",
                line_number=focus_line,
                metadata={"dbt": True, "provider": "openai", "model": "gpt-5"},
            )
        ],
        scan=ScanMetadata(timestamp="2026-09-18T12:00:00Z", files_scanned=1),
    )
    html = render_html(graph, include_source=True, source_root=tmp_path)
    assert "This HTML contains source-code excerpts." in html
    payload = _payload(html)
    assert payload["includes_source"] is True
    excerpt = payload["nodes"][0]["source_excerpt"]
    assert focus in excerpt["text"]
    assert start_marker not in excerpt["text"]
    assert end_marker not in excerpt["text"]
    assert excerpt["end_line"] - excerpt["start_line"] <= 10
    data_block = html.split('id="daygent-data">', 1)[1].split("</script>", 1)[0]
    assert "</script>" not in data_block


def test_include_source_cli_and_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.sql").write_text("select 1\n", encoding="utf-8")
    _save(tmp_path)
    opened: list[str] = []
    monkeypatch.setattr(
        "daygent.cli.webbrowser.open",
        lambda url: opened.append(url) or True,
    )
    result = runner.invoke(
        app,
        ["graph", "--html", "--include-source", "--open", "--root", str(tmp_path)],
    )
    assert result.exit_code == 0, _output(result)
    html = (tmp_path / ".daygent" / "graph.html").read_text(encoding="utf-8")
    assert "This HTML contains source-code excerpts." in html
    assert opened and opened[0].endswith("graph.html")


def test_include_source_requires_html(tmp_path: Path) -> None:
    _save(tmp_path)
    result = runner.invoke(app, ["graph", "--include-source", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "--html" in _output(result)


def test_missing_source_file_is_omitted_gracefully(tmp_path: Path) -> None:
    graph = Graph(
        nodes=[
            Node(
                id="dbt_model:ghost",
                name="ghost",
                type="dbt_model",
                file_path="missing.sql",
                line_number=3,
            ),
            Node(id="sql_table:raw", name="raw", type="sql_table"),
        ],
        scan=ScanMetadata(timestamp="2026-09-18T12:00:00Z", files_scanned=1),
    )
    html = render_html(graph, include_source=True, source_root=tmp_path)
    payload = _payload(html)
    ghost = next(node for node in payload["nodes"] if node["id"] == "dbt_model:ghost")
    assert "source_excerpt" not in ghost


def test_excerpt_helper_never_returns_whole_long_file(tmp_path: Path) -> None:
    path = tmp_path / "big.sql"
    path.write_text("\n".join(f"line-{i}" for i in range(1, 201)), encoding="utf-8")
    excerpt = excerpt_around(path, 100)
    assert excerpt is not None
    assert "line-1\n" not in excerpt["text"] + "\n"
    assert "line-200" not in excerpt["text"]
    assert excerpt["text"].count("\n") <= 10


def test_source_excerpt_escaping() -> None:
    graph = Graph(
        nodes=[
            Node(
                id="dbt_model:stg_users",
                name="stg_users",
                type="dbt_model",
                metadata={"note": "</script><script>alert(1)</script>"},
            )
        ],
        edges=[
            Edge(
                source="dbt_model:stg_users",
                target="dbt_model:stg_users",
                type="depends_on",
                metadata={"raw": "</script>"},
            )
        ],
        scan=ScanMetadata(timestamp="2026-09-18T12:00:00Z", files_scanned=1),
    )
    html = render_html(graph)
    data_block = re.search(
        r'<script type="application/json" id="daygent-data">(.*?)</script>',
        html,
        re.S,
    )
    assert data_block is not None
    raw = data_block.group(1)
    assert "</script>" not in raw
    assert html.count("<script") == 2


def test_existing_interactions_remain_in_viewer() -> None:
    html = render_html(_graph())
    for marker in (
        'id="search"',
        'id="type-filter"',
        'id="hide-functions"',
        'id="btn-fit"',
        'id="btn-reset-view"',
        'id="btn-upstream"',
        'id="btn-downstream"',
        'id="btn-impact"',
        'id="btn-clear"',
        "setHighlight",
        "clearSelection",
        "selectNode",
        "focusNode",
        'class: "edge-hit"',
        " visible · ",
        "prettyValue",
        "addKv",
        "inspect-title",
        "inspect-counts",
    ):
        assert marker in html


def test_cli_default_html_does_not_embed_source_when_files_exist(
    tmp_path: Path,
) -> None:
    secret = "CLI_DEFAULT_MUST_NOT_EMBED_SOURCE"
    models = tmp_path / "models"
    models.mkdir()
    (models / "stg.sql").write_text(
        "\n".join([secret, "select 1", "select 2"]),
        encoding="utf-8",
    )
    graph = Graph(
        nodes=[
            Node(
                id="dbt_model:stg",
                name="stg",
                type="dbt_model",
                file_path="models/stg.sql",
                line_number=2,
            )
        ],
        scan=ScanMetadata(timestamp="2026-09-18T12:00:00Z", files_scanned=1),
    )
    save_graph(graph, tmp_path / ".daygent" / "graph.json")
    result = runner.invoke(app, ["graph", "--html", "--root", str(tmp_path)])
    assert result.exit_code == 0, _output(result)
    html = (tmp_path / ".daygent" / "graph.html").read_text(encoding="utf-8")
    assert secret not in html
    assert 'id="source-banner" hidden' in html
    payload = _payload(html)
    assert payload["includes_source"] is False
    assert "source_excerpt" not in payload["nodes"][0]


def test_missing_line_number_does_not_embed_whole_file(tmp_path: Path) -> None:
    marker = "WHOLE_FILE_SHOULD_STAY_ON_DISK"
    path = tmp_path / "models" / "wide.sql"
    path.parent.mkdir()
    path.write_text("\n".join([marker] + [f"select {i}" for i in range(80)]), encoding="utf-8")
    graph = Graph(
        nodes=[
            Node(
                id="dbt_model:wide",
                name="wide",
                type="dbt_model",
                file_path="models/wide.sql",
            )
        ],
        scan=ScanMetadata(timestamp="2026-09-18T12:00:00Z", files_scanned=1),
    )
    html = render_html(graph, include_source=True, source_root=tmp_path)
    assert marker not in html
    payload = _payload(html)
    assert "source_excerpt" not in payload["nodes"][0]


def test_include_source_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    secret = "ESCAPED_FILE_MUST_NOT_EMBED"
    (tmp_path / "secret.sql").write_text(secret, encoding="utf-8")
    graph = Graph(
        nodes=[
            Node(
                id="dbt_model:escaped",
                name="escaped",
                type="dbt_model",
                file_path="../secret.sql",
                line_number=1,
            )
        ],
        scan=ScanMetadata(timestamp="2026-09-18T12:00:00Z", files_scanned=1),
    )
    html = render_html(graph, include_source=True, source_root=root)
    assert secret not in html
    payload = _payload(html)
    assert "source_excerpt" not in payload["nodes"][0]


def test_include_source_escapes_excerpt_script_tags(tmp_path: Path) -> None:
    payload_marker = "</script><script>alert(1)</script>"
    path = tmp_path / "models" / "evil.sql"
    path.parent.mkdir()
    path.write_text(
        "\n".join(["select 1", payload_marker, "select 2"]),
        encoding="utf-8",
    )
    graph = Graph(
        nodes=[
            Node(
                id="dbt_model:evil",
                name="evil",
                type="dbt_model",
                file_path="models/evil.sql",
                line_number=2,
            )
        ],
        scan=ScanMetadata(timestamp="2026-09-18T12:00:00Z", files_scanned=1),
    )
    html = render_html(graph, include_source=True, source_root=tmp_path)
    data_block = re.search(
        r'<script type="application/json" id="daygent-data">(.*?)</script>',
        html,
        re.S,
    )
    assert data_block is not None
    raw = data_block.group(1)
    assert "</script>" not in raw
    assert html.count("<script") == 2
    excerpt = _payload(html)["nodes"][0]["source_excerpt"]
    assert payload_marker in excerpt["text"]
