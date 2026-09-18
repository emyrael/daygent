"""Unrelated helpers that must not appear in the scoped Daygent graph."""


def slugify(text: str) -> str:
    """Normalize a label. Not part of data/AI lineage."""
    return text.lower().strip()


def unused_helper() -> int:
    """Dead utility. Not part of data/AI lineage."""
    return 1
