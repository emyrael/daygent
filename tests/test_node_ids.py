"""Stable typed node-ID helpers."""

from __future__ import annotations

import pytest

from daygent.models import (
    NodeType,
    make_api_route_id,
    make_dbt_model_id,
    make_dbt_source_id,
    make_embedding_model_id,
    make_external_system_id,
    make_langgraph_node_id,
    make_llm_id,
    make_node_id,
    make_python_function_id,
    make_python_module_id,
    make_sql_table_id,
    make_vector_collection_id,
    make_vector_store_id,
    normalize_sql_table_name,
)


def test_required_typed_ids() -> None:
    assert make_dbt_model_id("stg_users") == "dbt_model:stg_users"
    assert make_api_route_id("POST", "/recommend") == "api_route:POST:/recommend"
    assert make_sql_table_id("raw.users") == "sql_table:raw.users"
    assert (
        make_python_function_id("services.recommend", "recommend")
        == "python_function:services.recommend.recommend"
    )
    assert make_external_system_id("api.stripe.com") == "external_system:api.stripe.com"


def test_api_route_method_is_case_insensitive() -> None:
    expected = "api_route:POST:/recommend"
    assert make_api_route_id("post", "/recommend") == expected
    assert make_api_route_id("POST", "/recommend") == expected
    assert make_api_route_id("Post", "/recommend") == expected


def test_api_route_path_params_and_leading_slash() -> None:
    assert make_api_route_id("get", "/users/{id}") == "api_route:GET:/users/{id}"
    assert make_api_route_id("GET", "users/{id}") == "api_route:GET:/users/{id}"


def test_sql_table_normalization_variants() -> None:
    expected = "raw.users"
    assert normalize_sql_table_name("RAW.Users") == expected
    assert normalize_sql_table_name("raw.users") == expected
    assert normalize_sql_table_name(" raw.users ") == expected
    assert make_sql_table_id("RAW.Users") == make_sql_table_id(" raw.users ")
    assert make_sql_table_id('"RAW"."Users"') == "sql_table:raw.users"


def test_sql_normalization_does_not_casefold_python_or_dbt() -> None:
    assert make_dbt_model_id("Stg_Users") == "dbt_model:Stg_Users"
    assert make_python_module_id("Services.Recommend") == "python_module:Services.Recommend"


def test_additional_stable_ids() -> None:
    assert make_python_module_id("services.recommend") == "python_module:services.recommend"
    assert make_dbt_source_id("raw", "users") == "dbt_source:raw.users"
    assert make_langgraph_node_id("retrieve") == "langgraph_node:retrieve"
    assert make_llm_id("gpt-5") == "llm:gpt-5"
    assert make_embedding_model_id("text-embedding-3-small") == (
        "embedding_model:text-embedding-3-small"
    )
    assert make_vector_store_id("Qdrant") == "vector_store:qdrant"
    assert make_vector_collection_id("company_docs") == "vector_collection:company_docs"


def test_make_node_id_generic_and_extensible() -> None:
    assert make_node_id(NodeType.DBT_MODEL, "stg_users") == "dbt_model:stg_users"
    assert make_node_id("airflow_dag", "nightly") == "airflow_dag:nightly"


def test_make_node_id_rejects_blank_parts() -> None:
    with pytest.raises(ValueError):
        make_node_id(NodeType.SQL_TABLE, "  ")


def test_ids_are_deterministic() -> None:
    assert make_api_route_id("post", "/recommend") == make_api_route_id("POST", "/recommend")
    assert make_sql_table_id("RAW.Users") == make_sql_table_id("raw.users")
