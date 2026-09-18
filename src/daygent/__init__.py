"""Daygent: static lineage and blast-radius analysis for data + AI repos."""

from __future__ import annotations

__version__ = "0.2.0"

GRAPH_SCHEMA_VERSION = "0.1"

# A → B means B depends on / consumes / is affected by A.
GRAPH_CONVENTION = (
    "upstream_to_downstream_consumer"
)
