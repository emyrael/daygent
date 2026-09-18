"""Canonical node/edge types and confidence. Traversal uses source/target, not these values.

Graph convention (locked): A → B means B depends on / consumes / is affected by A.
Impact walks with the arrows. Upstream lineage walks against the arrows.

`Node.type` and `Edge.type` are strings so parsers can add kinds (airflow_dag,
spark_job, terraform_resource, kafka_topic, databricks_job, …) without changing
graph algorithms. Prefer these constants over ad-hoc literals.
"""

from __future__ import annotations

from enum import StrEnum


class NodeType(StrEnum):
    """v0.1 node kinds. Other lowercase snake_case strings are allowed."""

    PYTHON_MODULE = "python_module"
    PYTHON_FUNCTION = "python_function"
    SQL_TABLE = "sql_table"
    DBT_MODEL = "dbt_model"
    DBT_SOURCE = "dbt_source"
    API_ROUTE = "api_route"
    API_CLIENT = "api_client"
    SERVICE = "service"
    LANGGRAPH_NODE = "langgraph_node"
    AGENT = "agent"
    LLM = "llm"
    EMBEDDING_MODEL = "embedding_model"
    VECTOR_STORE = "vector_store"
    VECTOR_COLLECTION = "vector_collection"
    EXTERNAL_SYSTEM = "external_system"


class EdgeType(StrEnum):
    """v0.1 relationship kinds. Stored direction is always upstream → consumer."""

    IMPORTS = "imports"
    IMPORTED_BY = "imported_by"
    CALLS = "calls"
    INVOKES = "invokes"
    INVOKED_BY = "invoked_by"
    READS_FROM = "reads_from"
    READ_BY = "read_by"
    WRITES_TO = "writes_to"
    QUERIES = "queries"
    DEPENDS_ON = "depends_on"
    FEEDS = "feeds"
    REF = "ref"
    SOURCE = "source"
    EXPOSES = "exposes"
    EMBEDS_WITH = "embeds_with"
    RETRIEVES_FROM = "retrieves_from"
    ROUTES_TO = "routes_to"


class Confidence(StrEnum):
    """Static-analysis confidence. Rank is high > medium > low, not alphabetical."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


CONFIDENCE_RANK: dict[Confidence, int] = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
}


def max_confidence(left: Confidence, right: Confidence) -> Confidence:
    """Return the stronger of two confidence values (high > medium > low)."""
    return left if CONFIDENCE_RANK[left] >= CONFIDENCE_RANK[right] else right
