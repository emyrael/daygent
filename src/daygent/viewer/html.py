"""Render a self-contained offline HTML graph viewer from a Graph."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from daygent.graph.assets import ASSET_TYPES, project_asset_graph
from daygent.graph.render import display_name
from daygent.models import Graph
from daygent.viewer.source import excerpt_around, safe_repo_file

_DATA_TOKEN = "__DAYGENT_DATA__"
_CSS_TOKEN = "__DAYGENT_CSS__"
_JS_TOKEN = "__DAYGENT_JS__"
_BANNER_TOKEN = "__DAYGENT_BANNER__"

_SOURCE_BANNER = (
    '<p class="banner" id="source-banner">This HTML contains source-code excerpts.</p>'
)

_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Daygent graph</title>
  <style>
__DAYGENT_CSS__
  </style>
</head>
<body>
  <aside class="panel">
    <header class="brand">
      <div>
        <h1>Daygent</h1>
      </div>
      <div class="brand-actions">
        <button type="button" id="theme-toggle" aria-label="Toggle color theme">Light</button>
        <button type="button" id="sidebar-hide" aria-label="Hide sidebar" aria-expanded="true">Hide</button>
      </div>
    </header>
    __DAYGENT_BANNER__
    <p class="convention" id="direction-legend">A → B means B depends on A</p>
    <p class="hint">Scroll to zoom · drag canvas to pan · drag a node to move it · click a node or edge</p>
    <p class="stats" id="stats"></p>
    <section class="graph-controls" id="graph-controls">
      <h2>Graph</h2>
      <div class="actions">
        <button type="button" id="btn-fit">Fit</button>
        <button type="button" id="btn-reset-view">Reset view</button>
        <button type="button" id="btn-clear-focus" hidden>Clear focus</button>
      </div>
      <label class="field">
        Search nodes
        <input id="search" type="search" placeholder="Name or id" autocomplete="off">
      </label>
      <p class="hint">Enter cycles matches · Shift+Enter goes back</p>
      <div class="field">
        Filter by type
        <div id="type-filter" class="type-multiselect">
          <button type="button" id="type-filter-btn" class="type-multiselect-btn" aria-haspopup="listbox" aria-expanded="false" aria-controls="type-filter-menu">
            <span id="type-filter-label">All types</span>
          </button>
          <div id="type-filter-menu" class="type-multiselect-menu" role="listbox" aria-multiselectable="true" hidden></div>
        </div>
      </div>
      <label class="field">
        When filtering
        <select id="filter-mode">
          <option value="context">Keep lineage context</option>
          <option value="only">Show matches only</option>
        </select>
      </label>
      <label class="check">
        <input id="show-details" type="checkbox">
        Show implementation details
      </label>
      <div id="legend" class="legend"></div>
    </section>
    <section id="details" class="details">
      <h2>Inspector</h2>
      <p class="muted" id="details-empty">Click a node or edge to inspect it.</p>
      <div id="details-body" hidden>
        <div id="details-head"></div>
        <div class="actions inspect-actions" id="inspect-actions">
          <button type="button" id="btn-upstream">Direct upstream</button>
          <button type="button" id="btn-downstream">Direct downstream</button>
          <button type="button" id="btn-impact">Blast radius</button>
          <button type="button" id="btn-focus">Focus lineage</button>
          <button type="button" id="btn-clear">Reset selection</button>
        </div>
        <section id="details-connections">
          <h3>Connections</h3>
          <div id="connections-list"></div>
        </section>
        <section id="details-source">
          <h3>Source / Evidence</h3>
          <div id="source-list"></div>
        </section>
        <section id="details-meta">
          <h3>Metadata</h3>
          <div id="meta-list"></div>
        </section>
      </div>
    </section>
    <div id="sidebar-split" class="sidebar-split" role="separator" aria-orientation="vertical" aria-label="Resize sidebar" aria-valuemin="240" aria-valuemax="720" aria-valuenow="400" tabindex="0"></div>
  </aside>
  <main>
    <button type="button" id="sidebar-show" class="sidebar-show" aria-label="Show sidebar" hidden>Show sidebar</button>
    <svg id="canvas" role="img" aria-label="Dependency graph"></svg>
    <div id="tooltip" hidden></div>
  </main>
  <script type="application/json" id="daygent-data">__DAYGENT_DATA__</script>
  <script>
__DAYGENT_JS__
  </script>
</body>
</html>
"""


