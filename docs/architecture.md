# mnemo architecture (the gist)

A short tour of how mnemo is put together. For the full v1.0 design
rationale see
[docs/plans/2026-05-09-mnemo-design.md](plans/2026-05-09-mnemo-design.md);
for the v1.1 (Beyond Claude Code) design see
[docs/plans/2026-05-10-mnemo-v1.1-design.md](plans/2026-05-10-mnemo-v1.1-design.md).
The HTTP contract is at [docs/protocol.md](protocol.md).

## Three tiers (v1.1: clients + daemon + store)

```
                 Local machine only
+-------------------------------------------------------------+
|  Claude Code plugin   VS Code extension   mnemo-middleware  |
|  (existing, hooks)   (.vsix, @mnemo chat)  (PyPI, OpenAI/   |
|                                             Anthropic/...)  |
|        \_____________________|_________________/            |
|                              |                              |
|                  HTTP, all under /v1/...                    |
|                              v                              |
|             mnemo daemon (FastAPI, 127.0.0.1:7373)          |
|                              |                              |
|                              v                              |
|              SQLite + sqlite-vec (~/.claude/mnemo/)         |
+-------------------------------------------------------------+
```

**Plugin** (Claude Code reference adapter) is markdown skills + small
bash/PowerShell hooks. Zero Python dependencies in the plugin itself.

**VS Code extension** (`extensions/vscode/`) registers a `@mnemo`
chat participant + sidebar + status bar. Talks HTTP to the daemon.

**SDK middleware** (`clients/middleware-py/`) is a PyPI package that
patches OpenAI / Anthropic / Google / Ollama SDK clients to inject
retrieval as a system message before the model call. Always additive
(daemon down -> empty injection -> model call proceeds).

**Daemon** is a Python FastAPI process. It owns the store, the
embedding model, and the file watcher. Public HTTP surface lives
under `/v1/...` with an OpenAPI spec at `/v1/openapi.json`.

**Store** is two SQLite databases: `mnemo.db` (relational) and a
`vec_chunks` virtual table (sqlite-vec) for embeddings. Plus the
model cache and runtime logs. Everything sits under `~/.claude/mnemo/`.

## Why local-first

| Concern | Local-first | Cloud DB |
|---|---|---|
| Privacy | memory never leaves the machine | requires trust |
| Latency | < 50 ms / query | 100-500 ms |
| Offline | works | doesn't |
| Auth | none | API keys, accounts |
| Cost | free | recurring |

Local wins every axis for this workload.

## Data model

- **Nodes** — every memory entry is a node.
  Types: `memory_user` / `memory_feedback` / `memory_project` /
  `memory_reference` / `project_doc` / `plan_doc` / `session_summary`.
- **Edges** — typed relationships between nodes.
  Relations: `applies_to` / `derived_from` / `contradicts` / `supersedes` /
  `mentions` / `co_occurs_with`.
- **Sources** — registered ingestion roots: a `claude_md` file or a
  `memory_dir` / `plan_dir` directory.
- **Queries** — audit log of every retrieval; used to spot bad recall and
  tune.

## Ingestion (Scope B by default)

`mnemo init` walks `~/.claude/` for:

1. The global `~/.claude/CLAUDE.md` (kind = `claude_md`).
2. Each `~/.claude/projects/<key>/memory/` directory (kind = `memory_dir`,
   project_key = `<key>`).

`mnemo source add` lets you register additional roots: a repo's
`CLAUDE.md`, a `docs/plans/` directory, etc.

Reindex (`mnemo reindex`) is hash-gated and idempotent: rerunning on
unchanged files is a no-op.

## Retrieval — Hybrid Graph-RAG

For each query:

```
prompt --> intent classify --> vector top-k chunks
                            --> graph proximity from candidates
                            --> 5-term scoring
                            --> per-node dedup (best chunk wins)
                            --> top-k
                            --> compress to budget tokens
                            --> emit citations [mnemo:<id>]
```

The 5-term score:

```
score = a*vector + b*graph + c*recency + d*type_priority + e*project_scope
```

Defaults: `a=0.45, b=0.20, c=0.15, d=0.15, e=0.05`. Recency uses a
90-day exponential decay. Type priority comes from the intent
classification.

## Embedding

`sentence-transformers/all-MiniLM-L6-v2`:

- 22 MB on disk
- 384-dim embeddings
- ~5 ms / chunk on CPU
- Apache-2.0 license

The model is downloaded to `~/.claude/mnemo/cache/` on first use. To swap:
edit `mnemo.embed.DEFAULT_MODEL` and reindex. The vec table dim is
hard-coded to 384 in the schema; bumping it requires a migration.

## Plugin surface

- **Hooks**: `SessionStart` (memory map), `UserPromptSubmit` (auto-inject
  retrieval), `PostToolUse` matched on `Edit|Write` (async reindex on
  memory-shaped paths).
- **Slash commands**: `/mnemo-query`, `/mnemo-add`, `/mnemo-reindex`,
  `/mnemo-ui`, `/mnemo-status`, `/mnemo-hooks`, `/mnemo-show`.
- **Skills**: 7 workflow skills (rigid: `implement-platform`, `debug`,
  `query-knowledge`; flexible: `refactor`, `add-knowledge`,
  `onboard-project`, `review`).

## UI

Local web UI at `http://127.0.0.1:7373/` — search, graph view (Cytoscape),
node editor, source registry, audit log, settings. No Node toolchain:
FastAPI serves Jinja2 templates; HTMX handles partial updates; Alpine.js
handles tiny client state. CSS is hand-rolled.

## What we deliberately don't do

- Cloud sync, multi-user, account systems. Local-first is the point.
- LLM-based intent classification. Regex is deterministic, microsecond,
  and good enough.
