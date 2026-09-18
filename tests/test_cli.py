"""CLI smoke tests."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from daygent import __version__
from daygent.cli import app

runner = CliRunner()


def _help(args: list[str]) -> str:
    """Run a help command and return wrapped-plain stdout. Needs no saved graph."""
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    text = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    return re.sub(r"\s+", " ", text)


def test_help_lists_commands() -> None:
    text = _help(["--help"])
    assert "scan" in text
    assert "graph" in text
    assert "impact" in text


def test_top_level_help_explains_workflow() -> None:
    text = _help(["--help"])
    assert "Daygent statically scans data + AI repositories" in text
    assert "local dependency graph" in text
    assert "Scan a repository and build .daygent/graph.json" in text
    assert "View/export the saved dependency graph" in text
    assert "Show downstream blast radius for a node" in text
    assert "daygent scan ." in text
    assert "daygent graph" in text
    assert "daygent graph --html --open" in text
    assert "daygent impact <node>" in text
    assert "daygent <command> --help" in text


def test_scan_help_documents_static_scan() -> None:
    text = _help(["scan", "--help"])
    assert "daygent scan" in text
    assert "[PATH]" in text
    assert ".daygent/graph.json" in text
    assert "statically" in text
    assert "does not execute" in text
    assert "does not contact databases" in text
    assert "--verbose" in text
    assert "--config" in text
    assert "daygent scan ." in text
    assert "daygent scan ./my-project" in text
    assert "daygent scan . --verbose" in text
    assert "daygent scan . --config daygent.yml" in text


def test_graph_help_documents_modes() -> None:
    text = _help(["graph", "--help"])
    assert "daygent scan" in text
    assert "A → B means B depends on A" in text
    assert "--json" in text
    assert "--mermaid" in text
    assert "--html" in text
    assert "--open" in text
    assert "--include-source" in text
    assert "--root" in text
    assert "daygent graph --html --open" in text
    assert "daygent graph --html --include-source" in text
    assert "contain source code" in text or "embeds source" in text


def test_impact_help_documents_any_node() -> None:
    text = _help(["impact", "--help"])
    assert "any node" in text.lower()
    assert "What could be affected if this node changes?" in text
    assert "--depth" in text
    assert "--json" in text
    assert "--root" in text
    assert "daygent impact stg_users" in text
    assert "daygent impact warehouse.orders" in text
    assert "daygent impact retrieve_documents" in text
    assert "daygent impact langgraph_node:retrieve" in text
    assert "daygent impact stg_users --depth 2" in text
    assert "daygent impact stg_users --json" in text
    assert "Ambiguous" in text or "ambiguous" in text


def test_help_does_not_require_saved_graph(tmp_path: Path) -> None:
    result = runner.invoke(app, ["graph", "--help"])
    assert result.exit_code == 0
    assert "Graph not found" not in result.output
    missing = runner.invoke(app, ["graph", "--root", str(tmp_path)])
    assert missing.exit_code == 1


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_installed_console_script_help() -> None:
    scripts_dir = Path(sys.prefix) / ("Scripts" if sys.platform == "win32" else "bin")
    name = "daygent.exe" if sys.platform == "win32" else "daygent"
    executable = scripts_dir / name
    assert executable.is_file(), f"daygent console script missing at {executable}"
    completed = subprocess.run(
        [str(executable), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "scan" in completed.stdout
    assert "graph" in completed.stdout
    assert "impact" in completed.stdout
    assert "Quick start" in completed.stdout
    assert "Daygent statically scans" in completed.stdout
