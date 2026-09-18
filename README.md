# Daygent

Local-first static analysis for modern data + AI repositories.

Daygent reads source files only. It does not connect to warehouses, APIs, vector
databases, or cloud accounts, and it does not execute scanned code.

It is built to answer questions like:

- What depends on this table or dbt model?
- Which API endpoints could be affected by changing this Python function?
- What is downstream of this dataset?
- Which agents/services depend on this component?

## How it works

```text
Repository
   ↓
Scanner
   ↓
Technology Parsers
   ↓
Nodes + Edges
   ↓
Graph Builder
   ↓
Unified Dependency Graph
   ↓
.daygent/graph.json
   ↓
Lineage + Impact Analysis
```

The scanner walks the repo, dispatches pluggable parsers, and the graph builder
merges their nodes and edges into one artifact: `.daygent/graph.json`.

Lineage and impact analysis read that graph. Technology-specific parsers are
next, starting with Python and SQL.

## Graph convention

**A → B means B depends on A.** Impact analysis walks with the arrows.

```text
raw_users
   ↓
stg_users
   ↓
user_features
   ↓
recommend()
   ↓
POST /recommend
```

Changing `raw_users` can affect everything below it, including `POST /recommend`.

## v0.1 foundation

In place today:

- installable `daygent` CLI
- Node / Edge / Graph models
- stable typed IDs
- confidence levels (`high` > `medium` > `low`)
- deterministic `.daygent/graph.json` storage
- repository scanner
- optional `daygent.yml` (`include`, `exclude`, `output`)
- pluggable parser interface
- graph builder
- GitHub Actions CI for Python 3.11 and 3.12

Not in this slice: Python/SQL/dbt/FastAPI/LangGraph parsers, graph display, or
impact CLI output.

## Install

```bash
pip install -e ".[dev]"
daygent --help
daygent scan .
```

Requires Python 3.11+. `daygent scan` writes `.daygent/graph.json`. `graph` and
`impact` commands land after the parser work.

Optional config at `daygent.yml` or `daygent.yaml`:

```yaml
include:
  - src/**
  - models/**
exclude:
  - migrations/**
  - generated/**
output: .daygent/graph.json
```

## License

MIT. That is the working license for GitHub-first development. Confirm it in
[issue #2](https://github.com/emyrael/daygent/issues/2) before the first public
package tag. PyPI publishing is tracked in
[issue #22](https://github.com/emyrael/daygent/issues/22).
