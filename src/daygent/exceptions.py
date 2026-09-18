"""Daygent error types."""

from __future__ import annotations


class DaygentError(Exception):
    """Base error for recoverable Daygent failures."""


class DaygentConfigError(DaygentError):
    """Raised when daygent.yml cannot be loaded or is invalid."""


class GraphNotFoundError(DaygentError):
    """Raised when .daygent/graph.json (or configured output) is missing."""
