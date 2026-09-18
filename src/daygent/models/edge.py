"""Directed graph edge.

Graph convention (locked):
    A → B means B depends on / consumes / is affected by A.

Example (FastAPI): recommend() → POST /recommend
    source = python_function:…
    target = api_route:POST:/recommend
    type = invoked_by

Impact analysis walks with the arrows. Upstream lineage walks against them.
Do not reverse this convention.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from daygent.models.node import merge_metadata
from daygent.models.types import Confidence, EdgeType, max_confidence


def _non_empty_str(value: object, field_name: str) -> str:
    """Strip and reject blank strings."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


class Edge(BaseModel):
    """Directed dependency from upstream `source` to downstream `target`.

    Duplicate identity is `(source, target, type)`. Confidence defaults to LOW
    when the emitter cannot justify a stronger claim.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    type: str
    confidence: Confidence = Confidence.LOW
    evidence: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source", "target", "type", mode="before")
    @classmethod
    def _require_non_empty(cls, value: object, info: Any) -> str:
        return _non_empty_str(value, info.field_name)

    @property
    def key(self) -> tuple[str, str, str]:
        """Uniqueness key used when merging duplicate edges."""
        return (self.source, self.target, self.type)

    def merge(self, other: Edge) -> Edge:
        """Merge a duplicate edge. Highest confidence wins; order-independent."""
        if self.key != other.key:
            raise ValueError(f"Cannot merge edges with different keys: {self.key} {other.key}")
        tokens = {part for part in (self.evidence, other.evidence) if part}
        evidence = "|".join(sorted(tokens)) if tokens else None
        return Edge(
            source=self.source,
            target=self.target,
            type=self.type,
            confidence=max_confidence(self.confidence, other.confidence),
            evidence=evidence,
            metadata=merge_metadata(self.metadata, other.metadata),
        )


__all__ = ["Confidence", "Edge", "EdgeType", "max_confidence"]
