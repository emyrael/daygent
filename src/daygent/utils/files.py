"""Filesystem helpers for repository walking. Pathlib only. No network."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

IGNORE_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".next",
        ".daygent",
    }
)

SUPPORTED_SUFFIXES = frozenset({".py", ".sql", ".yml", ".yaml"})
CONFIG_FILENAMES = frozenset({"daygent.yml", "daygent.yaml"})
MAX_FILE_BYTES = 10 * 1024 * 1024


def is_ignored_dir(name: str) -> bool:
    """Return True if a directory name is always skipped."""
    return name in IGNORE_DIR_NAMES


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a glob where `**` spans directories and `*` spans one segment."""
    i = 0
    out: list[str] = ["^"]
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def _match_glob(path: str, pattern: str) -> bool:
    """Return True if POSIX `path` matches a glob `pattern`."""
    normalized = pattern.replace("\\", "/").strip()
    if not normalized:
        return False
    return _glob_to_regex(normalized).match(path) is not None


def matches_globs(relative_posix: str, patterns: list[str]) -> bool:
    """Return True if a POSIX relative path matches any include/exclude glob.

    Basename-only patterns like `skip.py` also match nested files named that way.
    """
    relative = relative_posix.replace("\\", "/").lstrip("./")
    name = relative.rsplit("/", maxsplit=1)[-1]
    for pattern in patterns:
        normalized = pattern.replace("\\", "/").strip().lstrip("./")
        if not normalized:
            continue
        if _match_glob(relative, normalized) or _match_glob(name, normalized):
            return True
    return False


def iter_source_files(
    root: Path,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    warnings: list[str] | None = None,
) -> Iterator[Path]:
    """Yield supported files under root, honoring ignore dirs and globs.

    Does not apply the 10 MiB size cap; the scanner warns and skips those.
    Never imports or executes scanned files. Guards symlink loops via resolved paths.
    """
    include = include or []
    exclude = exclude or []
    root = root.resolve()
    seen: set[Path] = set()
    yield from _walk(root, root, include, exclude, seen, warnings)


def _rel_or_name(path: Path, root: Path) -> str:
    """POSIX path relative to scan root, falling back to the name."""
    try:
        return path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return path.name


def _walk(
    root: Path,
    current: Path,
    include: list[str],
    exclude: list[str],
    seen: set[Path],
    warnings: list[str] | None,
) -> Iterator[Path]:
    try:
        resolved = current.resolve()
    except OSError as exc:
        if warnings is not None:
            warnings.append(f"Warning: unable to read {_rel_or_name(current, root)}: {exc}")
        return
    if resolved in seen:
        return
    seen.add(resolved)

    try:
        entries = sorted(current.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        if warnings is not None:
            warnings.append(f"Warning: unable to read {_rel_or_name(current, root)}: {exc}")
        return

    for entry in entries:
        if entry.is_dir() and not entry.is_symlink():
            if is_ignored_dir(entry.name):
                continue
            yield from _walk(root, entry, include, exclude, seen, warnings)
            continue
        if entry.is_symlink():
            try:
                target = entry.resolve()
            except OSError as exc:
                if warnings is not None:
                    warnings.append(
                        f"Warning: unable to read {_rel_or_name(entry, root)}: {exc}"
                    )
                continue
            if target.is_dir():
                if is_ignored_dir(entry.name):
                    continue
                yield from _walk(root, entry, include, exclude, seen, warnings)
                continue
        if not entry.is_file():
            continue
        if entry.name in CONFIG_FILENAMES:
            continue
        if entry.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            relative = entry.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if exclude and matches_globs(relative, exclude):
            continue
        if include and not matches_globs(relative, include):
            continue
        yield entry
