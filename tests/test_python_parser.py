"""Python AST parser tests. No FastAPI / LangGraph / HTTP detection."""

from __future__ import annotations

import ast
from pathlib import Path

from daygent.config import default_config
from daygent.parsers import PythonParser, default_registry
from daygent.parsers.base import ParseContext
from daygent.scanner import Scanner

PARSER_SRC = Path(__file__).resolve().parents[1] / "src" / "daygent" / "parsers" / "python_parser.py"


def _parse(tmp_path: Path, relative: str, source: str):
    """Write a file and parse it with PythonParser."""
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    context = ParseContext(root=tmp_path, config=default_config())
    return PythonParser().parse(path, context), path


def test_module_and_function_nodes_have_line_numbers(tmp_path: Path) -> None:
    result, _ = _parse(
        tmp_path,
        "services/recommend.py",
        "def helper():\n    return 1\n\ndef recommend():\n    return helper()\n",
    )
    modules = [node for node in result.nodes if node.type == "python_module"]
    functions = {node.name: node for node in result.nodes if node.type == "python_function"}
    local_module = next(node for node in modules if node.file_path == "services/recommend.py")
    assert local_module.id == "python_module:services.recommend"
    assert local_module.line_number == 1
    assert functions["helper"].id == "python_function:services.recommend.helper"
    assert functions["helper"].line_number == 1
    assert functions["recommend"].line_number == 4
    assert functions["recommend"].file_path == "services/recommend.py"


def test_import_edge_imported_module_is_upstream(tmp_path: Path) -> None:
    result, _ = _parse(
        tmp_path,
        "app.py",
        "import services.recommend\nfrom services.recommend import run\n",
    )
    edges = [edge for edge in result.edges if edge.type == "imported_by"]
    assert edges
    for edge in edges:
        assert edge.source == "python_module:services.recommend"
        assert edge.target == "python_module:app"
    assert any(node.id == "python_module:services.recommend" for node in result.nodes)


def test_module_file_and_import_share_canonical_id(tmp_path: Path) -> None:
    """services/recommend.py and `import services.recommend` use the same node id."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "recommend.py").write_text(
        "def recommend():\n    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("import services.recommend\n", encoding="utf-8")
    from daygent.models.ids import make_python_module_id
    from daygent.parsers.python_parser import module_name_from_path

    module_id = make_python_module_id(
        module_name_from_path(tmp_path / "services" / "recommend.py", tmp_path)
    )
    assert module_id == "python_module:services.recommend"
    report = Scanner(default_registry()).scan(tmp_path, scope=False)
    matches = [node for node in report.graph.nodes if node.id == module_id]
    assert len(matches) == 1
    assert matches[0].file_path == "services/recommend.py"
    assert any(
        edge.source == module_id and edge.target == "python_module:app"
        for edge in report.graph.edges
    )


def test_local_call_is_callee_to_caller_invoked_by(tmp_path: Path) -> None:
    result, _ = _parse(
        tmp_path,
        "mod.py",
        "def b():\n    pass\n\ndef a():\n    b()\n",
    )
    calls = [edge for edge in result.edges if edge.type == "invoked_by"]
    assert len(calls) == 1
    assert calls[0].source == "python_function:mod.b"
    assert calls[0].target == "python_function:mod.a"


def test_dynamic_calls_are_omitted(tmp_path: Path) -> None:
    result, _ = _parse(
        tmp_path,
        "dyn.py",
        "def a():\n    getattr(a, 'x')()\n    (lambda: None)()\n",
    )
    assert [edge for edge in result.edges if edge.type == "invoked_by"] == []


def test_syntax_error_returns_warning(tmp_path: Path) -> None:
    result, _ = _parse(tmp_path, "broken.py", "def oops(:\n")
    assert result.nodes == []
    assert result.edges == []
    assert any("syntax error" in warning.lower() for warning in result.warnings)


def test_scanner_integration_and_no_execution(tmp_path: Path) -> None:
    marker = tmp_path / "imported.flag"
    (tmp_path / "ok.py").write_text(
        "def b():\n    pass\n\ndef a():\n    b()\n",
        encoding="utf-8",
    )
    (tmp_path / "payload.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
        "raise SystemExit('should not run')\n",
        encoding="utf-8",
    )
    report = Scanner(default_registry()).scan(tmp_path)
    assert not marker.exists()
    ids = {node.id for node in report.graph.nodes}
    assert "python_module:ok" not in ids
    assert "python_function:ok.a" not in ids


def test_src_layout_module_names_drop_src_prefix(tmp_path: Path) -> None:
    result, _ = _parse(
        tmp_path,
        "src/daygent/cli.py",
        "from daygent.config import load_config\n\ndef main():\n    return load_config\n",
    )
    local = next(node for node in result.nodes if node.file_path == "src/daygent/cli.py")
    assert local.id == "python_module:daygent.cli"
    assert any(node.id == "python_function:daygent.cli.main" for node in result.nodes)


def test_unresolved_stdlib_imports_are_dropped_from_graph(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("import ast\nfrom pathlib import Path\n", encoding="utf-8")
    report = Scanner(default_registry()).scan(tmp_path, scope=False)
    ids = {node.id for node in report.graph.nodes}
    assert "python_module:app" in ids
    assert "python_module:ast" not in ids
    assert "python_module:pathlib" not in ids


def test_python_parser_does_not_execute_or_regex_parse() -> None:
    text = PARSER_SRC.read_text(encoding="utf-8")
    tree = ast.parse(text)
    forbidden = {"eval", "exec", "importlib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id not in forbidden
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"eval", "exec"}
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".", maxsplit=1)[0] != "importlib" for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".", maxsplit=1)[0] != "importlib"
    assert "re." not in text
    assert "import re" not in text
    assert "ast.parse" in text
