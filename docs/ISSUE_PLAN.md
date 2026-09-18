# Daygent v0.1 Issue Plan

- Source PRD: `docs/PRD.md`
- Generated: 2026-09-18
- Target repo: [emyrael/daygent](https://github.com/emyrael/daygent)
- Milestone: [v0.1](https://github.com/emyrael/daygent/milestone/1)
- License for implementation: MIT (provisional; confirm before public tag in #2)

Graph convention (locked): **A → B means B depends on A**. Impact and Mermaid walk with the arrows.

## Issue table

| Key | Number | Title | Type | Priority | Estimate | URL |
|-----|--------|-------|------|----------|----------|-----|
| E1 | #1 | Epic: Daygent v0.1 local-first lineage CLI | Epic | P1 | XL | https://github.com/emyrael/daygent/issues/1 |
| D1 | #2 | Confirm MIT license before first public package tag | Decision | P2 | XS | https://github.com/emyrael/daygent/issues/2 |
| F1 | #3 | Bootstrap installable src layout, MIT license, and pytest harness | Chore | P1 | M | https://github.com/emyrael/daygent/issues/3 |
| F2 | #4 | Unified Node, Edge, and Graph domain models with stable typed IDs | Feature | P1 | M | https://github.com/emyrael/daygent/issues/4 |
| F3 | #5 | Deterministic local graph.json store with atomic writes | Feature | P1 | S | https://github.com/emyrael/daygent/issues/5 |
| F4 | #6 | Repository scanner, parser contract, and daygent.yml configuration | Feature | P1 | M | https://github.com/emyrael/daygent/issues/6 |
| T2 | #7 | Add GitHub Actions CI to run pytest on Python 3.11+ | Testing | P1 | S | https://github.com/emyrael/daygent/issues/7 |
| F5 | #8 | Graph builder that merges parser results and scan warnings | Feature | P1 | S | https://github.com/emyrael/daygent/issues/8 |
| F13 | #9 | Core lineage and impact APIs independent of the CLI | Feature | P1 | M | https://github.com/emyrael/daygent/issues/9 |
| F6 | #10 | Python AST parser for modules, functions, imports, and local calls | Feature | P1 | L | https://github.com/emyrael/daygent/issues/10 |
| F8 | #11 | SQL lineage parser using sqlglot with CTE handling | Feature | P1 | L | https://github.com/emyrael/daygent/issues/11 |
| F7 | #12 | Detect FastAPI routes, handlers, and include_router relationships | Feature | P1 | M | https://github.com/emyrael/daygent/issues/12 |
| F9 | #13 | Parse dbt ref() and source() lineage without running dbt | Feature | P1 | L | https://github.com/emyrael/daygent/issues/13 |
| F10 | #14 | Detect LangGraph nodes and edges from Python AST | Feature | P1 | M | https://github.com/emyrael/daygent/issues/14 |
| F11 | #15 | Detect LLM, embedding, and vector-store constructors statically | Feature | P1 | L | https://github.com/emyrael/daygent/issues/15 |
| F12 | #16 | Detect statically obvious external HTTP hosts | Feature | P1 | M | https://github.com/emyrael/daygent/issues/16 |
| F14 | #17 | Implement daygent scan CLI with summary, warnings, and --verbose | Feature | P1 | M | https://github.com/emyrael/daygent/issues/17 |
| F15 | #18 | Implement daygent graph with --json and --mermaid | Feature | P1 | M | https://github.com/emyrael/daygent/issues/18 |
| F16 | #19 | Implement daygent impact with depth, JSON, and ambiguous-name candidates | Feature | P1 | M | https://github.com/emyrael/daygent/issues/19 |
| T1 | #20 | Add golden mixed-stack fixtures locking graph convention and AC-001–AC-018 | Testing | P1 | L | https://github.com/emyrael/daygent/issues/20 |
| DOC1 | #21 | Write README and CONTRIBUTING for GitHub-first v0.1 | Documentation | P1 | M | https://github.com/emyrael/daygent/issues/21 |
| R1 | #22 | PyPI publishing checklist after license confirmation | Release | P2 | M | https://github.com/emyrael/daygent/issues/22 |

## Dependency graph

```text
#2 D1 ----------------------------> #22 R1
#3 F1 -> #4 F2 -> #5 F3 -> #8 F5 -> #17 F14 -> #18 F15 -> #20 T1 -> #21 DOC1 -> #22 R1
                 \          ^  \-> #9 F13 ----------> #19 F16 ↗
                  \-> #6 F4 ↗
                         \-> #10 F6 -> #12 F7  ----------------^
                         \         \-> #14 F10 -------------^
                         \         \-> #15 F11 -------------^
                         \         \-> #16 F12 -------------^
                         \-> #11 F8 -> #13 F9 --------------^
#3 F1 -> #7 T2 -----------------------------------------------> #22 R1
```

## Critical path

`#3 → #4 → #6 → #8 → #10 → #12 → #17 → #18/#19 → #20`

Parser work after #10/#11 (FastAPI, dbt, LangGraph, AI, HTTP) can run in parallel.

## Parallel workstreams

1. **Foundation:** #3, #4, #5, #6, #8
2. **Python/AI stack:** #10, #12, #14, #15, #16
3. **Data stack:** #11, #13
4. **Analysis + CLI:** #9, #17, #18, #19
5. **Hardening:** #7 (early), #20, #21
6. **Public release (not blocking implementation):** #2, #22

## Unresolved decisions (non-blocking)

- Confirm MIT vs another SPDX license before the first public tag (#2).
- PyPI publishing, maintainer metadata, performance SLOs, adoption targets (#22).

## Implementation started

Local work has begun on the critical-path foundation: package (#3), domain models (#4), graph store (#5), scanner/parser contract/config (#6), graph builder (#8), and CI workflow (#7). Technology parsers and graph/impact CLI display remain open.
