"""Parser contract. Technology-specific logic lives in parser implementations, not the scanner."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from daygent.config import DaygentConfig
from daygent.models import Edge, Node


class ParseContext(BaseModel):
    """Shared context passed to every parser."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: Path
    config: DaygentConfig
    verbose: bool = False


class ParseResult(BaseModel):
    """Normalized parser output consumed by the graph builder."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BaseParser(ABC):
    """Pluggable file parser.

    Register Airflow, Spark, Python, SQL, dbt, and other parsers without
    changing the scanner. Implementations MUST NOT import, exec, or evaluate
    scanned project code, and MUST NOT contact the network.
    """

    name: str = "base"

    @abstractmethod
    def supports(self, path: Path, context: ParseContext) -> bool:
        """Return True if this parser should handle the file."""

    @abstractmethod
    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        """Parse one file into nodes, edges, and warnings."""


class ParserRegistry:
    """Ordered registry of parsers. First supporting parser wins per file."""

    def __init__(self, parsers: list[BaseParser] | None = None) -> None:
        self._parsers: list[BaseParser] = list(parsers or [])

    def register(self, parser: BaseParser) -> None:
        """Append a parser. Later registrations can handle new file kinds."""
        self._parsers.append(parser)

    def parsers(self) -> list[BaseParser]:
        """Return registered parsers in order."""
        return list(self._parsers)

    def choose(self, path: Path, context: ParseContext) -> BaseParser | None:
        """Return the first parser that supports this path."""
        for parser in self._parsers:
            if parser.supports(path, context):
                return parser
        return None


def default_registry() -> ParserRegistry:
    """Built-in parser registry.

    Technology parsers (Python, SQL, dbt, FastAPI, LangGraph, …) register here
    in later issues. v0.1 scanner still walks and dispatches with an empty set.
    """
    return ParserRegistry()
