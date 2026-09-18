"""dbt ref()/source() lineage from Jinja SQL. Never runs dbt or warehouse queries."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from daygent.models import Confidence, Edge, EdgeType, Node, NodeType
from daygent.models.ids import make_dbt_model_id, make_dbt_source_id, posix_relpath
from daygent.parsers.base import BaseParser, ParseContext, ParseResult
from daygent.parsers.evidence import evidence_metadata, first_line_matching
from daygent.parsers.sql_parser import looks_like_jinja_sql

_REF_RE = re.compile(
    r"\{\{\s*ref\(\s*(?:['\"][^'\"]+['\"]\s*,\s*)?['\"]([^'\"]+)['\"]\s*\)",
    re.IGNORECASE,
)
_SOURCE_RE = re.compile(
    r"\{\{\s*source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)",
    re.IGNORECASE,
)
_SKIP_YAML_NAMES = frozenset(
    {
        "daygent.yml",
        "daygent.yaml",
        "dbt_project.yml",
        "dbt_project.yaml",
        "packages.yml",
        "package-lock.yml",
        "selectors.yml",
        "profiles.yml",
    }
)


def find_dbt_root(path: Path, scan_root: Path) -> Path | None:
    """Return the nearest ancestor (within the scan) that contains dbt_project.yml."""
    scan_root = scan_root.resolve()
    current = path.parent.resolve()
    while True:
        if (current / "dbt_project.yml").is_file() or (current / "dbt_project.yaml").is_file():
            return current
        if current == scan_root or current.parent == current:
            break
        current = current.parent
    if (scan_root / "dbt_project.yml").is_file() or (scan_root / "dbt_project.yaml").is_file():
        return scan_root
    return None


def jinja_is_unbalanced(source: str) -> bool:
    """Heuristic for broken Jinja that dbt would also fail to compile."""
    return source.count("{{") != source.count("}}") or source.count("{%") != source.count("%}")


def extract_refs(source: str) -> list[str]:
    """Return model names passed to ref(), last argument when a package is given."""
    return [match.group(1) for match in _REF_RE.finditer(source)]


def extract_sources(source: str) -> list[tuple[str, str]]:
    """Return (source_name, table) pairs from source() calls."""
    return [(match.group(1), match.group(2)) for match in _SOURCE_RE.finditer(source)]


def _model_node(name: str, file_path: str | None = None) -> Node:
    """Create a dbt_model node."""
    return Node(
        id=make_dbt_model_id(name),
        name=name,
        type=NodeType.DBT_MODEL,
        file_path=file_path,
        metadata={"dbt": True},
    )


def _source_node(source: str, table: str, file_path: str | None = None) -> Node:
    """Create a dbt_source node."""
    return Node(
        id=make_dbt_source_id(source, table),
        name=f"{source}.{table}",
        type=NodeType.DBT_SOURCE,
        file_path=file_path,
        metadata={"dbt": True, "source": source, "table": table},
    )


def _lineage_edge(
    source_id: str,
    target_id: str,
    edge_type: str,
    evidence: str,
    *,
    file_path: str | None = None,
    line_number: int | None = None,
    reference: str | None = None,
) -> Edge:
    """Upstream model/source → current model, with known static evidence."""
    return Edge(
        source=source_id,
        target=target_id,
        type=edge_type,
        confidence=Confidence.HIGH,
        evidence=evidence,
        metadata=evidence_metadata(
            file_path=file_path,
            line_number=line_number,
            reference=reference,
        ),
    )


def parse_sources_yaml(data: Any, file_path: str) -> list[Node]:
    """Collect dbt_source nodes from a schema/sources YAML mapping."""
    if not isinstance(data, dict):
        return []
    nodes: list[Node] = []
    for block in data.get("sources") or []:
        if not isinstance(block, dict):
            continue
        source_name = str(block.get("name") or "").strip()
        if not source_name:
            continue
        for table in block.get("tables") or []:
            if not isinstance(table, dict):
                continue
            table_name = str(table.get("name") or "").strip()
            if not table_name:
                continue
            nodes.append(_source_node(source_name, table_name, file_path))
    return nodes


def parse_manifest(data: dict[str, Any]) -> tuple[list[Node], list[Edge]]:
    """Merge optional target/manifest.json nodes and depends_on edges."""
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    seen: set[tuple[str, str, str]] = set()

    def add_node(node: Node) -> None:
        nodes[node.id] = node

    for payload in (data.get("sources") or {}).values():
        if not isinstance(payload, dict):
            continue
        source_name = str(payload.get("source_name") or "").strip()
        table = str(payload.get("name") or "").strip()
        if source_name and table:
            add_node(_source_node(source_name, table))

    for payload in (data.get("nodes") or {}).values():
        if not isinstance(payload, dict) or payload.get("resource_type") != "model":
            continue
        name = str(payload.get("name") or "").strip()
        if not name:
            continue
        current = _model_node(name, str(payload.get("original_file_path") or "") or None)
        add_node(current)
        depends = payload.get("depends_on") or {}
        for dep in depends.get("nodes") or []:
            if not isinstance(dep, str):
                continue
            edge: Edge | None = None
            if dep.startswith("source."):
                parts = dep.split(".")
                if len(parts) >= 4:
                    src_node = _source_node(parts[-2], parts[-1])
                    add_node(src_node)
                    edge = _lineage_edge(src_node.id, current.id, EdgeType.SOURCE, "dbt_manifest")
            elif dep.startswith("model."):
                model_name = dep.split(".")[-1]
                add_node(_model_node(model_name))
                edge = _lineage_edge(
                    make_dbt_model_id(model_name), current.id, EdgeType.REF, "dbt_manifest"
                )
            if edge is not None and edge.key not in seen:
                seen.add(edge.key)
                edges.append(edge)
    return list(nodes.values()), edges


class DbtParser(BaseParser):
    """Parse dbt models and sources from Jinja SQL and YAML. No dbt CLI."""

    name = "dbt"

    def supports(self, path: Path, context: ParseContext) -> bool:
        """Claim Jinja SQL, dbt model SQL, and dbt YAML (not profiles.yml)."""
        if path.name in _SKIP_YAML_NAMES:
            return False
        suffix = path.suffix.lower()
        dbt_root = find_dbt_root(path, context.root)
        if suffix == ".sql":
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                return dbt_root is not None
            return looks_like_jinja_sql(source) or dbt_root is not None
        if suffix in {".yml", ".yaml"}:
            return dbt_root is not None
        return False

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        """Extract ref/source lineage and optional manifest edges."""
        relative = posix_relpath(path, context.root)
        dbt_root = find_dbt_root(path, context.root)
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            return ParseResult(warnings=[f"Warning: unable to read {relative}: {exc}"])

        if path.suffix.lower() in {".yml", ".yaml"}:
            return self._parse_yaml(source, relative)

        warnings: list[str] = []
        if looks_like_jinja_sql(source) and jinja_is_unbalanced(source):
            warnings.append(f"Warning: broken Jinja in {relative}")

        model_name = path.stem
        current = _model_node(model_name, relative)
        nodes: dict[str, Node] = {current.id: current}
        edges: list[Edge] = []
        seen: set[tuple[str, str, str]] = set()

        for ref_name in extract_refs(source):
            upstream = _model_node(ref_name)
            nodes[upstream.id] = upstream
            line = first_line_matching(
                source, [f"ref('{ref_name}')", f'ref("{ref_name}")']
            )
            edge = _lineage_edge(
                upstream.id,
                current.id,
                EdgeType.REF,
                "dbt_ref",
                file_path=relative,
                line_number=line,
                reference=ref_name,
            )
            if edge.key not in seen:
                seen.add(edge.key)
                edges.append(edge)

        for source_name, table in extract_sources(source):
            upstream = _source_node(source_name, table)
            nodes[upstream.id] = upstream
            line = first_line_matching(
                source,
                [
                    f"source('{source_name}', '{table}')",
                    f'source("{source_name}", "{table}")',
                ],
            )
            edge = _lineage_edge(
                upstream.id,
                current.id,
                EdgeType.SOURCE,
                "dbt_source",
                file_path=relative,
                line_number=line,
                reference=f"{source_name}.{table}",
            )
            if edge.key not in seen:
                seen.add(edge.key)
                edges.append(edge)

        if dbt_root is not None:
            manifest_nodes, manifest_edges, manifest_warnings = self._load_manifest(dbt_root)
            warnings.extend(manifest_warnings)
            for node in manifest_nodes:
                nodes.setdefault(node.id, node)
            for edge in manifest_edges:
                if edge.key not in seen:
                    seen.add(edge.key)
                    edges.append(edge)

        return ParseResult(nodes=list(nodes.values()), edges=edges, warnings=warnings)

    def _parse_yaml(self, source: str, relative: str) -> ParseResult:
        """Parse schema/sources YAML into dbt_source nodes."""
        try:
            data = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            return ParseResult(warnings=[f"Warning: invalid dbt YAML in {relative}: {exc}"])
        return ParseResult(nodes=parse_sources_yaml(data, relative))

    def _load_manifest(self, dbt_root: Path) -> tuple[list[Node], list[Edge], list[str]]:
        """Optionally merge target/manifest.json. Missing file is not an error."""
        manifest_path = dbt_root / "target" / "manifest.json"
        if not manifest_path.is_file():
            return [], [], []
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return [], [], [f"Warning: unable to read dbt manifest {manifest_path.name}: {exc}"]
        if not isinstance(data, dict):
            return [], [], [f"Warning: dbt manifest {manifest_path.name} is not a mapping"]
        nodes, edges = parse_manifest(data)
        return nodes, edges, []
