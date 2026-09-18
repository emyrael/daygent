"""Local checks for the GitHub Actions workflow. No secrets, no network."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_workflow_runs_pytest_on_supported_python() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    on_block = data.get("on", data.get(True))
    assert on_block["push"]["branches"] == ["main"]
    assert on_block["pull_request"]["branches"] == ["main"]
    job = data["jobs"]["test"]
    assert job["strategy"]["matrix"]["python-version"] == ["3.11", "3.12"]
    assert "python -m pip install --upgrade pip" in text
    assert 'pip install -e ".[dev]"' in text
    assert "pytest" in text
    assert "secrets." not in text
    assert "coverage" not in text.lower()
    assert "publish" not in text.lower()
    assert "deploy" not in text.lower()
