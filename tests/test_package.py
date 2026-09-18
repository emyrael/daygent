"""Smoke tests for packaging and core/CLI isolation."""

from __future__ import annotations

import ast
from pathlib import Path

from daygent import GRAPH_CONVENTION, __version__

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "daygent"


def test_version() -> None:
    assert __version__ == "0.1.0"


def test_graph_convention_is_locked() -> None:
    assert GRAPH_CONVENTION == "upstream_to_downstream_consumer"


def test_core_modules_do_not_import_typer_or_cli() -> None:
    """Parsers, graph, scanner, and analysis must stay CLI-free."""
    for path in SRC.rglob("*.py"):
        if path.name == "cli.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", maxsplit=1)[0]
                    assert root != "typer", f"{path} imports typer"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", maxsplit=1)[0]
                assert root != "typer", f"{path} imports typer"
                assert node.module != "daygent.cli", f"{path} imports daygent.cli"


def test_license_is_mit() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = "MIT"' in pyproject


def test_gitignore_excludes_daygent_artifact() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".daygent/" in gitignore.splitlines()
