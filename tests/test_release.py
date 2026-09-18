"""Checks for the PyPI release workflow. No secrets, no network, no publish."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def test_release_workflow_uses_oidc_not_tokens() -> None:
    text = RELEASE.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    on_block = data.get("on", data.get(True))
    assert on_block["push"]["tags"] == ["v*"]
    assert "branches" not in on_block.get("push", {})
    assert "release" not in on_block
    assert data["jobs"]["build"]["steps"][1]["with"]["python-version"] == "3.11"
    assert 'pip install -e ".[dev]"' in text
    assert "pytest" in text
    assert "python -m build" in text
    assert "python -m twine check dist/*" in text
    publish = data["jobs"]["publish"]
    assert publish["needs"] == "build"
    assert publish["environment"]["name"] == "pypi"
    assert publish["permissions"]["id-token"] == "write"
    assert "pypa/gh-action-pypi-publish@release/v1" in text
    assert "python -m build" not in "".join(
        step.get("run", "") for step in publish.get("steps", [])
    )
    assert "TWINE_PASSWORD" not in text
    assert "PYPI_API_TOKEN" not in text
    assert "pypi-token" not in text.lower()
    assert "password" not in text.lower()
    assert ".pypirc" not in text
    assert "secrets." not in text


def test_ci_workflow_does_not_publish() -> None:
    text = CI.read_text(encoding="utf-8")
    assert "pypa/gh-action-pypi-publish" not in text
    assert "secrets." not in text
