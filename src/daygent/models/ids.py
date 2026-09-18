"""Stable typed node IDs. Never use UUIDs for static graph elements.

Normalization is type-aware:
- SQL tables: strip whitespace/quotes, lowercase (merge-by-name).
- HTTP methods: uppercase. Paths stay recognizable; a missing leading slash is added.
- External hosts: lowercase (DNS is case-insensitive).
- dbt / Python / LangGraph names: stripped only — do not case-fold.
"""

from __future__ import annotations

from pathlib import Path

from daygent.models.types import NodeType


def make_node_id(node_type: NodeType | str, *parts: str) -> str:
    """Build `{type}:{part:part:…}` from a type and one or more identity parts."""
    type_value = str(node_type).strip()
    if not type_value:
        raise ValueError("node_type must be a non-empty string")
    cleaned = [part.strip() for part in parts if part is not None and str(part).strip()]
    if not cleaned:
        raise ValueError("node id requires at least one identity part")
    return f"{type_value}:{':'.join(cleaned)}"


def posix_relpath(path: Path, root: Path) -> str:
    """Return a POSIX relative path for file-scoped metadata (not SQL identity)."""
    resolved = path if path.is_absolute() else root / path
    try:
        relative = resolved.resolve().relative_to(root.resolve())
    except ValueError:
        relative = Path(path.name)
    return relative.as_posix()


def normalize_sql_table_name(name: str) -> str:
    """Canonical SQL relation name for merge-by-name identity.

    v0.1 rule: trim whitespace, strip matching quote characters from each
    dotted part, lowercase. Does not attempt dialect-accurate quoting.
    """
    stripped = name.strip()
    parts = [part.strip().strip("`'\"[]") for part in stripped.split(".")]
    parts = [part.lower() for part in parts if part]
    if not parts:
        raise ValueError("SQL table name is empty")
    return ".".join(parts)


def make_python_module_id(module: str) -> str:
    """Example: `python_module:services.recommend`."""
    return make_node_id(NodeType.PYTHON_MODULE, module)


def make_python_function_id(module: str, function: str) -> str:
    """Example: `python_function:services.recommend.recommend`."""
    qualified = f"{module.strip()}.{function.strip()}".strip(".")
    return make_node_id(NodeType.PYTHON_FUNCTION, qualified)


def make_sql_table_id(name: str) -> str:
    """Example: `sql_table:raw.users`."""
    return make_node_id(NodeType.SQL_TABLE, normalize_sql_table_name(name))


def make_dbt_model_id(name: str) -> str:
    """Example: `dbt_model:stg_users`."""
    return make_node_id(NodeType.DBT_MODEL, name.strip())


def make_dbt_source_id(source: str, table: str) -> str:
    """Example: `dbt_source:raw.users`."""
    return make_node_id(NodeType.DBT_SOURCE, f"{source.strip()}.{table.strip()}")


def make_api_route_id(method: str, path: str) -> str:
    """Example: `api_route:POST:/recommend`. Method is case-insensitive."""
    normalized_method = method.strip().upper()
    normalized_path = path.strip() or "/"
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    return make_node_id(NodeType.API_ROUTE, f"{normalized_method}:{normalized_path}")


def make_langgraph_node_id(name: str, scope: str | None = None) -> str:
    """Example: `langgraph_node:retrieve`."""
    if scope and scope.strip():
        return make_node_id(NodeType.LANGGRAPH_NODE, scope.strip(), name.strip())
    return make_node_id(NodeType.LANGGRAPH_NODE, name.strip())


def make_llm_id(model: str, provider: str | None = None) -> str:
    """Example: `llm:gpt-5`. Optional provider becomes `llm:openai:gpt-5`."""
    if provider and provider.strip():
        return make_node_id(NodeType.LLM, provider.strip(), model.strip())
    return make_node_id(NodeType.LLM, model.strip())


def make_embedding_model_id(name: str) -> str:
    """Example: `embedding_model:text-embedding-3-small`."""
    return make_node_id(NodeType.EMBEDDING_MODEL, name.strip())


def make_vector_store_id(vendor: str) -> str:
    """Example: `vector_store:qdrant`."""
    return make_node_id(NodeType.VECTOR_STORE, vendor.strip().lower())


def make_vector_collection_id(name: str, vendor: str | None = None) -> str:
    """Example: `vector_collection:company_docs`."""
    if vendor and vendor.strip():
        return make_node_id(NodeType.VECTOR_COLLECTION, vendor.strip().lower(), name.strip())
    return make_node_id(NodeType.VECTOR_COLLECTION, name.strip())


def make_pipeline_dataset_id(name: str) -> str:
    """Example: `pipeline_dataset:silver_customers`."""
    return make_node_id(NodeType.PIPELINE_DATASET, name.strip())


def make_django_model_id(qualified: str) -> str:
    """Example: `django_model:customers.Customer`."""
    return make_node_id(NodeType.DJANGO_MODEL, qualified.strip())


def make_sqlalchemy_model_id(qualified: str) -> str:
    """Example: `sqlalchemy_model:models.Customer`."""
    return make_node_id(NodeType.SQLALCHEMY_MODEL, qualified.strip())


def django_model_qualname(module: str, class_name: str) -> str:
    """Qualify a Django model, dropping a trailing `.models` module segment."""
    parts = [part for part in module.strip().split(".") if part]
    if parts and parts[-1] == "models":
        parts = parts[:-1]
    parts.append(class_name.strip())
    return ".".join(part for part in parts if part)


def make_external_system_id(hostname: str) -> str:
    """Example: `external_system:api.stripe.com`."""
    host = hostname.strip().lower()
    if "://" in host:
        host = host.split("://", maxsplit=1)[1]
    host = host.split("/", maxsplit=1)[0]
    if "@" in host:
        host = host.rsplit("@", maxsplit=1)[-1]
    if host.startswith("[") and "]" in host:
        host = host[1 : host.index("]")]
    elif host.count(":") == 1:
        host = host.rsplit(":", maxsplit=1)[0]
    return make_node_id(NodeType.EXTERNAL_SYSTEM, host)


# Short aliases used throughout the package.
python_module_id = make_python_module_id
python_function_id = make_python_function_id
sql_table_id = make_sql_table_id
dbt_model_id = make_dbt_model_id
dbt_source_id = make_dbt_source_id
api_route_id = make_api_route_id
langgraph_node_id = make_langgraph_node_id
llm_id = make_llm_id
embedding_model_id = make_embedding_model_id
vector_store_id = make_vector_store_id
vector_collection_id = make_vector_collection_id
pipeline_dataset_id = make_pipeline_dataset_id
django_model_id = make_django_model_id
sqlalchemy_model_id = make_sqlalchemy_model_id
external_system_id = make_external_system_id
