"""Unrelated helpers that must not appear in the scoped Daygent graph."""


def format_date(value: str) -> str:
    """Normalize a date label. Not part of data/AI lineage."""
    return value.strip()


def unrelated_helper() -> int:
    """Dead utility. Not part of data/AI lineage."""
    return 1
