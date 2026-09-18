"""FastAPI AST parser tests. No FastAPI runtime imports of scanned apps."""

from __future__ import annotations

from pathlib import Path

from daygent.config import default_config
from daygent.parsers import FastAPIParser, default_registry
from daygent.parsers.base import ParseContext
from daygent.scanner import Scanner

SOURCE = '''
from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter()
api = APIRouter(prefix="/v1")

@app.get("/health")
def health():
    return "ok"

@app.post("/recommend")
def recommend():
    return {"ok": True}

@app.put("/items/{item_id}")
def replace_item(item_id: str):
    return item_id

@app.patch("/items/{item_id}")
def patch_item(item_id: str):
    return item_id

@app.delete("/items/{item_id}")
def delete_item(item_id: str):
    return item_id

@router.get("/items")
def list_items():
    return []

@api.get("/users")
def list_users():
    return []

path = "/dynamic"
@app.get(path)
def dynamic_route():
    return None

app.include_router(router, prefix="/api")
'''


def _parse(tmp_path: Path, source: str = SOURCE, name: str = "main.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    context = ParseContext(root=tmp_path, config=default_config())
    return FastAPIParser().parse(path, context)


def test_all_http_methods_and_path_parameters(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    ids = {node.id for node in result.nodes}
    assert "api_route:GET:/health" in ids
    assert "api_route:POST:/recommend" in ids
    assert "api_route:PUT:/items/{item_id}" in ids
    assert "api_route:PATCH:/items/{item_id}" in ids
    assert "api_route:DELETE:/items/{item_id}" in ids
    recommend = next(node for node in result.nodes if node.id == "api_route:POST:/recommend")
    assert recommend.metadata["method"] == "POST"
    assert recommend.metadata["path"] == "/recommend"
    assert recommend.name == "POST /recommend"


def test_handler_invoked_by_route(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    edge = next(
        item
        for item in result.edges
        if item.target == "api_route:POST:/recommend"
    )
    assert edge.source == "python_function:main.recommend"
    assert edge.type == "invoked_by"
    assert edge.confidence == "high"


def test_router_prefix_and_include_router(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    ids = {node.id for node in result.nodes}
    assert "api_route:GET:/items" in ids
    assert "api_route:GET:/api/items" in ids
    assert "api_route:GET:/v1/users" in ids


def test_dynamic_paths_are_omitted(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    ids = {node.id for node in result.nodes}
    assert "api_route:GET:/dynamic" not in ids
    assert not any("/dynamic" in node.id for node in result.nodes)


def test_app_vs_router_owners(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    health = next(node for node in result.nodes if node.id == "api_route:GET:/health")
    items = next(node for node in result.nodes if node.id == "api_route:GET:/items")
    assert health.metadata["owner"] == "app"
    assert items.metadata["owner"] == "router"


def test_scanner_builds_recommend_to_route(tmp_path: Path) -> None:
    source = (
        "from fastapi import FastAPI\n"
        "from langchain_openai import ChatOpenAI\n"
        "app = FastAPI()\n\n"
        "@app.post(\"/recommend\")\n"
        "def recommend():\n"
        "    ChatOpenAI(model=\"gpt-5\")\n"
        "    return {\"ok\": True}\n"
    )
    (tmp_path / "main.py").write_text(source, encoding="utf-8")
    report = Scanner(default_registry()).scan(tmp_path)
    assert any(
        edge.source == "python_function:main.recommend"
        and edge.target == "api_route:POST:/recommend"
        and edge.type == "invoked_by"
        for edge in report.graph.edges
    )
    ids = {node.id for node in report.graph.nodes}
    assert "llm:openai:gpt-5" in ids
    assert "api_route:POST:/recommend" in ids
