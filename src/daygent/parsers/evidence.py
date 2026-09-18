"""Static evidence fields for graph edges. Never stores source file bodies."""

from __future__ import annotations


def evidence_metadata(
    *,
    file_path: str | None = None,
    line_number: int | None = None,
    reference: str | None = None,
    operation: str | None = None,
    framework: str | None = None,
    parser: str | None = None,
    sql: str | None = None,
) -> dict[str, object]:
    """Return structured evidence fields that are already statically known."""
    meta: dict[str, object] = {}
    if file_path:
        meta["file_path"] = file_path
    if isinstance(line_number, int) and line_number > 0:
        meta["line_number"] = line_number
    if reference:
        meta["reference"] = reference
    if operation:
        meta["operation"] = operation
    if framework:
        meta["framework"] = framework
    if parser:
        meta["parser"] = parser
    if sql:
        meta["sql"] = sql
    return meta


def first_line_matching(source: str, needles: list[str]) -> int | None:
    """Return the first 1-based line that contains any needle, if known."""
    for index, line in enumerate(source.splitlines(), start=1):
        for needle in needles:
            if needle and needle in line:
                return index
    return None
