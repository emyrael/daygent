# Contributing to Daygent

Daygent is a local-first static lineage CLI. Keep parsers honest: read files, emit nodes and edges, never run the scanned project.

## Development setup

Requires Python 3.11+.

```bash
git clone https://github.com/emyrael/daygent.git
cd daygent
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
daygent --help
```

Do not publish to PyPI from this workflow. That is a separate release issue.

## Tests

```bash
pytest
pytest -v tests/test_golden_mixed_stack.py
```

The golden mixed-stack fixture (`tests/fixtures/golden_mixed_stack/`) is the v0.1 definition-of-done suite. Keep it tiny, deterministic, and secret-free.

Rules:

- Never import, exec, or eval scanned project code.
- Never make live vendor, database, warehouse, or network calls in tests.
- Mock browser open / filesystem errors; do not hit the network to “verify” HTTP or LLM nodes.
- Fixtures must not contain real API keys.

## Architecture

```text
file walk
  → every matching parser (multi-dispatch)
  → candidate graph (builder merge)
  → project_lineage_graph()  # language-agnostic relevance scope
  → .daygent/graph.json
```

**Scan broadly, graph narrowly.** Python still extracts helpers; scoping drops them unless they connect data/AI anchors (SQL/dbt, LLM, embeddings, vector stores, HTTP hosts, LangGraph nodes) or sit on a path between those anchors.

The scope layer keys off **node types and edges**, not Python. A future TypeScript parser can reuse it if it emits `*_function` / `*_module` code types and the same domain anchors.

### Graph direction

**A → B means B depends on A.** Parsers must not reverse this. Impact walks with the arrows; upstream walks against them.

Typer lives only in `src/daygent/cli.py`. Scanner, parsers, graph, models, analysis, and the HTML renderer must not import `typer` or `daygent.cli`.

## Parser contract

`BaseParser` in `src/daygent/parsers/base.py`:

- `supports(path, context) -> bool`
- `parse(path, context) -> ParseResult` with `nodes`, `edges`, `warnings`

`ParserRegistry.matching()` runs **every** matching parser on a file (Python + FastAPI + AI + HTTP on the same `.py` is expected). Register new parsers in `default_registry()` in registration order.

Implementations must:

- use AST / regex / sqlglot on text only
- never import the scanned module
- never contact the network
- emit warnings for malformed files and continue (scan exit 0)
- skip or warn on files larger than 10 MiB (scanner already skips)

## Adding a node or edge type

`Node.type` and `Edge.type` are strings. Prefer constants in `src/daygent/models/types.py` (`NodeType`, `EdgeType`). Unknown lowercase snake_case types are allowed so parsers can grow without changing traversal.

If the type is a **domain anchor** (should always remain after scoping), add it to `ANCHOR_TYPES` in `src/daygent/graph/scope.py`. Language-level code should keep a `*_function` / `*_module` / `*_class` suffix so scoping stays language-agnostic.

Stable ids go through `src/daygent/models/ids.py`. Do not use UUIDs for static graph elements.

Structured edge evidence belongs in `edge.evidence` plus small `metadata` (`file_path`, `line_number`, `reference`). Never put full source files into graph metadata.

## Adding a parser or detector

1. Implement `BaseParser` (or a detector called from an existing parser, as HTTP/AI do).
2. Register it in `default_registry()`.
3. Add focused tests plus, when the behavior is user-visible, coverage in the golden fixture.
4. Assert edge **direction**, confidence, and evidence. Do not invent edges Daygent cannot statically justify.
5. Confirm core modules still have no Typer import (`tests/test_package.py`).

Same-file Python calls are in scope. **Cross-file Python call resolution is a known v0.1 limitation** — do not build a language server to “complete” a chain in a fixture.

## CLI vs library

- Library APIs (`Scanner`, `impact`, `get_descendants`, `render_html`) are the source of truth.
- `cli.py` formats stdout and writes artifacts. Keep business rules out of Typer callbacks when they already live in a library module.

## License

MIT is the working GitHub-first license. Do not treat a PyPI release as done until the license issue is confirmed and publishing is explicitly requested.
