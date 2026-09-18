"""Scanner, config, ignore rules, and parser isolation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from daygent.config import load_config
from daygent.exceptions import DaygentConfigError
from daygent.models import Confidence, Edge, Node, NodeType
from daygent.parsers.base import BaseParser, ParseContext, ParserRegistry, ParseResult
from daygent.scanner import Scanner
from daygent.utils.files import (
    IGNORE_DIR_NAMES,
    MAX_FILE_BYTES,
    matches_globs,
)

SCANNER_SRC = Path(__file__).resolve().parents[1] / "src" / "daygent"


class RecordingParser(BaseParser):
    """Test parser that records paths and emits one node per file."""

    name = "recording"

    def __init__(self) -> None:
        self.parsed: list[Path] = []

    def supports(self, path: Path, context: ParseContext) -> bool:
        return path.suffix in {".py", ".sql"}

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        self.parsed.append(path)
        rel = path.name
        return ParseResult(
            nodes=[
                Node(
                    id=f"python_module:{rel}",
                    name=rel,
                    type=NodeType.PYTHON_MODULE,
                    file_path=rel,
                )
            ]
        )


class PyOnlyParser(BaseParser):
    """Parser that only claims .py files."""

    name = "py-only"

    def __init__(self) -> None:
        self.parsed: list[Path] = []

    def supports(self, path: Path, context: ParseContext) -> bool:
        return path.suffix == ".py"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        self.parsed.append(path)
        return ParseResult(
            nodes=[
                Node(id=f"python_module:{path.name}", name=path.name, type="python_module")
            ]
        )


class SqlOnlyParser(BaseParser):
    """Parser that only claims .sql files."""

    name = "sql-only"

    def __init__(self) -> None:
        self.parsed: list[Path] = []

    def supports(self, path: Path, context: ParseContext) -> bool:
        return path.suffix == ".sql"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        self.parsed.append(path)
        return ParseResult(
            nodes=[Node(id=f"sql_table:{path.stem}", name=path.stem, type="sql_table")]
        )


class FirstWinsParser(BaseParser):
    """Always supports; used to prove registry order."""

    name = "first"

    def __init__(self) -> None:
        self.parsed: list[Path] = []

    def supports(self, path: Path, context: ParseContext) -> bool:
        return path.suffix == ".py"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        self.parsed.append(path)
        return ParseResult(
            nodes=[Node(id="python_module:first", name="first", type="python_module")]
        )


class SecondParser(BaseParser):
    """Would also support .py if the first parser did not win."""

    name = "second"

    def __init__(self) -> None:
        self.parsed: list[Path] = []

    def supports(self, path: Path, context: ParseContext) -> bool:
        return path.suffix == ".py"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        self.parsed.append(path)
        return ParseResult(
            nodes=[Node(id="python_module:second", name="second", type="python_module")]
        )


class BoomParser(BaseParser):
    """Parser that fails on boom.py and succeeds on ok.py."""

    name = "boom"

    def supports(self, path: Path, context: ParseContext) -> bool:
        return path.suffix == ".py"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        if path.name == "boom.py":
            raise RuntimeError("parse exploded")
        return ParseResult(
            nodes=[
                Node(
                    id="python_module:ok.py",
                    name="ok.py",
                    type=NodeType.PYTHON_MODULE,
                    file_path="ok.py",
                )
            ],
            edges=[
                Edge(
                    source="python_module:ok.py",
                    target="python_module:ok.py",
                    type="depends_on",
                    confidence=Confidence.LOW,
                )
            ],
        )


class ReadingParser(BaseParser):
    """Parser that reads file bytes so unreadable files surface as parse errors."""

    name = "reading"

    def supports(self, path: Path, context: ParseContext) -> bool:
        return path.suffix == ".py"

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        path.read_text(encoding="utf-8")
        return ParseResult(
            nodes=[Node(id=f"python_module:{path.name}", name=path.name, type="python_module")]
        )


def test_ignore_builtin_dirs_and_large_files(tmp_path: Path) -> None:
    for name in sorted(IGNORE_DIR_NAMES):
        hidden = tmp_path / name / "lib"
        hidden.mkdir(parents=True)
        (hidden / "hidden.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    huge = tmp_path / "huge.py"
    huge.write_bytes(b"x" * (MAX_FILE_BYTES + 1))

    parser = RecordingParser()
    report = Scanner(ParserRegistry([parser])).scan(tmp_path)
    parsed_names = {path.name for path in parser.parsed}
    assert parsed_names == {"keep.py"}
    assert any("larger than 10 MiB" in warning for warning in report.graph.scan.warnings)
    assert any(node.id == "python_module:keep.py" for node in report.graph.nodes)


def test_parser_exception_does_not_abort_scan(tmp_path: Path) -> None:
    (tmp_path / "boom.py").write_text("broken", encoding="utf-8")
    (tmp_path / "ok.py").write_text("ok", encoding="utf-8")
    report = Scanner(ParserRegistry([BoomParser()])).scan(tmp_path)
    assert any("unable to parse boom.py" in warning for warning in report.graph.scan.warnings)
    assert any(node.id == "python_module:ok.py" for node in report.graph.nodes)


def test_exclude_glob_from_config(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("x", encoding="utf-8")
    (tmp_path / "skip.py").write_text("x", encoding="utf-8")
    (tmp_path / "daygent.yml").write_text(
        "exclude:\n  - skip.py\noutput: custom/graph.json\n",
        encoding="utf-8",
    )
    parser = RecordingParser()
    report = Scanner(ParserRegistry([parser])).scan(tmp_path)
    assert {path.name for path in parser.parsed} == {"keep.py"}
    assert report.output_path == tmp_path / "custom" / "graph.json"


def test_include_and_exclude_globs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "models").mkdir()
    (tmp_path / "migrations").mkdir()
    (tmp_path / "generated").mkdir()
    (tmp_path / "src" / "app.py").write_text("x", encoding="utf-8")
    (tmp_path / "models" / "orders.sql").write_text("select 1", encoding="utf-8")
    (tmp_path / "migrations" / "001.sql").write_text("select 1", encoding="utf-8")
    (tmp_path / "generated" / "gen.py").write_text("x", encoding="utf-8")
    (tmp_path / "root.py").write_text("x", encoding="utf-8")
    (tmp_path / "daygent.yml").write_text(
        "include:\n  - src/**\n  - models/**\n"
        "exclude:\n  - migrations/**\n  - generated/**\n",
        encoding="utf-8",
    )
    parser = RecordingParser()
    report = Scanner(ParserRegistry([parser])).scan(tmp_path)
    parsed = {path.name for path in parser.parsed}
    assert parsed == {"app.py", "orders.sql"}
    assert report.files_considered == 2


def test_invalid_yaml_fails_scan(tmp_path: Path) -> None:
    (tmp_path / "daygent.yml").write_text("exclude: [\n", encoding="utf-8")
    with pytest.raises(DaygentConfigError, match="Invalid YAML"):
        Scanner().scan(tmp_path)


def test_invalid_config_schema_fails_scan(tmp_path: Path) -> None:
    (tmp_path / "daygent.yml").write_text("include: src\n", encoding="utf-8")
    with pytest.raises(DaygentConfigError, match="Invalid config"):
        Scanner().scan(tmp_path)


def test_unknown_config_keys_warn(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("x", encoding="utf-8")
    (tmp_path / "daygent.yml").write_text("exclude: []\nexperimental: true\n", encoding="utf-8")
    report = Scanner(ParserRegistry([RecordingParser()])).scan(tmp_path)
    assert any("Unknown config keys" in warning for warning in report.graph.scan.warnings)


def test_load_config_defaults_when_missing(tmp_path: Path) -> None:
    config, warnings = load_config(tmp_path)
    assert config.output.endswith("graph.json")
    assert config.include == []
    assert config.exclude == []
    assert warnings == []


def test_daygent_yaml_is_discovered(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("x", encoding="utf-8")
    (tmp_path / "daygent.yaml").write_text("output: artifacts/graph.json\n", encoding="utf-8")
    report = Scanner(ParserRegistry([RecordingParser()])).scan(tmp_path)
    assert report.output_path == tmp_path / "artifacts" / "graph.json"


def test_parser_dispatch_by_extension(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x", encoding="utf-8")
    (tmp_path / "orders.sql").write_text("select 1", encoding="utf-8")
    py_parser = PyOnlyParser()
    sql_parser = SqlOnlyParser()
    report = Scanner(ParserRegistry([py_parser, sql_parser])).scan(tmp_path)
    assert [path.name for path in py_parser.parsed] == ["app.py"]
    assert [path.name for path in sql_parser.parsed] == ["orders.sql"]
    ids = {node.id for node in report.graph.nodes}
    assert ids == {"python_module:app.py", "sql_table:orders"}


def test_first_supporting_parser_wins(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x", encoding="utf-8")
    first = FirstWinsParser()
    second = SecondParser()
    report = Scanner(ParserRegistry([first, second])).scan(tmp_path)
    assert first.parsed
    assert second.parsed == []
    assert [node.id for node in report.graph.nodes] == ["python_module:first"]


def test_unreadable_file_warns_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locked = tmp_path / "locked.py"
    locked.write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("ok", encoding="utf-8")

    real_open = Path.open

    def boom_open(self: Path, *args: object, **kwargs: object):
        if self.name == "locked.py":
            raise PermissionError("denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", boom_open)
    parser = ReadingParser()
    report = Scanner(ParserRegistry([parser])).scan(tmp_path)
    assert any("unable to read locked.py" in warning for warning in report.graph.scan.warnings)
    assert any(node.id == "python_module:ok.py" for node in report.graph.nodes)


def test_symlink_loop_does_not_hang(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (nested / "loop").symlink_to(tmp_path)
    parser = RecordingParser()
    report = Scanner(ParserRegistry([parser])).scan(tmp_path)
    assert {path.name for path in parser.parsed} == {"ok.py"}
    assert report.files_considered == 1


def test_scanner_does_not_import_scanned_code(tmp_path: Path) -> None:
    marker = tmp_path / "imported.flag"
    payload = tmp_path / "payload.py"
    payload.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n",
        encoding="utf-8",
    )
    Scanner(ParserRegistry([RecordingParser()])).scan(tmp_path)
    assert not marker.exists()


def test_scanner_modules_have_no_language_parser_logic() -> None:
    forbidden = ("sqlglot", "ast.parse", "ast.dump", "exec(", "eval(")
    for relative in ("scanner.py", "config.py", "utils/files.py", "parsers/base.py"):
        text = (SCANNER_SRC / relative).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{relative} contains {token}"


def test_matches_globs_double_star() -> None:
    assert matches_globs("src/app.py", ["src/**"])
    assert matches_globs("models/orders.sql", ["models/**"])
    assert not matches_globs("root.py", ["src/**", "models/**"])
    assert matches_globs("migrations/001.sql", ["migrations/**"])
    assert matches_globs("nested/skip.py", ["skip.py"])
