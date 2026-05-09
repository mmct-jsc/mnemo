# mnemo architecture (the gist)

A short tour of how mnemo is put together. For the full design rationale,
see [docs/plans/2026-05-09-mnemo-design.md](plans/2026-05-09-mnemo-design.md).

## Three tiers

```
[Claude Code plugin]   markdown skills + hooks + slash commands
        |
        v   localhost HTTP / stdin pipe
[mnemo daemon]         Python + FastAPI on 127.0.0.1:7373
        |
        v
[mnemo store]          SQLite + sqlite-vec  (~/.claude/mnemo/)
```

**Plugin** is markdown + small bash/PowerShell hooks. Zero Python
dependencies in the plugin itself. The hooks shell out to the `mnemo`
CLI.

**Daemon** is a Python process. It owns the store, the embedding model, and
the file watcher. Started on demand via `mnemo daemon start`.

**Store** is two SQLite databases: `mnemo.db` (relational) and a
`vec_chunks` virtual table (sqlite-vec) for embeddings. Plus the model
cache and runtime logs. Everything sits under `~/.claude/mnemo/`.

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
