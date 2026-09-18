"""Parser package. Python, FastAPI, AI, HTTP, SQL, and dbt parsers register here."""

from __future__ import annotations

from daygent.parsers.ai_parser import AIParser, LangGraphDetector
from daygent.parsers.base import (
    BaseParser,
    ParseContext,
    ParserRegistry,
    ParseResult,
)
from daygent.parsers.dbt_parser import DbtParser
from daygent.parsers.fastapi_parser import FastAPIParser
from daygent.parsers.http_parser import HttpParser, LiteralHttpDetector
from daygent.parsers.python_parser import PythonParser
from daygent.parsers.sql_parser import SqlParser, looks_like_jinja_sql


def default_registry() -> ParserRegistry:
    """Built-in parsers. Multiple parsers may match the same file."""
    registry = ParserRegistry()
    registry.register(PythonParser())
    registry.register(FastAPIParser())
    registry.register(AIParser())
    registry.register(HttpParser())
    registry.register(SqlParser())
    registry.register(DbtParser())
    return registry


__all__ = [
    "AIParser",
    "BaseParser",
    "DbtParser",
    "FastAPIParser",
    "HttpParser",
    "LangGraphDetector",
    "LiteralHttpDetector",
    "ParseContext",
    "ParseResult",
    "ParserRegistry",
    "PythonParser",
    "SqlParser",
    "default_registry",
    "looks_like_jinja_sql",
]
