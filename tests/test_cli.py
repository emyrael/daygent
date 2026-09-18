"""CLI smoke tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from daygent import __version__
from daygent.cli import app

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "scan" in result.stdout
    assert "graph" in result.stdout
    assert "impact" in result.stdout


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
