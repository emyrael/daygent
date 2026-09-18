"""Deterministic local graph.json persistence. No database, no network."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from daygent.exceptions import DaygentError, GraphNotFoundError
from daygent.models import Graph

GRAPH_FILENAME = "graph.json"
DEFAULT_GRAPH_DIR = ".daygent"

_TOP_LEVEL_KEYS = ("version", "project", "nodes", "edges", "scan")
_SCAN_KEYS = ("timestamp", "files_scanned", "warnings")
_NODE_KEYS = ("id", "name", "type", "file_path", "line_number", "metadata")
_EDGE_KEYS = ("source", "target", "type", "confidence", "evidence", "metadata")


class GraphStoreError(DaygentError):
    """Raised when a graph artifact exists but cannot be parsed."""


def default_graph_path(root: Path | None = None) -> Path:
    """Return `<root>/.daygent/graph.json`, defaulting to the current directory."""
    base = Path(root) if root is not None else Path.cwd()
    return base / DEFAULT_GRAPH_DIR / GRAPH_FILENAME


def _ensure_utc_z(timestamp: str) -> str:
    """Normalize a timestamp to UTC with a trailing Z."""
    value = timestamp.strip()
    if value.endswith("+00:00"):
        return f"{value[:-6]}Z"
    if value.endswith("Z"):
        return value
    return f"{value}Z" if value else value


def _ordered(mapping: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Rebuild a mapping in the given key order, then leftover keys sorted."""
    ordered: dict[str, Any] = {}
    for key in keys:
        if key in mapping:
            ordered[key] = mapping[key]
    for key in sorted(k for k in mapping if k not in keys):
        ordered[key] = mapping[key]
    return ordered


def _sorted_metadata(value: Any) -> Any:
    """Sort dict keys recursively so metadata dumps are deterministic."""
    if isinstance(value, dict):
        return {key: _sorted_metadata(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sorted_metadata(item) for item in value]
    return value


def canonical_graph_dict(graph: Graph) -> dict[str, Any]:
    """JSON-ready payload with sorted nodes/edges and stable key order."""
    payload = graph.sorted().model_dump(mode="json")
    nodes = [
        _ordered({**node, "metadata": _sorted_metadata(node.get("metadata") or {})}, _NODE_KEYS)
        for node in payload.get("nodes", [])
    ]
    edges = [
        _ordered({**edge, "metadata": _sorted_metadata(edge.get("metadata") or {})}, _EDGE_KEYS)
        for edge in payload.get("edges", [])
    ]
    scan = dict(payload.get("scan") or {})
    scan["timestamp"] = _ensure_utc_z(str(scan.get("timestamp") or ""))
    scan["warnings"] = list(scan.get("warnings") or [])
    project = dict(payload.get("project") or {})
    return _ordered(
        {
            "version": payload.get("version") or "0.1",
            "project": _sorted_metadata(project),
            "nodes": nodes,
            "edges": edges,
            "scan": _ordered(scan, _SCAN_KEYS),
        },
        _TOP_LEVEL_KEYS,
    )


def render_graph_json(graph: Graph) -> str:
    """Render canonical JSON text (trailing newline, UTF-8)."""
    return json.dumps(canonical_graph_dict(graph), indent=2, ensure_ascii=False) + "\n"


class GraphStore:
    """Load and atomically save a Graph document."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @classmethod
    def default(cls, root: Path | None = None) -> GraphStore:
        """Store at `<root>/.daygent/graph.json`."""
        return cls(default_graph_path(root))

    def save(self, graph: Graph) -> Path:
        """Write canonical JSON via a temp file in the same directory, then replace."""
        text = render_graph_json(graph)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".graph.",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            tmp_path.replace(self.path)
        except BaseException:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return self.path

    def load(self) -> Graph:
        """Load a previously saved graph."""
        if not self.path.is_file():
            raise GraphNotFoundError(
                f"Graph not found at {self.path}. Run `daygent scan` first."
            )
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GraphStoreError(f"Invalid graph JSON at {self.path}: {exc}") from exc
        return Graph.model_validate(data)


def save_graph(
    graph: Graph,
    path: Path | None = None,
    *,
    root: Path | None = None,
) -> Path:
    """Save a graph. Default path is `<root or cwd>/.daygent/graph.json`."""
    target = Path(path) if path is not None else default_graph_path(root)
    return GraphStore(target).save(graph)


def load_graph(path: Path | None = None, *, root: Path | None = None) -> Graph:
    """Load a graph. Default path is `<root or cwd>/.daygent/graph.json`."""
    target = Path(path) if path is not None else default_graph_path(root)
    return GraphStore(target).load()