def html_path_for_graph(graph_json_path: Path) -> Path:
    """Return `.daygent/graph.html` next to the saved graph JSON."""
    return Path(graph_json_path).with_name("graph.html")


def _read_static(name: str) -> str:
    """Load a bundled viewer asset from the installed package."""
    return (files("daygent.viewer") / "static" / name).read_text(encoding="utf-8")


def _embed_json(payload: dict[str, Any]) -> str:
    """JSON-encode payload so it cannot break out of a script tag."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _excerpt_for(
    *,
    file_path: str | None,
    line_number: int | None,
    source_root: Path,
) -> dict[str, Any] | None:
    """Load a bounded excerpt when the file and line are known."""
    if not file_path:
        return None
    path = safe_repo_file(source_root, file_path)
    if path is None:
        return None
    return excerpt_around(path, line_number)


def viewer_payload(
    graph: Graph,
    *,
    include_source: bool = False,
    source_root: Path | None = None,
) -> dict[str, Any]:
    """Subset of graph.json used by the viewer.

    Default payload never includes source-file contents.
    """
    ordered = graph.sorted()
    root = source_root.resolve() if source_root is not None else None
    nodes = []
    for node in ordered.nodes:
        item: dict[str, Any] = {
            "id": node.id,
            "name": node.name,
            "label": display_name(node),
            "type": node.type,
            "file_path": node.file_path,
            "line_number": node.line_number,
            "metadata": dict(node.metadata or {}),
        }
        if include_source and root is not None:
            excerpt = _excerpt_for(
                file_path=node.file_path,
                line_number=node.line_number,
                source_root=root,
            )
            if excerpt is not None:
                item["source_excerpt"] = excerpt
        nodes.append(item)
    edges = []
    for edge in ordered.edges:
        meta = dict(edge.metadata or {})
        item = {
            "source": edge.source,
            "target": edge.target,
            "type": edge.type,
            "confidence": str(edge.confidence) if edge.confidence else None,
            "evidence": edge.evidence,
            "metadata": meta,
        }
        if include_source and root is not None:
            excerpt = _excerpt_for(
                file_path=str(meta.get("file_path") or "") or None,
                line_number=(
                    meta.get("line_number")
                    if isinstance(meta.get("line_number"), int)
                    else None
                ),
                source_root=root,
            )
            if excerpt is not None:
                item["source_excerpt"] = excerpt
        edges.append(item)
    projection = project_asset_graph(ordered)
    asset_edges = [
        {
            "source": edge.source,
            "target": edge.target,
            "type": edge.type,
            "confidence": str(edge.confidence) if edge.confidence else None,
            "evidence": edge.evidence,
            "metadata": dict(edge.metadata or {}),
        }
        for edge in projection.edges
    ]
    return {
        "convention": "A → B means B depends on A",
        "includes_source": bool(include_source),
        "nodes": nodes,
        "edges": edges,
        # Display-only contraction of implementation nodes. The detailed graph
        # above is untouched and still powers impact analysis.
        "asset_types": sorted(str(item) for item in ASSET_TYPES),
        "asset_edges": asset_edges,
    }


def render_html(
    graph: Graph,
    *,
    include_source: bool = False,
    source_root: Path | None = None,
) -> str:
    """Return a self-contained HTML document with embedded graph data."""
    css = _read_static("viewer.css")
    js = _read_static("viewer.js")
    payload = _embed_json(
        viewer_payload(graph, include_source=include_source, source_root=source_root)
    )
    banner = (
        _SOURCE_BANNER
        if include_source
        else '<p class="banner" id="source-banner" hidden></p>'
    )
    html = (
        _TEMPLATE.replace(_CSS_TOKEN, css)
        .replace(_JS_TOKEN, js)
        .replace(_BANNER_TOKEN, banner)
        .replace(_DATA_TOKEN, payload)
    )
    return html


def write_html(
    graph: Graph,
    path: Path,
    *,
    include_source: bool = False,
    source_root: Path | None = None,
) -> Path:
    """Write the viewer HTML atomically next to graph.json."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = render_html(graph, include_source=include_source, source_root=source_root)
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target
