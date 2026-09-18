"""Daygent error types."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daygent.models import Node


class DaygentError(Exception):
    """Base error for recoverable Daygent failures."""


class DaygentConfigError(DaygentError):
    """Raised when daygent.yml cannot be loaded or is invalid."""


class GraphNotFoundError(DaygentError):
    """Raised when .daygent/graph.json (or configured output) is missing."""


class NodeNotFoundError(DaygentError):
    """Raised when a lineage/impact query matches no graph node."""

    def __init__(
        self,
        message: str,
        *,
        query: str | None = None,
        suggestions: list[Node] | None = None,
    ) -> None:
        self.query = query or message
        self.suggestions = list(suggestions or [])
        super().__init__(message)


class AmbiguousNodeError(DaygentError):
    """Raised when a node name matches more than one graph node.

    Callers must not pick a candidate silently.
    """

    def __init__(self, query: str, candidates: list[Node]) -> None:
        self.query = query
        self.candidates = list(candidates)
        lines = [f"Ambiguous node {query!r}.", "", "Choose one:", ""]
        for node in self.candidates:
            hint = node.id.split(":", maxsplit=1)[-1]
            lines.append(f'  daygent impact "{hint}"')
        lines.extend(["", "Stable IDs:", ""])
        for node in self.candidates:
            lines.append(f"  {node.id}")
        super().__init__("\n".join(lines))
