"""In-memory graph document. File I/O belongs to a later issue."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from daygent import GRAPH_SCHEMA_VERSION
from daygent.models.edge import Edge
from daygent.models.node import Node


def utc_now_iso() -> str:
    """Return a UTC timestamp with a Z suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ProjectMetadata(BaseModel):
    """Optional project identity stored with a scan."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    path: str | None = None


class ScanMetadata(BaseModel):
    """Provenance for one scan run. Populated by later scan/store issues."""

    model_config = ConfigDict(extra="forbid")

    timestamp: str
    files_scanned: int = 0
    warnings: list[str] = Field(default_factory=list)


def merge_node_list(nodes: Iterable[Node]) -> list[Node]:
    """Collapse nodes with the same id. Sorted by id for determinism."""
    by_id: dict[str, Node] = {}
    for node in nodes:
        existing = by_id.get(node.id)
        by_id[node.id] = existing.merge(node) if existing else node
    return sorted(by_id.values(), key=lambda item: item.id)


def merge_edge_list(edges: Iterable[Edge]) -> list[Edge]:
    """Collapse edges with the same (source, target, type). Sorted by key."""
    by_key: dict[tuple[str, str, str], Edge] = {}
    for edge in edges:
        existing = by_key.get(edge.key)
        by_key[edge.key] = existing.merge(edge) if existing else edge
    return sorted(by_key.values(), key=lambda item: item.key)


class Graph(BaseModel):
    """Normalized Daygent graph. Edges point upstream → downstream consumer."""

    model_config = ConfigDict(extra="forbid")

    version: str = GRAPH_SCHEMA_VERSION
    project: ProjectMetadata = Field(default_factory=ProjectMetadata)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    scan: ScanMetadata = Field(
        default_factory=lambda: ScanMetadata(timestamp=utc_now_iso())
    )

    def sorted(self) -> Graph:
        """Return a copy with nodes and edges in canonical order."""
        return Graph(
            version=self.version,
            project=self.project.model_copy(deep=True),
            nodes=sorted(self.nodes, key=lambda node: node.id),
            edges=sorted(self.edges, key=lambda edge: edge.key),
            scan=ScanMetadata(
                timestamp=self.scan.timestamp,
                files_scanned=self.scan.files_scanned,
                warnings=list(self.scan.warnings),
            ),
        )

    def to_canonical_dict(self) -> dict[str, Any]:
        """JSON-ready dict with sorted collections (no disk I/O)."""
        return self.sorted().model_dump(mode="json")

    def node_index(self) -> dict[str, Node]:
        """Map node id → node."""
        return {node.id: node for node in self.nodes}
