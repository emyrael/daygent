# Daygent

Local-first static lineage and blast-radius analysis for modern data + AI repositories.

Daygent reads source files on disk and builds a dependency graph you can inspect, query, and share as JSON, Mermaid, or a fully offline HTML viewer. It answers questions like:

- What depends on this table or dbt model?
- Which API routes could change if this function changes?
- What is downstream of this dataset?
- Which agents, LLMs, or vector collections sit on this path?

It does **not** connect to warehouses, APIs, vector databases, or cloud accounts. It does **not** execute scanned code, resolve environment variables, or phone home.

## Why it exists

Data + AI codebases mix SQL, dbt, Python services, FastAPI, LangGraph, LLM constructors, and HTTP clients. Runtime catalogs and vendor consoles only see what already ran. Daygent maps what the **source** statically declares, so you can review impact before you ship.

## How it works

```text
Repository
   ↓
Scanner (walk files, skip junk)
   ↓
Technology parsers (all matching parsers run)
   ↓
Candidate graph
   ↓
Relevance scope  ←  scan broadly, graph narrowly
   ↓
.daygent/graph.json
   ↓
daygent graph  |  daygent impact  |  HTML viewer
```

Parsers may see every function in a file. The final graph keeps **data/AI lineage**: tables, dbt models, LLMs, vector stores, HTTP hosts, LangGraph nodes, and the connector code that joins them. Unrelated helpers such as `format_date()` stay out unless they sit on that lineage.

## Graph convention

**A → B means B depends on A.** Impact walks with the arrows. Upstream lineage walks against them.

```text
raw.users
   ↓
stg_users
   ↓
user_features
```

Changing `raw.users` can affect everything below it.

## v0.1 technologies

Static detection only:

| Area | What Daygent reads |
| --- | --- |
| Python | modules, functions, local calls, imports |
| SQL | table lineage via sqlglot, including CTEs |
| dbt | `ref()` / `source()` from Jinja SQL (no dbt CLI, manifest optional) |
| FastAPI | routes, handlers, `include_router` |
| LangGraph | literal `add_node` / `add_edge` |
| LLM constructors | ChatOpenAI, Anthropic, Azure, … when the model is a literal |
| Embeddings | OpenAI / HuggingFace / SentenceTransformer constructors |
| Vector stores | Qdrant, Pinecone, Weaviate, Chroma, FAISS, PGVector |
| External HTTP | literal `https://host/...` URLs |

No live vendor, database, or network calls. Dynamic model names (`os.getenv("MODEL")`) are marked dynamic and are **not** resolved.

## Install

Requires Python 3.11+.

```bash
pip install daygent
```

With uv:

```bash
uv tool install daygent
```

One-off:

```bash
uvx daygent --help
```

## Quick Start

```bash
daygent scan .
daygent graph --html --open
daygent impact <node>
```

From a clone (contributors):

```bash
git clone https://github.com/emyrael/daygent.git
cd daygent
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Commands

```bash
daygent scan .
daygent graph
daygent graph --json
daygent graph --mermaid
daygent graph --html --open
daygent impact <node>
```

`scan` writes `.daygent/graph.json` (gitignored). `graph` and `impact` read that artifact. If it is missing, the CLI tells you to scan first.

Useful flags:

```bash
daygent scan . --verbose
daygent graph --html --include-source --open
daygent impact stg_users --depth 2 --json
```

`--include-source` is opt-in. Default HTML never embeds repository file contents. With the flag, the viewer includes a short excerpt around known lines and banners: *This HTML contains source-code excerpts.*

## Config is optional

No `daygent.yml` is required. Empty or missing `include` means repository-wide discovery, then lineage scoping.

```yaml
# daygent.yml — optional overrides only
exclude:
  - migrations/**
  - generated/**
output: .daygent/graph.json
```

Built-in skips include `.venv`, `node_modules`, `graphify-out`, `.cursor`, and `*.egg-info`.

## HTML viewer

`daygent graph --html --open` writes a self-contained `.daygent/graph.html`:

- pan, zoom, search, type filters
- click a node for details, connections, evidence, metadata
- click an edge for relationship evidence
- drag nodes; blast-radius / upstream / downstream remain
- no CDN, no `fetch`, no telemetry

## Local-first guarantees

- Fully offline after install
- No telemetry
- No runtime network calls from Daygent itself
- No warehouse / vector-store / API credentials
- No secret or environment resolution
- Scan warnings do not print file bodies

## Known limitations (v0.1)

- Cross-file Python call resolution is incomplete. Same-file calls are detected; calls across modules are not fully linked.
- Only literal HTTP URLs and literal LLM / collection names are recorded.
- Isolated Python helpers and unrelated health routes are omitted by design (scoping).
- dbt compile and warehouse introspection are out of scope.
- Default HTML does not ship source code.

The golden fixture at `tests/fixtures/golden_mixed_stack/` locks this behavior.

## Roadmap

- Real-repository testing beyond fixtures

## License

MIT. See [LICENSE](LICENSE).

## Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, parser contracts, and test rules.
