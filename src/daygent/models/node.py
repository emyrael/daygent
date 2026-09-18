"""Graph node model.

Metadata is static-analysis evidence only. Do not store resolved secrets
(api_key, password, access_token, credential, env values).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from daygent.models.types import NodeType


def _non_empty_str(value: object, field_name: str) -> str:
    """Strip and reject blank strings."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _prefer_text(left: str | None, right: str | None) -> str | None:
    """Keep a non-empty value; if both differ, the lexicographically smaller one."""
    if not left:
        return right or left
    if not right:
        return left
    return left if left == right else min(left, right)


def merge_metadata(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Union metadata with order-independent conflict handling.

    Same key, one empty → keep the non-empty value.
    Same key, both set and different → keep the lexicographically smaller
    JSON-ish string form so merge(a, b) == merge(b, a).
    """
    merged: dict[str, Any] = {}
    for key in sorted(set(left) | set(right)):
        in_left = key in left
        in_right = key in right
        if in_left and not in_right:
            merged[key] = left[key]
            continue
        if in_right and not in_left:
            merged[key] = right[key]
            continue
        lv, rv = left[key], right[key]
        if lv in (None, "") and rv not in (None, ""):
            merged[key] = rv
        elif rv in (None, "") and lv not in (None, ""):
            merged[key] = lv
        elif lv == rv:
            merged[key] = lv
        else:
            merged[key] = lv if str(lv) <= str(rv) else rv
    return merged


class Node(BaseModel):
    """One vertex in the Daygent graph.

    `type` is a string (typically a `NodeType` value) so additional kinds can be
    added without changing traversal.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: str
    file_path: str | None = None
    line_number: int | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "name", "type", mode="before")
    @classmethod
    def _require_non_empty(cls, value: object, info: Any) -> str:
        return _non_empty_str(value, info.field_name)

    def merge(self, other: Node) -> Node:
        """Merge another node with the same id. Result does not depend on order."""
        if other.id != self.id:
            raise ValueError(f"Cannot merge nodes with different ids: {self.id} {other.id}")
        line_number = self.line_number
        if line_number is None:
            line_number = other.line_number
        elif other.line_number is not None:
            line_number = min(line_number, other.line_number)
        return Node(
            id=self.id,
            name=_prefer_text(self.name, other.name) or self.name,
            type=_prefer_text(self.type, other.type) or self.type,
            file_path=_prefer_text(self.file_path, other.file_path),
            line_number=line_number,
            metadata=merge_metadata(self.metadata, other.metadata),
        )

    def typed(self) -> NodeType | str:
        """Return a NodeType if this is a known kind, else the raw string."""
        try:
            return NodeType(self.type)
        except ValueError:
            return self.type
