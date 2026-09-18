"""Literal HTTP host detection tests. No network calls."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from daygent.config import default_config
from daygent.parsers import HttpParser, PythonParser, default_registry
from daygent.parsers.base import ParseContext, ParserRegistry
from daygent.parsers.http_parser import hostname_from_url
from daygent.scanner import Scanner

HTTP_SRC = Path(__file__).resolve().parents[1] / "src" / "daygent" / "parsers" / "http_parser.py"

SOURCE = '''
import requests
import httpx

def fetch_customers():
    requests.get("https://api.stripe.com/v1/customers")

def create_customer():
    requests.post("https://api.stripe.com/v1/customers")

def ping_example():
    httpx.get("https://example.com/api")

def post_example():
    httpx.post("https://example.com/api")

def client_get(client):
    client.get("https://api.foo.com/data")

def client_post(client):
    client.post("https://api.foo.com/data")

def dynamic_url(url):
    requests.get(url)
    requests.get(os.getenv("URL"))

def relative_only():
    requests.get("/users")
    client.get("/users")
    httpx.post("users")
'''


def _parse(tmp_path: Path, source: str = SOURCE, name: str = "client.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    context = ParseContext(root=tmp_path, config=default_config())
    return HttpParser().parse(path, context)


def test_requests_get_and_post(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    ids = {node.id for node in result.nodes}
    assert "external_system:api.stripe.com" in ids


def test_httpx_get_and_post(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    ids = {node.id for node in result.nodes}
    assert "external_system:example.com" in ids


def test_generic_client_get_and_post(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    ids = {node.id for node in result.nodes}
    assert "external_system:api.foo.com" in ids


def test_literal_url_hostname_extraction() -> None:
    assert hostname_from_url("https://api.stripe.com/v1/customers") == "api.stripe.com"
    assert hostname_from_url("http://Example.COM:8080/api") == "example.com"
    assert hostname_from_url("https://user:pass@api.stripe.com/v1") == "api.stripe.com"
    assert hostname_from_url("/users") is None
    assert hostname_from_url("users") is None
    assert hostname_from_url("ftp://files.example.com/x") is None


def test_dynamic_url_omitted(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    assert not any("getenv" in node.id for node in result.nodes)
    assert not any(node.id.endswith(":url") for node in result.nodes)


def test_relative_url_omitted(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    ids = {node.id for node in result.nodes if node.type == "external_system"}
    assert "external_system:users" not in ids
    assert not any("/users" in node.id for node in result.nodes)


def test_function_to_external_system_direction(tmp_path: Path) -> None:
    result = _parse(tmp_path)
    edge = next(
        item
        for item in result.edges
        if item.target == "external_system:api.stripe.com"
        and item.source.endswith(".fetch_customers")
    )
    assert edge.source == "python_function:client.fetch_customers"
    assert edge.confidence == "medium"
    assert edge.type == "depends_on"


def test_scanner_keeps_function_to_host(tmp_path: Path) -> None:
    (tmp_path / "client.py").write_text(SOURCE, encoding="utf-8")
    report = Scanner(default_registry()).scan(tmp_path)
    assert any(
        edge.source == "python_function:client.fetch_customers"
        and edge.target == "external_system:api.stripe.com"
        and edge.confidence == "medium"
        for edge in report.graph.edges
    )


def test_no_network_call_occurs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network call")

    monkeypatch.setattr("socket.create_connection", boom)
    monkeypatch.setattr("socket.socket", boom)
    monkeypatch.setattr("urllib.request.urlopen", boom)
    result = _parse(tmp_path)
    assert "external_system:api.stripe.com" in {node.id for node in result.nodes}


def test_http_parser_source_has_no_network_clients() -> None:
    text = HTTP_SRC.read_text(encoding="utf-8")
    assert "urlopen" not in text
    assert "urlretrieve" not in text
    tree = ast.parse(text)
    forbidden = {"requests", "httpx", "aiohttp", "urllib.request"}
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert name not in forbidden
            assert not name.startswith("urllib.request")


def test_coexists_with_python_parser(tmp_path: Path) -> None:
    (tmp_path / "client.py").write_text(SOURCE, encoding="utf-8")
    context = ParseContext(root=tmp_path, config=default_config())
    registry = ParserRegistry([PythonParser(), HttpParser()])
    names = [parser.name for parser in registry.matching(tmp_path / "client.py", context)]
    assert names == ["python", "http"]
    report = Scanner(default_registry()).scan(tmp_path)
    ids = {node.id for node in report.graph.nodes}
    assert "python_function:client.fetch_customers" in ids
    assert "external_system:api.stripe.com" in ids


def test_password_in_url_is_not_stored(tmp_path: Path) -> None:
    result = _parse(
        tmp_path,
        'def leak():\n    requests.get("https://user:s3cret@api.stripe.com/v1")\n',
        name="secret.py",
    )
    host = next(node for node in result.nodes if node.type == "external_system")
    assert host.id == "external_system:api.stripe.com"
    assert "s3cret" not in str(host.metadata)
    assert host.metadata["hostname"] == "api.stripe.com"
