"""LLM, embedding, and vector-store constructor tests. No vendor runtime."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from daygent.config import default_config
from daygent.parsers import AIParser, default_registry
from daygent.parsers.base import ParseContext
from daygent.scanner import Scanner

AI_SRC = Path(__file__).resolve().parents[1] / "src" / "daygent" / "parsers"
LLM_FAMILIES = (
    ("ChatOpenAI", "openai"),
    ("OpenAI", "openai"),
    ("ChatAnthropic", "anthropic"),
    ("Anthropic", "anthropic"),
    ("AzureOpenAI", "azure"),
)
VECTOR_FAMILIES = (
    ("PineconeVectorStore", "pinecone", "index_name", "company_index"),
    ("QdrantVectorStore", "qdrant", "collection_name", "company_docs"),
    ("WeaviateVectorStore", "weaviate", "index_name", "CompanyDocs"),
    ("Chroma", "chroma", "collection_name", "company_docs"),
    ("FAISS", "faiss", None, None),
    ("PGVector", "pgvector", "collection_name", "company_docs"),
)


def _parse(tmp_path: Path, source: str, name: str = "ai_app.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    context = ParseContext(root=tmp_path, config=default_config())
    return AIParser().parse(path, context)


@pytest.mark.parametrize(("cls", "provider"), LLM_FAMILIES)
def test_every_llm_family_literal_model(tmp_path: Path, cls: str, provider: str) -> None:
    result = _parse(tmp_path, f'{cls}(model="gpt-5")\n')
    node = next(item for item in result.nodes if item.type == "llm")
    assert node.metadata["provider"] == provider
    assert node.metadata["model"] == "gpt-5"
    assert node.id == f"llm:{provider}:gpt-5"
    assert node.metadata.get("dynamic") is not True


def test_literal_model_metadata(tmp_path: Path) -> None:
    result = _parse(tmp_path, 'ChatOpenAI(model="gpt-5")\n')
    node = next(item for item in result.nodes if item.id == "llm:openai:gpt-5")
    assert node.metadata == {
        "provider": "openai",
        "constructor": "ChatOpenAI",
        "model": "gpt-5",
    }


def test_dynamic_env_model_is_not_resolved(tmp_path: Path) -> None:
    result = _parse(tmp_path, 'ChatOpenAI(model=os.getenv("MODEL"))\n')
    node = next(item for item in result.nodes if item.type == "llm")
    assert node.metadata["provider"] == "openai"
    assert node.metadata.get("dynamic") is True
    assert "os.getenv" in str(node.metadata.get("model_expr"))
    assert node.metadata.get("model") != "gpt-5"
    assert "MODEL" not in node.id
    assert node.id == "llm:openai:dynamic"


def test_embedding_constructors(tmp_path: Path) -> None:
    source = """
OpenAIEmbeddings(model="text-embedding-3-small")
SentenceTransformer("all-MiniLM-L6-v2")
HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")
"""
    result = _parse(tmp_path, source)
    ids = {node.id for node in result.nodes}
    assert "embedding_model:text-embedding-3-small" in ids
    assert "embedding_model:all-MiniLM-L6-v2" in ids
    assert "embedding_model:sentence-transformers/all-mpnet-base-v2" in ids
    openai = next(n for n in result.nodes if n.id == "embedding_model:text-embedding-3-small")
    assert openai.metadata["provider"] == "openai"
    assert openai.type == "embedding_model"


@pytest.mark.parametrize(("cls", "vendor", "kw", "collection"), VECTOR_FAMILIES)
def test_each_vector_store_family(
    tmp_path: Path, cls: str, vendor: str, kw: str | None, collection: str | None
) -> None:
    if kw and collection:
        source = f'{cls}({kw}="{collection}")\n'
    else:
        source = f"{cls}.from_documents(docs, embeddings)\n"
    result = _parse(tmp_path, source)
    store_id = f"vector_store:{vendor}"
    assert store_id in {node.id for node in result.nodes}
    store = next(node for node in result.nodes if node.id == store_id)
    assert store.metadata["vendor"] == vendor
    if collection:
        coll_id = f"vector_collection:{collection}"
        assert coll_id in {node.id for node in result.nodes}


def test_literal_collection_store_to_collection_direction(tmp_path: Path) -> None:
    result = _parse(tmp_path, 'QdrantVectorStore(collection_name="company_docs")\n')
    ids = {node.id for node in result.nodes}
    assert "vector_store:qdrant" in ids
    assert "vector_collection:company_docs" in ids
    edge = next(
        item
        for item in result.edges
        if item.source == "vector_store:qdrant"
        and item.target == "vector_collection:company_docs"
    )
    assert edge.confidence == "high"
    assert edge.type == "feeds"


def test_dynamic_collection_is_not_invented(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        'QdrantVectorStore(collection_name=os.getenv("COLLECTION"))\n',
    )
    ids = {node.id for node in result.nodes}
    assert "vector_store:qdrant" in ids
    assert not any(node.type == "vector_collection" for node in result.nodes)
    store = next(node for node in result.nodes if node.id == "vector_store:qdrant")
    assert store.metadata.get("collection_dynamic") is True
    assert "os.getenv" in str(store.metadata.get("collection_expr"))


def test_os_getenv_is_never_executed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("os.getenv executed")

    monkeypatch.setattr(os, "getenv", boom)
    monkeypatch.setenv("MODEL", "should-not-be-read")
    result = _parse(
        tmp_path,
        'ChatOpenAI(model=os.getenv("MODEL"))\nQdrantVectorStore(collection_name=os.getenv("C"))\n',
    )
    assert any(node.type == "llm" for node in result.nodes)
    llm = next(node for node in result.nodes if node.type == "llm")
    assert llm.metadata.get("model") != "should-not-be-read"


def test_ai_parser_does_not_import_vendors() -> None:
    forbidden = {
        "openai",
        "langchain",
        "langchain_openai",
        "langchain_anthropic",
        "pinecone",
        "qdrant",
        "qdrant_client",
        "weaviate",
        "chromadb",
        "faiss",
        "sentence_transformers",
    }
    for path in (AI_SRC / "ai_parser.py", AI_SRC / "ai_constructors.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", maxsplit=1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".", maxsplit=1)[0]]
            for name in names:
                assert name not in forbidden, f"{path.name} imports {name}"


def test_constructor_coexists_with_langgraph(tmp_path: Path) -> None:
    source = """
graph.add_node("retrieve", retrieve)
ChatOpenAI(model="gpt-5")
"""
    result = _parse(tmp_path, source)
    ids = {node.id for node in result.nodes}
    assert "langgraph_node:retrieve" in ids
    assert "llm:openai:gpt-5" in ids
    report = Scanner(default_registry()).scan(tmp_path)
    graph_ids = {node.id for node in report.graph.nodes}
    assert "langgraph_node:retrieve" in graph_ids
    assert "llm:openai:gpt-5" in graph_ids
