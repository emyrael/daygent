# Product Requirements Document: Daygent

## 1. Document Control

- Status: Draft
- Version: 0.1.0
- Last Updated: 2026-09-18
- Source Material: Product brief in conversation (local-first static lineage CLI); repository inspection of `emyrael/daygent` (empty except `# daygent` README)
- Owners: `[DECISION NEEDED]` — GitHub repo is [emyrael/daygent](https://github.com/emyrael/daygent)
- Related Documents: This file is the first product spec. No `docs/discovery.md` exists.

### Changelog

- 2026-09-18 — Initial PRD from product brief. Confirmed: full v0.1 detector set; single graph convention (upstream → downstream consumer).
- 2026-09-18 — Locked MVP assumptions: `daygent.yml` keys `include`/`exclude`/`output`; scan warnings keep exit code 0; typed node IDs; SQL tables merge by normalized name; skip files > 10 MiB; no telemetry; pathlib; English-only CLI; MIT license provisional until public tag; GitHub-first (PyPI/maintainers/SLOs deferred).

---

## 2. Executive Summary

Daygent is an open-source, local-first CLI for static lineage and blast-radius analysis of Data Engineering and AI Engineering repositories. A developer installs it with `pip install daygent`, runs `daygent scan .`, and gets a normalized dependency graph of Python, SQL, dbt, FastAPI, LangGraph, LLM/embedding/vector-store usage, and statically identifiable external HTTP calls — without connecting to warehouses, APIs, vector databases, or cloud accounts.

The graph is stored locally as `.daygent/graph.json`. `daygent graph` prints a readable summary (and JSON/Mermaid). `daygent impact <node>` walks downstream consumers to show what would be affected if that node changed.

v0.1 ships the installable Python package, CLI, unified graph model, parsers listed above, and reusable lineage APIs. It does **not** ship a web UI or FastAPI server.

---

## 3. Context and Problem

Modern data and AI codebases mix SQL, dbt, Python services, FastAPI routes, LangGraph agents, LLM clients, embeddings, and vector stores. Lineage is usually split across dbt docs, tribal knowledge, runtime traces, or warehouse catalogs. Those tools require live systems, miss AI/agent structure, or only cover one technology.

Workarounds today: reading dbt `ref()`/`source()` by hand, grepping for `ChatOpenAI` and route decorators, or running production-connected catalogs. Those are slow, incomplete, and unsafe for local/offline work.

Daygent is worth building now because the same repository often contains both classic data lineage and AI control flow, and there is no small local-first tool that normalizes both into one impact graph.

---

## 4. Goals and Success Measures

### Goals

- Make Daygent installable as a Python 3.11+ package (`pip install daygent`) exposing the `daygent` CLI.
- Produce a useful local graph from a messy real repository without network access to production systems.
- Give developers a single, consistent mental model: **A → B means B depends on / consumes / is affected by A**.
- Support blast-radius questions: “If this model, function, or collection changes, what breaks?”
- Keep scanners, parsers, graph engine, and analysis independent of Typer so a later API/UI can reuse the same core.

### Non-Goals

- Web frontend or FastAPI application (post-MVP).
- Connecting to production databases, warehouses, vector DBs, LLM APIs, or cloud accounts.
- Running dbt, compiling projects, or executing pipelines.
- Perfect runtime name resolution, dynamic Python, or Jinja beyond dbt `ref`/`source`.
- Airflow, Dagster, Spark, Terraform, Kafka, Snowflake, Databricks, or TypeScript parsers (architecture must allow them later).
- Multi-user auth, SaaS tenancy, or a hosted graph service.
- A database or server process for graph storage.

### Success Metrics

| Metric | Baseline | Target | Measurement method | Period |
|--------|----------|--------|--------------------|--------|
| CLI happy path | n/a | `scan`, `graph`, `impact` succeed on the fixture repo | pytest + manual fixture run | v0.1 release |
| Scan robustness | n/a | One malformed file MUST NOT abort the scan | pytest with a broken `.sql`/`.py` fixture | v0.1 release |
| Graph determinism | n/a | Two scans of unchanged fixtures produce identical `graph.json` | pytest byte/canonical JSON compare | v0.1 release |
| Detector coverage on fixtures | n/a | Each v0.1 parser produces expected nodes/edges on golden fixtures | pytest | v0.1 release |
| Time-to-first-graph | unknown | `[DECISION NEEDED]` (e.g. scan a mid-size repo in N seconds) | local timing on a sample repo | post-MVP unless a target is set |
| Adoption | 0 installs | `[DECISION NEEDED]` PyPI downloads / GitHub stars | PyPI / GitHub | post-launch |

---

## 5. Users, Roles, and Permissions

v0.1 is a local CLI. There is no account system. The “roles” below are usage personas and OS-level file access, not application RBAC.

### Developer (primary)

- Purpose: understand lineage and blast radius in a checkout they already can read.
- Main actions: install Daygent, scan a repo, inspect the graph, run impact on a node, export JSON/Mermaid.
- Accessible data: source files the process can read; `.daygent/graph.json` it writes.
- Prohibited: Daygent MUST NOT require credentials for warehouses/APIs/vector stores. It MUST NOT exfiltrate source code.
- Permission boundary: filesystem permissions of the invoking user only.

### Open-source contributor

- Purpose: add parsers, fix detection, extend node/edge types.
- Main actions: follow parser contract; add fixtures and tests.
- Prohibited: coupling core analysis to the CLI; contacting live external systems in tests.

### CI system `[ASSUMPTION]`

- May run `daygent scan` in pipelines as a local tool. No special role in v0.1.

### Authentication / tenancy

Not applicable. Single-process, local-first, no tenants.

---

## 6. User Journeys

### Happy path

1. Developer installs `pip install daygent`.
2. From a repo root, they run `daygent scan .`.
3. Daygent discovers supported files, runs parsers, writes `.daygent/graph.json`, and prints a scan summary (file counts, node/edge totals, output path).
4. They run `daygent graph` and see a readable downstream chain (or `--json` / `--mermaid`).
5. They run `daygent impact stg_users` and see direct vs transitive downstream consumers, with depth.
6. They copy Mermaid into Markdown or consume `--json` in another tool.

### Alternate paths

| Path | Behaviour |
|------|-----------|
| Cancellation | Ctrl-C MUST stop the process. Partial `.daygent/graph.json` MUST NOT be left as if it were a successful scan. `[ASSUMPTION]`: write to a temp file then replace atomically. |
| Retry | Re-running `scan` overwrites `.daygent/graph.json` deterministically. |
| Correction | After code edits, user re-scans. No incremental cache is required in v0.1. |
| Missing graph | `graph` / `impact` without a prior scan MUST fail clearly and tell the user to run `daygent scan`. |
| Empty / unsupported repo | Scan succeeds with zero or few nodes, prints zeros, still writes a valid graph file. |
| Ambiguous impact target | If several nodes match the query, Daygent MUST list candidates and exit non-zero rather than picking silently. |
| Unknown node | Print not-found plus closest name suggestions. |
| Parse failure | Warn, record warning in graph metadata, continue. |
| Verbose | `daygent scan . --verbose` prints extra diagnostics (file-level parser choice, skipped paths, parse errors). |
| Permission denial | Unreadable files: warn and continue. Unwritable `.daygent/`: fail with a filesystem error. |

---

## 7. User Stories

`US-001` — As a data/AI engineer, I want to install Daygent with pip, so that I can run it on any checkout without a cloud account.

`US-002` — As a data/AI engineer, I want `daygent scan .` to build a local graph from source and config files, so that I get lineage without warehouse credentials.

`US-003` — As a data/AI engineer, I want a scan summary (counts by detected kind + totals), so that I can trust the tool found my stack.

`US-004` — As a data/AI engineer, I want `daygent graph` to show readable downstream chains, so that I can explain how data/AI pieces connect.

`US-005` — As a data/AI engineer, I want `daygent graph --json` and `--mermaid`, so that I can pipe the graph into other tools or docs.

`US-006` — As a data/AI engineer, I want `daygent impact <node>` with optional `--depth` and `--json`, so that I can estimate blast radius.

`US-007` — As a data/AI engineer, I want ambiguous names to produce candidates, so that I never get a silent wrong impact report.

`US-008` — As a data engineer, I want dbt `ref()` / `source()` lineage even without `target/manifest.json`, so that uncompiled projects still work.

`US-009` — As a data engineer, I want SQL CREATE/INSERT/SELECT/JOIN/MERGE lineage, so that non-dbt SQL is still on the graph.

`US-010` — As a backend engineer, I want FastAPI routes and handlers on the graph, so that I can see which endpoints depend on which functions.

`US-011` — As an AI engineer, I want LangGraph nodes/edges, LLM, embedding, and vector-store detections, so that agent stacks have blast radius too.

`US-012` — As an AI/backend engineer, I want statically obvious HTTP hosts on the graph, so that external systems are visible without calling them.

`US-013` — As a contributor, I want a parser interface, so that I can add Airflow/Spark/etc. later without rewriting the scanner.

`US-014` — As a future API/UI developer, I want lineage/impact functions in the core package, so that the web app does not reimplement graph logic.

`US-015` — As a user of a messy repo, I want one bad file to warn rather than crash the scan, so that partial graphs are still useful.

---

## 8. Functional Requirements

### FR-001 — Installable CLI package

- Requirement: Daygent MUST be packaged with a `src/` layout and expose a `daygent` console script.
- Rationale: `pip install daygent` is the v0.1 product surface.
- Actor: Developer
- Preconditions: Python 3.11+
- Trigger: pip install
- Expected behaviour: `daygent --help` works and lists `scan`, `graph`, `impact`.
- Failure behaviour: Standard packaging/import errors; no extra runtime services.
- Priority: Must
- Related user stories: US-001

### FR-002 — CLI independent of core

- Requirement: `scanner`, parsers, graph store/traversal, and analysis MUST NOT import Typer or CLI presentation modules.
- Rationale: Later FastAPI/UI reuse.
- Actor: Contributor / future API
- Preconditions: Package import
- Trigger: Importing `daygent.analysis` or `daygent.graph`
- Expected behaviour: Core modules import without Typer.
- Failure behaviour: Test failure if a core module imports `typer` or `daygent.cli`.
- Priority: Must
- Related user stories: US-014

### FR-003 — Repository scan

- Requirement: `daygent scan [PATH]` MUST recursively discover supported files, dispatch parsers, build one graph, and write `.daygent/graph.json` under the target path (or configured output).
- Rationale: Primary ingestion path.
- Actor: Developer
- Preconditions: Readable directory
- Trigger: `daygent scan .`
- Expected behaviour: Summary printed; graph saved; non-zero nodes on fixture repos.
- Failure behaviour: See FR-023; unwritable output dir fails the command.
- Priority: Must
- Related user stories: US-002, US-003

### FR-004 — Scan summary

- Requirement: Scan stdout SHOULD include product name, per-kind counts (at least: Python files, SQL files, dbt models, FastAPI routes, LangGraph nodes, external systems), discovered node count, discovered edge count, and graph path.
- Rationale: Trust and quick sanity check.
- Actor: Developer
- Preconditions: Scan completed
- Trigger: End of scan
- Expected behaviour: Human-readable aligned summary similar to the product brief.
- Failure behaviour: Counts MAY be zero; they MUST NOT be omitted on success.
- Priority: Must
- Related user stories: US-003

### FR-005 — Ignore rules

- Requirement: Scanner MUST skip at least: `.git`, `.venv`, `venv`, `node_modules`, `dist`, `build`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.next`, `.daygent`.
- Rationale: Avoid noise and self-ingestion of previous graphs.
- Actor: Scanner
- Preconditions: Directory walk
- Trigger: Scan
- Expected behaviour: Files under those directories are not parsed.
- Failure behaviour: If a skip fails (symlink oddity), warn and continue.
- Priority: Must
- Related user stories: US-002

### FR-006 — File types

- Requirement: Scanner MUST consider at least `.py`, `.sql`, `.yml`, `.yaml`. Parser `supports()` decides actual parsing.
- Rationale: Python, SQL, dbt YAML.
- Actor: Scanner
- Preconditions: File discovered
- Trigger: Scan
- Expected behaviour: Each file is offered to registered parsers; first matching parser(s) run as designed in FR-007.
- Failure behaviour: Unknown extensions ignored silently (verbose: log skip).
- Priority: Must
- Related user stories: US-002

### FR-007 — Parser orchestration

- Requirement: Scanner MUST NOT contain technology-specific parse logic. It MUST call a parser registry.
- Rationale: Extensibility.
- Actor: Scanner
- Preconditions: Parsers registered
- Trigger: File match
- Expected behaviour: `ParseResult(nodes, edges, warnings)` merged into the graph.
- Failure behaviour: Parser exception caught, converted to a warning (FR-023).
- Priority: Must
- Related user stories: US-013

### FR-008 — Unified graph model

- Requirement: All parsers MUST emit the same `Node` / `Edge` model (Pydantic). Node `type` MUST be an extensible string enum (or equivalent) including the v0.1 types in §10, without requiring a core rewrite to add types.
- Rationale: One graph for data + AI.
- Actor: All parsers
- Preconditions: Parse
- Trigger: ParseResult merge
- Expected behaviour: Unknown future types remain representable (`[ASSUMPTION]`: string enum with documented known values, not a closed switch in traversal).
- Failure behaviour: Invalid node/edge (missing id) rejected with warning, not crash.
- Priority: Must
- Related user stories: US-004, US-013

### FR-009 — Graph direction convention

- Requirement: Every edge MUST follow **upstream → downstream consumer**. `A → B` means B depends on / consumes / is affected by A.
- Rationale: One rule for lineage, impact, and Mermaid.
- Actor: Parsers / analysis
- Preconditions: Edge created
- Trigger: Graph build
- Expected behaviour:
  - `raw_users → stg_users → user_features → recommend() → POST /recommend`
  - AI: `documents → embedding_model → vector_collection → retriever → agent → POST /ask`
  - FastAPI handler call: `recommend() → POST /recommend` with edge type `invoked_by`
- Failure behaviour: Parsers MUST NOT emit caller→callee as the stored direction.
- Priority: Must
- Related user stories: US-004, US-006, US-010

### FR-010 — Deterministic graph.json

- Requirement: Serialized graph MUST sort nodes (by id) and edges (by source, target, type) and use stable IDs so two scans without code changes do not produce meaningless diffs.
- Rationale: Git-friendly local artifacts; testability.
- Actor: Graph store
- Preconditions: Scan complete
- Trigger: Write `.daygent/graph.json`
- Expected behaviour: Canonical JSON (stable key order via Pydantic/model dump). Timestamps may change; tests MAY ignore `scan.timestamp` when comparing.
- Failure behaviour: Write error fails the command.
- Priority: Must
- Related user stories: US-002

### FR-011 — Python AST analysis

- Requirement: Python parser MUST use the stdlib `ast` module (not regex) to extract modules, functions, imports, and locally resolvable function calls, with file path and line numbers when AST provides them.
- Rationale: Quality of static analysis.
- Actor: Python parser
- Preconditions: Parseable `.py`
- Trigger: Scan of Python file
- Expected behaviour: Nodes for module and functions; edges for imports and resolvable local calls using FR-009 direction.
- Failure behaviour: SyntaxError → warning; unresolved dynamic calls recorded as metadata/low-confidence or omitted rather than invented.
- Priority: Must
- Related user stories: US-010, US-011

### FR-012 — FastAPI routes

- Requirement: Detect `@app|router.(get|post|put|patch|delete)(...)` and create `api_route` nodes named like `POST /recommend`, with metadata `{ "method", "path" }`. Associate handler functions. Detect `include_router` when statically obvious.
- Rationale: API blast radius.
- Actor: Python parser
- Preconditions: FastAPI-style decorators
- Trigger: Python parse
- Expected behaviour: `function → api_route` via `invoked_by` when the handler is known. Router includes recorded as metadata and/or `routes_to` / `mounted_in` edges if resolvable.
- Failure behaviour: Dynamic path/method → node with `dynamic: true` metadata, confidence low/medium; do not guess.
- Priority: Must
- Related user stories: US-010

### FR-013 — SQL lineage via sqlglot

- Requirement: SQL parser MUST use `sqlglot` to extract relations from SELECT/FROM/JOIN, CREATE TABLE/VIEW, INSERT, UPDATE, MERGE. CTEs MUST NOT be emitted as physical tables when underlying sources can be determined.
- Rationale: Accurate SQL lineage without a hand-rolled parser.
- Actor: SQL parser
- Preconditions: `.sql` file (including dbt SQL after Jinja stripping/ref rewrite — see FR-014)
- Trigger: Parse
- Expected behaviour: For `CREATE TABLE customer_summary AS SELECT ... FROM customers JOIN orders`, edges `customers → customer_summary` and `orders → customer_summary` (`read_by` or `depends_on` as specified in §9).
- Failure behaviour: Unsupported/malformed SQL → warning; file skipped; scan continues.
- Priority: Must
- Related user stories: US-009

### FR-014 — dbt without running dbt

- Requirement: Detect `{{ ref('...') }}` and `{{ source('schema','table') }}`. Model file `models/customer_features.sql` with `ref('stg_users')` yields `stg_users → customer_features`. `source('raw','users')` yields `raw.users → <current model>` as a `dbt_source` node. MUST work without `target/manifest.json`. MUST NOT invoke dbt. If `target/manifest.json` exists, parser MAY merge more accurate lineage, tagged with evidence `dbt_manifest`.
- Rationale: dbt is core to data repos; compiled manifest is optional.
- Actor: dbt parser
- Preconditions: dbt-style SQL/YAML in the repo
- Trigger: Scan
- Expected behaviour: `dbt_model` / `dbt_source` nodes; high-confidence `ref` / `source` edges.
- Failure behaviour: Broken Jinja: warn; still try remaining files. YAML schema files MAY contribute source definitions when present.
- Priority: Must
- Related user stories: US-008

### FR-015 — LangGraph detection

- Requirement: Heuristically detect `add_node(name, fn)`, `add_edge(a, b)`, `add_conditional_edges(...)`. Literal `add_edge("retrieve", "generate")` becomes `retrieve → generate` (high confidence).
- Rationale: AI graph structure is a differentiator.
- Actor: AI parser (Python AST)
- Preconditions: Python file
- Trigger: Parse
- Expected behaviour: `langgraph_node` nodes; edges matching documented graph convention. Conditional edges recorded with metadata (`conditional: true`, evidence) even if the mapping is not fully static.
- Failure behaviour: Dynamic node names → low confidence metadata, no invented names.
- Priority: Must
- Related user stories: US-011

### FR-016 — LLM detection

- Requirement: Detect constructors such as `ChatOpenAI`, `OpenAI`, `ChatAnthropic`, `Anthropic`, `AzureOpenAI`. Create `llm` nodes. Capture literal `model=`; if the model is an env lookup or other dynamic expression, store the expression / `dynamic: true` and MUST NOT resolve secrets.
- Rationale: Visible model usage without calling providers.
- Actor: AI parser
- Preconditions: Matching AST
- Trigger: Parse
- Expected behaviour: Metadata e.g. `{ "provider": "openai", "model": "gpt-5" }` when literal.
- Failure behaviour: Unknown kwargs ignored; no network.
- Priority: Must
- Related user stories: US-011

### FR-017 — Embedding detection

- Requirement: Detect `OpenAIEmbeddings`, `SentenceTransformer`, `HuggingFaceEmbeddings` (and obvious aliases). Where the using function is known, connect `function → embedding_model` or `embedding_model → vector_collection` per available evidence and FR-009.
- Rationale: Ingestion/retrieval blast radius.
- Actor: AI parser
- Preconditions: Matching AST
- Trigger: Parse
- Expected behaviour: `embedding_model` nodes; edges only when statically justified.
- Failure behaviour: Isolated constructor still creates a node with file/line metadata.
- Priority: Must
- Related user stories: US-011

### FR-018 — Vector store detection

- Requirement: Recognize Pinecone, Qdrant, Weaviate, Chroma, FAISS, pgvector construction patterns in code only. Literal collection/index names become `vector_collection` nodes related to a `vector_store` node. Dynamic names marked dynamic; no service connections.
- Rationale: Collection-level impact without credentials.
- Actor: AI parser
- Preconditions: Matching AST
- Trigger: Parse
- Expected behaviour: Example: `QdrantVectorStore(collection_name="company_docs")` → `vector_store` (qdrant) and `vector_collection` (company_docs) with an appropriate edge (collection depends on store: `store → collection`).
- Failure behaviour: Unresolved kwargs → store node without collection.
- Priority: Must
- Related user stories: US-011

### FR-019 — External HTTP detection

- Requirement: Recognize `requests.get/post`, `httpx.get/post`, and `client.get/post` when the URL/hostname is a string literal. Create `external_system` (host) nodes. Example: `requests.get("https://api.stripe.com/v1/customers")` from `fn` yields `api.stripe.com` with `fn → api.stripe.com` (function depends on the external system). Confidence medium. Provider-specific detectors MUST be pluggable later.
- Rationale: Surface third-party dependencies without calling them.
- Actor: Python/AI parser HTTP detector
- Preconditions: Literal URL
- Trigger: Parse
- Expected behaviour: No node for fully dynamic URLs; MAY record low-confidence metadata on the function.
- Failure behaviour: Do not guess hostnames from concatenations beyond simple literal strings. `[ASSUMPTION]`: string constants and `ast.Constant` only in v0.1.
- Priority: Must
- Related user stories: US-012

### FR-020 — Graph display

- Requirement: `daygent graph` loads `.daygent/graph.json` and prints a readable summary of chains. `--json` prints the stored graph (or a documented subset). `--mermaid` prints Mermaid `graph LR` with Mermaid-safe IDs, arrows matching FR-009.
- Rationale: Inspection and docs export.
- Actor: Developer
- Preconditions: Successful prior scan
- Trigger: `daygent graph`
- Expected behaviour: Human output uses display names; Mermaid uses sanitized IDs.
- Failure behaviour: Missing graph → non-zero exit + instruction to scan.
- Priority: Must
- Related user stories: US-004, US-005

### FR-021 — Impact analysis CLI

- Requirement: `daygent impact <node>` MUST resolve the node, walk downstream (with arrows), split **direct** (depth 1) vs **transitive** (depth ≥ 2), show depth, support `--depth N` (inclusive max depth) and `--json`.
- Rationale: Core product question.
- Actor: Developer
- Preconditions: Graph exists
- Trigger: impact command
- Expected behaviour: Counts and lists of affected nodes; JSON schema in §11.
- Failure behaviour: Ambiguous or missing node: candidates, non-zero exit, no silent pick.
- Priority: Must
- Related user stories: US-006, US-007

### FR-022 — Core lineage API

- Requirement: Package MUST expose reusable functions (names MAY match): `get_upstream`, `get_downstream`, `get_ancestors`, `get_descendants`, `impact`. These MUST live outside CLI modules.
- Rationale: Future web/API.
- Actor: Library consumer
- Preconditions: In-memory or loaded graph
- Trigger: Function call
- Expected behaviour:
  - downstream/descendants/impact = walk **with** arrows
  - upstream/ancestors = walk **against** arrows
- Failure behaviour: Unknown id raises a typed error or returns a structured not-found result; MUST NOT hang on cycles (cycles allowed; traversal MUST visit each node at most once per walk).
- Priority: Must
- Related user stories: US-014

### FR-023 — Non-fatal parse errors

- Requirement: One unreadable/unparseable file MUST NOT abort repository scan. Emit a warning (`Warning: unable to parse <path>`), continue, store warnings in `scan.warnings`.
- Rationale: Real repos are messy.
- Actor: Scanner
- Preconditions: Parse exception
- Trigger: Parser failure
- Expected behaviour: Graph still written; exit code 0 if the scan otherwise completed. `[ASSUMPTION]`: exit 0 with warnings; `--strict` is post-MVP.
- Failure behaviour: Unexpected bugs in Daygent itself MAY still crash (those are defects).
- Priority: Must
- Related user stories: US-015

### FR-024 — Verbose diagnostics

- Requirement: `daygent scan . --verbose` MUST show extra diagnostics (skipped dirs, parser selected, per-file errors).
- Rationale: Debug detection.
- Actor: Developer
- Preconditions: Scan
- Trigger: `--verbose`
- Expected behaviour: Rich/logging debug on stderr; graph still written.
- Failure behaviour: Verbose MUST NOT print secrets from source (e.g. do not dump full files).
- Priority: Should
- Related user stories: US-015

### FR-025 — Optional daygent.yml

- Requirement: If `daygent.yml` or `daygent.yaml` exists at the scan root, it MUST be applied. If absent, built-in defaults apply.
- Rationale: Repo-specific ignores without flags.
- Actor: Developer
- Preconditions: Optional file
- Trigger: Scan start
- Expected behaviour: See BR-010. Invalid YAML → fail scan with a config error (config is user-controlled; unlike a random SQL file).
- Failure behaviour: Unknown keys SHOULD warn and ignore (`[ASSUMPTION]`).
- Priority: Should
- Related user stories: US-002

### FR-026 — Confidence on edges

- Requirement: Edges MUST carry `confidence` in `{high, medium, low}` and SHOULD carry `evidence` (stable string).
- Rationale: Never pretend static inference is perfect.
- Actor: Parsers
- Preconditions: Edge emit
- Trigger: Parse
- Expected behaviour: See BR-004.
- Failure behaviour: Missing confidence defaults to `low` at merge time rather than crashing. `[ASSUMPTION]`
- Priority: Must
- Related user stories: US-008–US-012

### FR-027 — Recognizable external clients

- Requirement: Python parser SHOULD flag recognizable client constructors (e.g. SDK classes) as `api_client` / `external_system` when imports/names are statically obvious, without calling them.
- Rationale: Brief asks for recognizable external service/client construction.
- Actor: Python parser
- Preconditions: AST
- Trigger: Parse
- Expected behaviour: Pluggable name tables; v0.1 MAY start small (HTTP + listed AI vendors).
- Failure behaviour: Prefer omit over false certainty.
- Priority: Should
- Related user stories: US-012

---

## 9. Business Rules

### BR-001 — Arrow meaning

`A → B` always means: B depends on A; changing A may affect B. Impact and downstream walk with arrows. Upstream walks against arrows. Mermaid uses the same direction.

### BR-002 — Node identity

Each node MUST have a stable `id` unique in the graph. `[ASSUMPTION]` ID scheme:

| Kind | Pattern | Example |
|------|---------|---------|
| Python module | `python_module:<posix_relpath>` | `python_module:src/services/recommend.py` |
| Function | `python_function:<posix_relpath>:<qualname>` | `python_function:src/services/recommend.py:recommend` |
| SQL table | `sql_table:<name>` | `sql_table:customers` |
| dbt model | `dbt_model:<name>` | `dbt_model:stg_users` |
| dbt source | `dbt_source:<source>.<table>` | `dbt_source:raw.users` |
| API route | `api_route:<METHOD>:<path>` | `api_route:POST:/recommend` |
| LangGraph node | `langgraph_node:<graph_or_file>:<name>` | `langgraph_node:src/agent.py:retrieve` |
| LLM | `llm:<provider>:<model_or_expr>:<file>:<line>` | `llm:openai:gpt-5:app.py:12` |
| Embedding | `embedding_model:<library>:<file>:<line>` | … |
| Vector store | `vector_store:<vendor>:<file>:<line>` | `vector_store:qdrant:app.py:40` |
| Collection | `vector_collection:<vendor>:<name>` | `vector_collection:qdrant:company_docs` |
| External host | `external_system:<hostname>` | `external_system:api.stripe.com` |

Collisions (same table name in two SQL files): SAME `sql_table` id is allowed (merged node, union of metadata/file list). `[ASSUMPTION]`: tables are global by default in v0.1; schema-qualified names used when present.

Display `name` is human-facing (`stg_users`, `POST /recommend`) and is what `impact` matches first.

### BR-003 — Edge types (v0.1 set, extensible)

Edge `type` describes the relationship **given stored direction** (source is upstream of target).

| type | Meaning | Typical confidence |
|------|---------|-------------------|
| `imported_by` | target module/function imports source | high |
| `invoked_by` | target calls/invokes source (e.g. function → route) | high |
| `read_by` | target reads source relation | high/medium |
| `written_by` | source writes into target? **Forbidden as inverted.** Use `writes` as: source table/query is upstream of the written table: `source_table → dest_table` type `read_by` or `feeds` | — |
| `feeds` | generic data/control flow source → consumer | medium |
| `depends_on` | generic; prefer a more specific type | medium |
| `ref` | dbt ref: referenced model → current model | high |
| `source` | dbt source → model | high |
| `exposes` | inner unit → API/service surface | high |
| `queries` | function/model → relation it queries; **direction must still be relation → function** if the function is the consumer. Prefer `read_by`. | — |
| `embeds_with` | documents/function consume embedding model: `embedding_model → function` or `docs → embedding_model` per evidence | medium |
| `retrieves_from` | retriever consumes collection: `vector_collection → retriever` | medium |
| `routes_to` | router include: route/router → app? App **depends on** router: `router → app` | medium |
| `invokes` | **Do not store caller→callee.** Use `invoked_by` with function as source. | — |

Parsers MUST use `invoked_by` for “POST /recommend calls recommend()”:

```json
{
  "source": "python_function:…:recommend",
  "target": "api_route:POST:/recommend",
  "type": "invoked_by",
  "confidence": "high",
  "evidence": "fastapi_handler"
}
```

### BR-004 — Confidence mapping

| Evidence | Confidence |
|----------|------------|
| dbt `ref()` / `source()` | high |
| dbt manifest edge | high |
| literal LangGraph `add_edge` | high |
| Python import | high |
| FastAPI decorator + same-file handler | high |
| sqlglot explicit table refs | high |
| heuristic HTTP literal URL | medium |
| include_router with unresolved prefix | medium |
| dynamic Python / env model name / f-string URL | low |

Confidence is an enum, not free text.

### BR-005 — Impact resolution

1. Exact `id` match.
2. Exact display `name` match (case-sensitive `[ASSUMPTION]`).
3. Unique suffix / unique case-insensitive name.
4. Else: print candidates (id + name + type + file), exit non-zero.

### BR-006 — Depth

`--depth N` includes nodes with `1 <= depth <= N`. Direct impact is always depth 1. Depth 0 is the selected node and MUST NOT be listed as affected.

### BR-007 — CTE handling

CTE names are not graph nodes when sqlglot can in-line their sources onto the final target. If a CTE cannot be resolved, `[ASSUMPTION]`: omit rather than emit a fake table.

### BR-008 — No live systems

Parsers MUST NOT open network connections, read cloud SDK default chains, or execute SQL against databases.

### BR-009 — Secrets

Do not resolve or print environment variable values. Record `os.getenv("X")` as dynamic expression text `os.getenv("X")` only.

### BR-010 — Configuration

v0.1 `daygent.yml` / `daygent.yaml` keys (confirmed):

```yaml
exclude:
  - "vendor/**"
  - "notebooks/**"
include: []          # optional extra globs; empty = all supported files minus exclude
output: ".daygent/graph.json"
```

Built-in ignore dirs always apply in addition to `exclude`.

### BR-011 — Time zones

`scan.timestamp` MUST be UTC ISO-8601 with `Z`.

### BR-012 — Locale

CLI messages in English for v0.1. No i18n requirement.

### BR-013 — Duplicate edges

Same `(source, target, type)` merged; metadata/evidence MAY be combined; confidence = max(high>medium>low).

### BR-014 — Deletion / lifecycle

Graph is fully replaced on each successful scan. No historical versions in v0.1.

### BR-015 — Self-graph

Never parse `.daygent/**`.

---

## 10. Data Requirements

### Entities

#### Node

- Purpose: one graph vertex (code unit, dataset, AI component, or external system).
- Key fields: `id` (required), `name` (required), `type` (required), `file_path` (optional), `line_number` (optional), `metadata` (dict, default `{}`).
- Owner: local project (the scanned tree). No tenant.
- Relationships: incident edges.
- Uniqueness: `id`.
- Lifecycle: recreated each scan.
- Sensitive-data classification: file paths and code-derived names; MUST NOT store env secret values.
- Retention: until next scan or user deletes `.daygent/`.

v0.1 `type` values (extensible):

`python_module`, `python_function`, `sql_table`, `dbt_model`, `dbt_source`, `api_route`, `api_client`, `service`, `langgraph_node`, `agent`, `llm`, `embedding_model`, `vector_store`, `vector_collection`, `external_system`.

#### Edge

- Purpose: directed dependency from upstream `source` to downstream `target`.
- Key fields: `source`, `target`, `type`, `confidence`, `evidence` (optional), `metadata` (dict).
- Uniqueness: `(source, target, type)`.
- Sensitive: evidence strings should not include secrets.

#### Graph document

- Purpose: serializable scan output.
- Fields: `version` (`"0.1"`), `project` (name/path `[ASSUMPTION]`), `nodes`, `edges`, `scan` (`timestamp`, `files_scanned`, `warnings`).

#### ParseResult (in-memory)

- `nodes`, `edges`, `warnings` — not persisted except as merged into the graph document.

#### Config

- Optional YAML at repo root; see BR-010.

### Data Integrity

- Source of truth: the scanned working tree, not `.daygent/graph.json`. The JSON is a cache/artifact.
- Consistency: after a successful scan, JSON matches in-memory graph.
- Transaction boundary: atomic replace of `graph.json`.
- Idempotency: same tree → same nodes/edges (timestamp excluded).
- Audit / version history: not required.
- Migration: none (new project).
- NetworkX: MAY be used internally for traversal; MUST NOT appear in persisted JSON or public Pydantic models.

---

## 11. API and Integration Contracts

v0.1 public surfaces: CLI and Python functions. No HTTP API.

### CLI

| Command | Purpose | Auth |
|---------|---------|------|
| `daygent scan [PATH] [--verbose] [--config FILE]` | Build graph | none |
| `daygent graph [--json] [--mermaid]` | Display graph | none |
| `daygent impact <node> [--depth N] [--json]` | Blast radius | none |

Exit codes `[ASSUMPTION]`: `0` success (scan may include warnings); `1` usage/resolution error (missing graph, ambiguous node); `2` unexpected crash.

### `daygent impact --json` shape

```json
{
  "query": "stg_users",
  "resolved_id": "dbt_model:stg_users",
  "direct": [{ "id": "...", "name": "...", "type": "...", "depth": 1 }],
  "downstream": [{ "id": "...", "name": "...", "type": "...", "depth": 2 }],
  "affected_count": 4
}
```

`downstream` here is transitive only (depth ≥ 2). `affected_count` = unique nodes in direct + transitive.

Ambiguous:

```json
{
  "error": "ambiguous_node",
  "query": "recommend",
  "candidates": [{ "id": "...", "name": "...", "type": "...", "file_path": "..." }]
}
```

### Python API (core)

Conceptual signatures (implementation names MUST be documented in README):

- `build_graph(root: Path, config: DaygentConfig | None) -> Graph`
- `load_graph(path: Path) -> Graph`
- `save_graph(graph: Graph, path: Path) -> None`
- `get_downstream(graph, node_id) -> list[Node]` (depth 1)
- `get_descendants(graph, node_id, max_depth=None) -> list[tuple[Node, int]]`
- `get_upstream(graph, node_id) -> list[Node]`
- `get_ancestors(graph, node_id, max_depth=None) -> list[tuple[Node, int]]`
- `impact(graph, node_id, max_depth=None) -> ImpactResult`

No authentication. No rate limits. Timeout: bound by local I/O and parse time; `[DECISION NEEDED]` if a max-files or max-seconds flag is required for huge monorepos.

### Third-party libraries (not runtime integrations)

| Library | Direction | Credentials | Outage |
|---------|-----------|-------------|--------|
| sqlglot | local parse | none | unsupported dialect → warning |
| PyYAML | local config/dbt YAML | none | invalid YAML: config fails, dbt YAML warns |
| Pydantic | models | none | validation error → warning or reject node |
| Typer/Rich | CLI only | none | n/a |

No webhooks. No sandbox cloud. Tests MUST use fixtures, not live Stripe/OpenAI/Pinecone.

---

## 12. AI Behaviour

Daygent **analyzes** AI application source; it is not itself an LLM product in v0.1.

- Model responsibility: none at runtime. Detection is deterministic AST/heuristics.
- Deterministic vs probabilistic: parsers MUST be deterministic. No embeddings of customer code. No LLM-as-judge in v0.1.
- Tools: none.
- Allowed: pattern-match known constructors and LangGraph APIs.
- Prohibited: calling LLM APIs; resolving API keys; executing agent graphs.
- Grounding: source files only.
- Confirmation: not required for scan (read-only besides `.daygent/`).
- Fallback: unknown AI patterns omitted, not hallucinated.
- Prompt-injection: N/A (no model). Source that *looks like* prompts is treated as code/strings, not instructions to Daygent.
- Data isolation: local process; no telemetry required in v0.1 (`[ASSUMPTION]`: no phone-home).
- Evaluation: golden fixtures in `tests/fixtures/`.
- Logging: file paths and node names; never env values.

If an LLM is added later for summarization, it MUST NOT be the source of truth for authorization or lineage edges.

---

## 13. UI/UX Requirements

v0.1 surfaces are terminal only (Rich).

### Scan

- Purpose: ingest repo, write graph, confirm counts.
- Entry: `daygent scan [PATH]`
- Components: title “Daygent”, status line, count table, output path.
- Validation: PATH must be a directory.
- Loading: “Scanning repository...”
- Empty: zeros + still writes graph.
- Error: config/IO errors in red; parse warnings as warnings.
- Success: counts + `Graph saved to: .daygent/graph.json`
- Accessibility: plain text MUST remain readable if Rich highlighting is off; avoid color-only meaning.
- Responsive: wrap reasonably in 80-column terminals.
- Destructive: overwrite of `graph.json` needs no confirm (expected).

### Graph

- Purpose: understand structure.
- Entry: `daygent graph`
- Main: indented chains using `↓` for a sample of lineage paths (longest or highest-degree `[ASSUMPTION]`: show up to a capped set of representative paths plus totals; `--json` for complete data).
- `--mermaid`: fenced-ready `graph LR` body (CLI MAY print without markdown fences; README shows how to paste).
- Missing graph: error + hint.

### Impact

- Purpose: blast radius.
- Entry: `daygent impact <node>`
- Sections: title, Directly affected, Downstream (transitive), count line.
- `--depth`: truncate walk.
- Ambiguous: candidate list, no analysis.
- Success: human lists with optional depth annotations.

No web screens.

---

## 14. Non-Functional Requirements

### Security

- `NFR-001` — Daygent MUST NOT require or collect cloud credentials to scan.
- `NFR-002` — Daygent MUST NOT upload repository contents.

### Privacy

- `NFR-003` — v0.1 MUST NOT emit telemetry.

### Accessibility

- `NFR-004` — CLI MUST be usable as plain UTF-8 text (screen readers / redirected stdout).

### Performance

- `NFR-005` — `[DECISION NEEDED]` latency target. `[ASSUMPTION]` for implementation: scan is single-process, streaming file walk; acceptable to be slower than `rg` but finish small fixtures in < 5s on a laptop.

### Scalability

- `NFR-006` — v0.1 targets single-repo local use, not multi-GB monorepos. MAY add exclude config rather than distributed scan.

### Availability

N/A (local CLI).

### Reliability

- `NFR-007` — Parser failures MUST be isolated per file (FR-023).

### Observability

- `NFR-008` — Warnings stored in `graph.scan.warnings`; `--verbose` for diagnostics.

### Maintainability

- `NFR-009` — Parser contract in `parsers/base.py`; new parsers register without editing traversal.
- `NFR-010` — Public models MUST NOT depend on NetworkX.

### Compatibility

- `NFR-011` — Python 3.11+. Cross-platform paths via `pathlib` (no POSIX-only APIs).

### Localization

English only (BR-012).

### Backup and recovery

User-managed git; graph artifact is regenerable.

### Compliance

Open-source license `[DECISION NEEDED]` (MIT vs Apache-2.0). No personal-data processor role in v0.1.

---

## 15. Security and Privacy

- Authentication: none.
- Authorization: OS file permissions only.
- Tenant isolation: n/a.
- Secrets: never resolve env/API keys; never write discovered `.env` values into `graph.json`.
- Encryption: not required for local JSON. Users MAY gitignore `.daygent/` `[ASSUMPTION]`: default `.gitignore` template in the Daygent repo ignores `.daygent/` for consumers; the tool SHOULD mention this in README.
- Sensitive-data handling: node names and paths only.
- File-upload: none.
- Input validation: Pydantic models; path confinement `[ASSUMPTION]`: scan path is user-supplied; do not follow `exclude` bypasses via `..` in a way that writes outside the project output path.
- Abuse prevention: n/a locally; parsers MUST be robust against huge/malicious files (cap file size `[ASSUMPTION]`: skip files > 10 MiB with a warning).
- Audit logging: none beyond verbose stderr.
- Retention: local files until deleted.
- Legal: LICENSE `[DECISION NEEDED]`; third-party notices via package metadata.
- Threat review: regex/AST on untrusted repos (typical OSS linter threat model); no code execution of scanned project. **Do not `eval` or import scanned modules.**

---

## 16. Edge Cases and Failure Handling

| ID | Scenario | Expected Behaviour | Recovery | Monitoring |
|----|----------|--------------------|----------|------------|
| EC-001 | SyntaxError in one `.py` | Warning; continue | Fix file; re-scan | warning in JSON |
| EC-002 | sqlglot cannot parse dialect | Warning; continue | none | warning |
| EC-003 | Broken Jinja in dbt SQL | Warning; no fake refs | none | warning |
| EC-004 | Missing `graph.json` | CLI error, exit 1 | run scan | n/a |
| EC-005 | Ambiguous impact name | Candidates, exit 1 | pass id | n/a |
| EC-006 | Cycles in graph | Traversal visits once; impact still returns | n/a | n/a |
| EC-007 | Duplicate edges | Merge (BR-013) | n/a | n/a |
| EC-008 | Dynamic URL / model | No host/model guess; low/dynamic metadata | n/a | n/a |
| EC-009 | No dbt manifest | Jinja `ref`/`source` still works | optional compile | n/a |
| EC-010 | Manifest present but stale | MAY use it; evidence `dbt_manifest`; source still wins as files `[ASSUMPTION]`: merge, don’t require match | recompile dbt | warning if node missing |
| EC-011 | Concurrent scans | Last atomic write wins | re-run | n/a |
| EC-012 | Unreadable file | Warn; continue | fix perms | warning |
| EC-013 | Unwritable `.daygent` | Fail scan | fix perms | stderr |
| EC-014 | Empty repo | Empty graph, success | n/a | n/a |
| EC-015 | CTE-only SQL | Sources attached to final target; CTE not a table | n/a | n/a |
| EC-016 | `include_router` without prefix | Route path as in decorator | n/a | medium confidence |
| EC-017 | FastAPI path not a string literal | Route node omitted or marked dynamic; no invented path | n/a | warning/verbose |
| EC-018 | Huge file | Skip + warning (NFR size cap) | exclude | warning |
| EC-019 | Symlink loops | Guard with visited-inode/path set | n/a | warning |
| EC-020 | Invalid `daygent.yml` | Fail fast | fix YAML | stderr |

---

## 17. Analytics and Observability

### Product events

None required in v0.1. `[ASSUMPTION]`: no telemetry.

### Operational metrics (local)

- Files scanned, files skipped, files failed
- Nodes/edges by type
- Scan duration (verbose)

### Logs

- Default: warnings + summary
- Verbose: per-file parser and skips
- MUST NOT log secret values or full file contents

### Traces

Not required.

### Alerts / dashboards

Not required.

---

## 18. Rollout and Migration

- Feature flags: not required.
- Data migration: none.
- Backwards compatibility: first public graph schema `version: "0.1"`. Later versions MUST document migrations.
- Staged rollout: GitHub + PyPI. `[DECISION NEEDED]` whether v0.1 publishes to PyPI immediately or GitHub-only first.
- Rollback: uninstall package; delete `.daygent/`.
- Support: README + CONTRIBUTING; GitHub issues.
- Consumer `.gitignore`: recommend ignoring `.daygent/` unless teams want to commit the artifact.

---

## 19. Dependencies and Constraints

### Internal

- Empty repository: implement architecture from this PRD; keep the existing README title.

### External (libraries)

- Python 3.11+
- Typer (CLI)
- Rich (presentation)
- Pydantic (models/config)
- sqlglot (SQL)
- PyYAML (config/dbt YAML)
- pytest (tests)
- NetworkX optional, internal only

### Credentials / approvals

- `[DECISION NEEDED]` PyPI project name `daygent` availability and publish rights.
- `[DECISION NEEDED]` SPDX license.

### Legal

- Do not vendor copyrighted third-party code beyond license-compatible dependencies.

### Design

- CLI copy from this PRD; no separate design system.

### Technical spikes

- sqlglot dialect mix in one repo (default dialect `[ASSUMPTION]`: try `ansi` then fallback / `read=None` as sqlglot allows).
- Distinguishing dbt SQL vs generic SQL (path `models/` + `ref`/`source` or `dbt_project.yml` presence).

### Timeline

No dates. Sequence in §23.

---

## 20. Acceptance Criteria

### AC-001 — Install and help

- Given: Python 3.11+ and the packaged project
- When: the user installs the package and runs `daygent --help`
- Then: the CLI lists `scan`, `graph`, and `impact`
- Related requirements: FR-001

### AC-002 — Scan writes graph

- Given: the golden fixture repository
- When: `daygent scan .` is run from that root
- Then: stdout includes counts and a save path; `.daygent/graph.json` exists with `version`, sorted `nodes`, sorted `edges`, and `scan` metadata
- Related requirements: FR-003, FR-004, FR-010

### AC-003 — Ignore and no crash

- Given: a repo containing `.venv` and one syntactically invalid `.sql` file plus valid files
- When: scan runs
- Then: `.venv` is not scanned; a warning mentions the bad SQL file; valid nodes still appear; command exits 0
- Related requirements: FR-005, FR-023

### AC-004 — Graph convention on data + API

- Given: fixtures with `raw` source → `stg_users` → `user_features` → `recommend()` used by `POST /recommend`
- When: the graph is built
- Then: edges exist `raw.users → stg_users → user_features → recommend → POST /recommend` (by ids/names), and the function-to-route edge type is `invoked_by`
- Related requirements: FR-009, FR-012, FR-014, BR-001, BR-003

### AC-005 — Graph command

- Given: a saved graph
- When: `daygent graph` / `--json` / `--mermaid` run
- Then: human output shows downstream chains; JSON is valid graph document; Mermaid is `graph LR` with `A --> B` matching stored direction and safe IDs
- Related requirements: FR-020

### AC-006 — Impact blast radius

- Given: the AC-004 graph
- When: `daygent impact stg_users`
- Then: direct includes `user_features`; transitive includes `recommend` and `POST /recommend` (and any intermediate); count matches unique downstream nodes
- Related requirements: FR-021, FR-022

### AC-007 — Impact depth and json

- Given: the same graph
- When: `daygent impact stg_users --depth 2 --json`
- Then: no node with depth > 2 is included; JSON matches §11
- Related requirements: FR-021, BR-006

### AC-008 — Ambiguous names

- Given: two functions named `recommend` in different files
- When: `daygent impact recommend`
- Then: no impact list is produced; candidates are shown; exit code is non-zero
- Related requirements: FR-021, BR-005

### AC-009 — SQL CTE and create table

- Given: SQL `CREATE TABLE customer_summary AS SELECT ... FROM customers c JOIN orders o` with a CTE
- When: scanned
- Then: edges from `customers` and `orders` to `customer_summary`; CTE name is not a physical `sql_table`
- Related requirements: FR-013, BR-007

### AC-010 — dbt without manifest

- Given: dbt models using `ref` and `source` and no `target/manifest.json`
- When: scanned
- Then: those edges exist with confidence `high` and evidence `dbt_ref` / `dbt_source`
- Related requirements: FR-014, FR-026

### AC-011 — LangGraph literal edges

- Given: `graph.add_node("retrieve", retrieve)` and `graph.add_edge("retrieve", "generate")`
- When: scanned
- Then: `retrieve → generate` with confidence `high`
- Related requirements: FR-015

### AC-012 — LLM literal vs env

- Given: `ChatOpenAI(model="gpt-5")` and `ChatOpenAI(model=os.getenv("MODEL"))`
- When: scanned
- Then: first node metadata includes provider openai and model `gpt-5`; second marks model dynamic and does not invent a model name or read the environment
- Related requirements: FR-016, BR-009

### AC-013 — Vector collection literal

- Given: `QdrantVectorStore(collection_name="company_docs")`
- When: scanned
- Then: a qdrant `vector_store` and `vector_collection` `company_docs` exist with a connecting edge; no network call
- Related requirements: FR-018, BR-008

### AC-014 — External HTTP literal

- Given: a function calling `requests.get("https://api.stripe.com/v1/customers")`
- When: scanned
- Then: `external_system` `api.stripe.com` exists; edge is `function → host`; confidence `medium`
- Related requirements: FR-019

### AC-015 — Core has no Typer dependency

- Given: the installed package
- When: tests import scanner, parsers, graph, and analysis
- Then: those modules do not import `typer` or `daygent.cli`
- Related requirements: FR-002

### AC-016 — Lineage API direction

- Given: an in-memory graph `A → B → C`
- When: `get_descendants(A)` and `get_ancestors(C)` are called
- Then: descendants of A include B and C; ancestors of C include B and A
- Related requirements: FR-022, BR-001

### AC-017 — Deterministic rescan

- Given: unchanged fixtures
- When: scan is run twice
- Then: `nodes` and `edges` JSON are identical (timestamp may differ)
- Related requirements: FR-010

### AC-018 — Missing scan artifact

- Given: no `.daygent/graph.json`
- When: `daygent graph` or `daygent impact x` runs
- Then: a clear error tells the user to run `daygent scan`
- Related requirements: FR-020, FR-021

---

## 21. Open Questions and Decisions

| ID | Question | Why It Matters | Owner | Blocking? | Status |
|----|----------|----------------|-------|-----------|--------|
| Q-001 | SPDX license (MIT vs Apache-2.0 vs other) | LICENSE, CONTRIBUTING, PyPI | Product owner | Yes before public tag | Provisional MIT for implementation; confirm before first public package tag |
| Q-002 | PyPI publish in v0.1 vs GitHub-only | Rollout | Product owner | No for local package | GitHub-first development; PyPI is a separate release issue |
| Q-003 | Numeric performance SLO | NFR-005 | Product owner | No | Deferred until before public release |
| Q-004 | Confirm `daygent.yml` schema (brief truncated at `exc`) | Config FR | Product owner | No | Confirmed: `include`, `exclude`, `output` |
| Q-005 | Windows official support | NFR-011 | Product owner | No | Confirmed: cross-platform paths via pathlib |
| Q-006 | Scan exit code when warnings exist | CI usage | Product owner | No | Confirmed: warnings do not fail the scan; exit code stays 0 |
| Q-007 | Commit `.daygent/` in consumer repos | Artifact policy | Product owner | No | Confirmed: default gitignore `.daygent/` |
| Q-008 | Official product owners / maintainers | Document control | Product owner | No | Deferred until before public release |
| Q-009 | Adoption metric targets | Success measures | Product owner | No | Deferred until before public release |

Resolved in conversation:

| ID | Decision |
|----|----------|
| D-001 | v0.1 includes Python, SQL, dbt, FastAPI, LangGraph, LLM, embeddings, vector stores, and basic HTTP detection — not stubs |
| D-002 | Single convention: upstream → downstream consumer everywhere; impact walks with arrows; FastAPI uses `function → route` + `invoked_by` |
| D-003 | Local-first; no web UI in v0.1 |
| D-004 | No production system access; dbt is never auto-run |
| D-005 | `daygent.yml` keys are `include`, `exclude`, `output` |
| D-006 | Parse warnings do not fail the scan; exit code stays 0; no `--strict` in v0.1 |
| D-007 | Stable typed node IDs (e.g. `dbt_model:stg_users`, `api_route:POST:/recommend`) |
| D-008 | SQL tables merge by normalized name; skip files larger than 10 MiB |
| D-009 | No telemetry; pathlib; English-only CLI |
| D-010 | MIT is the provisional license; confirm before the first public package tag |
| D-011 | GitHub-first development; PyPI, maintainers, and SLOs are separate from implementation |

---

## 22. Risks

| Risk | Probability | Impact | Mitigation | Owner |
|------|-------------|--------|------------|-------|
| Heuristic AI/HTTP detection false positives | Medium | Medium | Confidence levels; fixtures; prefer omit | Engineering |
| sqlglot dialect gaps | Medium | Medium | Per-file try/except; warnings | Engineering |
| Graph direction bugs in a parser | Medium | High | Shared tests asserting FR-009 on mixed fixtures | Engineering |
| Unstable node IDs → noisy JSON diffs | Medium | Medium | Documented ID scheme + sort | Engineering |
| `daygent` name taken on PyPI | Low | Medium | Rename extra / occupy name early | Owner |
| Scope too large for a solid v0.1 | Medium | High | Golden fixtures per parser; parsers isolated | Engineering |
| Scanning untrusted repos (zip bombs, huge files) | Low | Medium | Size cap; no exec/import of target code | Engineering |
| dbt manifest vs source conflict | Low | Medium | Evidence tags; files always parsed | Engineering |

---

## 23. Delivery Phases

### MVP (v0.1)

1. Packaging: `pyproject.toml`, `src/daygent`, CLI entry, LICENSE placeholder once Q-001 resolved (use `[DECISION NEEDED]` file only if owner picks; otherwise Apache/MIT decision before first public tag).
2. Domain models + graph store (`graph.json`) + ignore/file walk.
3. Parser contract + Python (modules, functions, imports, local calls).
4. FastAPI routes + `invoked_by`.
5. sqlglot SQL lineage + CTE rule.
6. dbt `ref`/`source` without manifest; optional manifest merge.
7. AI parser: LangGraph, LLM, embeddings, vector stores.
8. HTTP literal host detector (pluggable registry).
9. CLI: `scan`, `graph` (`--json`, `--mermaid`), `impact` (`--depth`, `--json`).
10. Core lineage/impact APIs + pytest fixtures/golden tests.
11. README / CONTRIBUTING for a real OSS repo.

### Post-MVP

- FastAPI + web UI on the same core.
- `--strict` scan, incremental scan, performance SLOs.
- Airflow, Dagster, Spark, Terraform, Kafka, warehouse-native, TypeScript parsers.
- Richer HTTP/SDK provider plugins.
- Optional committed graph in CI diffs.
- Ambiguous-node interactive picker.

### Future considerations

- Hosted multi-tenant service (would introduce auth — out of scope until explicitly designed).
- LLM-assisted summaries with edges remaining deterministic.
- Runtime/open-lineage merge vs static-only.

---

## 24. Definition of Done

v0.1 is done when:

- `pip install` from the repo (editable or sdist) provides `daygent`.
- `scan`, `graph`, `impact` behave as AC-001–AC-018 on fixtures.
- Core packages have no Typer dependency.
- Graph convention tests lock D-002.
- One bad file cannot abort a scan.
- `graph.json` is canonical aside from timestamp.
- README documents install, the three commands, graph convention, local-first constraint, and `.daygent/`.
- CONTRIBUTING documents how to add a parser.
- Tests run in CI `[ASSUMPTION]`: GitHub Actions on 3.11+.
- No parser imports or executes the scanned project.
- Open questions Q-001/Q-002 are resolved before a public PyPI release; they do not block implementing against this PRD.

---

## Quality checklist (author)

- Requirements are testable and IDed.
- Local-only “role” model is explicit (no fake RBAC).
- Failure/recovery documented.
- MVP vs later parsers separated; v0.1 detector set is full, not stubbed.
- ACs trace to FRs.
- Open questions are not written as if decided, except conversation decisions D-001–D-004.
- Repo is empty: stack is as specified by the user, not invented beyond that.
- Terminology: **upstream → downstream consumer** used consistently; `invoked_by` for handlers.