- LLM-inferred edges. Graph edges come from frontmatter (declared) and
  co-occurrence (learned). No black-box reasoning.
- General-purpose document Q&A. mnemo is tuned for typed, short memory;
  arbitrary PDFs are out of scope.

## Where to look in the code

| You want to understand... | Read... |
|---|---|
| Schema | `daemon/mnemo/store.py` (top of file) |
| How files become nodes | `daemon/mnemo/ingest.py::reindex` |
| Chunking strategy | `daemon/mnemo/embed.py::chunk_body` |
| Retrieval algorithm | `daemon/mnemo/retrieve.py::query` |
| Scoring weights | `daemon/mnemo/retrieve.py` (top) |
| Intent regex | `daemon/mnemo/intent.py::INTENT_PATTERNS` |
| Plugin manifest | `.claude-plugin/plugin.json` |
| Hook contracts | `hooks/*.sh`, `hooks/*.ps1` |

## Page-shell contract (C1, v4.0)

The UI design system has **one token layer** (`app.css :root`) and
**exactly two page-shell modes**. There is no third. A grep guard test
(`daemon/tests/unit/test_design_system_contract.py`) is the contract's
teeth -- it makes the v3.2-class layout bug (the one that cost a multi-
round `/chat` saga, gotcha 35) impossible to reintroduce silently.

**Token layer.** Every primitive value lives in `:root` exactly once
and consumers use `var(--...)`: `--topbar-h` (65px; every full-window
shell is `calc(100vh - var(--topbar-h))`), `--content-max` (1600px
centered cap), `--page-pad` (2rem), `--radius-pill` (999px),
`--accent-fg` / `--warn-fg` (text on accent/warn fills),
`--measure*` (reading widths). No raw `65px`/`1600px`/`999px`/
`#06201e`/`#1a0f0c` literal may appear outside `:root`. This mirrors
the proven `palette.py` single-source model.

**Mode 1 -- Centered.** A normal content page. Override only
`{% block content %}`; **never emit a `<main>`** (base.html already
provides the single `<main>`). Inherits `main { max-width:
var(--content-max); margin: 2rem auto; padding: 0 var(--page-pad)
4rem }`. Examples: `/settings`, `/nodes-page`, `/dashboard`.

**Mode 2 -- Full-window.** An app-shell page (graph, chat). Override
`{% block layout %}`; emit **exactly one** `<main class="full">` then
one root `<section class="shell-NAME">` whose height is
`calc(100vh - var(--topbar-h))`. The inner regions scroll internally;
the document does not. Reference: `graph.html` `.nebula-shell`;
`chat.html` `.mn` conforms. A **nested second `<main>`** inside the
full one inherits the centered `main {}` rule and, as a grid item,
shrink-to-fit-centres instead of filling -- that was the v3.2 bug; use
a `<div>` for inner regions (gotcha 35).

**Shared primitives** (`.mnem-working`, `.load-older`/`.lo-pill`/
`.lo-dot`, `.link-button`, `.btn-pill`, `@keyframes mnem-bob`) have
their single canonical definition in `app.css`; no page template may
redefine them (they were duplicated + divergent across chat.html/
base.html pre-v4.0).

**No-overflow rule (v4.3.1).** A CSS `grid`/`flex` container whose
descendants include long `white-space:nowrap` text (e.g. the audit
`.hit-desc`) MUST use `grid-template-columns: minmax(0, …)` (or
`min-width:0` on the flex/grid items) -- an implicit `auto` track
sizes to *max-content* and the nowrap text propagates up the chain
and blows the page width; `overflow:hidden;text-overflow:ellipsis` is
inert without a width-constrained ancestor. Same
`test_design_system_contract.py` guard
(`test_audit_grids_constrain_long_content`) is the teeth.

**Breakpoint scale (C1.R, v4.4).** Responsiveness is a C1 contract,
not per-page media-query hacks. The breakpoint scale lives once in
`app.css :root`, exactly like the colour tokens:

| Token     | Value   | ≈ px @16px root | Role                        |
|-----------|---------|-----------------|-----------------------------|
| `--bp-sm` | `40rem` | ~640px          | tight / mobile              |
| `--bp-md` | `60rem` | ~960px          | the primary collapse point  |
| `--bp-lg` | `80rem` | ~1280px         | wide desktop                |

CSS `@media` cannot take `var()`, so the *rem literal in every width
media query IS the single-source contract* —
`tests/unit/test_responsive_contract.py` forbids any `px` width
literal or any rem value outside the 3-token set. The 15 ad-hoc
literals were consolidated to their natural cluster point `--bp-md`
(documented px↔rem map):

| Was            | Now (token)        | Affects                              |
|----------------|--------------------|--------------------------------------|
| `max-width:1100px` | `60rem` (`--bp-md`) | centred-page `main` padding tighten |
| `max-width:800px`  | `60rem` (`--bp-md`) | `.dash-row` 2→1 col                  |
| `max-width:980px`  | `60rem` (`--bp-md`) | `.dash-row.split-2` / `.node-detail-grid` |
| `max-width:1080px` | `60rem` (`--bp-md`) | `.dash-row.split-3`                  |
| `min-width:900px`  | `60rem` (`--bp-md`) | `.code-columns` / `.ego-network`     |
| `min-width:1000px` | `60rem` (`--bp-md`) | `.project-columns`                   |

Desktop pixel-parity at 1280/1440 is preserved by construction: every
`max-width:60rem` stays inactive and every `min-width:60rem` stays
active at those widths, identical to the pre-consolidation 800–1100 /
900–1000 px behaviour (live-verified via `matchMedia`, gotcha-19
screenshot-independent). `prefers-reduced-motion` / `prefers-color-scheme`
are feature queries, not width literals, and are unaffected.
