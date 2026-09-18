"""Bounded source excerpts for the optional HTML viewer. Never embed whole files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

EXCERPT_RADIUS = 5
MAX_EXCERPT_LINES = EXCERPT_RADIUS * 2 + 1


def safe_repo_file(root: Path, relative: str) -> Path | None:
    """Resolve `relative` under `root`, rejecting path escape."""
    if not relative or not relative.strip():
        return None
    base = root.resolve()
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def excerpt_around(
    path: Path,
    line_number: int | None,
    *,
    radius: int = EXCERPT_RADIUS,
) -> dict[str, Any] | None:
    """Return at most `radius` lines before and after `line_number`.

    Missing files or unknown lines yield None. Never returns an entire large file.
    """
    if line_number is None or line_number < 1 or radius < 0:
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    start = max(1, line_number - radius)
    end = min(len(lines), line_number + radius)
    if end - start + 1 > MAX_EXCERPT_LINES:
        end = start + MAX_EXCERPT_LINES - 1
    snippet = lines[start - 1 : end]
    return {
        "start_line": start,
        "end_line": start + len(snippet) - 1 if snippet else start,
        "focus_line": line_number,
        "text": "\n".join(snippet),
    }
