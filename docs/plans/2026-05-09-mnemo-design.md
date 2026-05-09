# mnemo — Knowledge Memory System Design

**Status:** Approved 2026-05-09
**Author:** mnemo contributors
**Repository root:** `D:\Repository\knowledge-base\`

---

## 1. Vision

Claude memory today is scattered across `~/.claude/projects/<project>/memory/*.md`, the global `~/.claude/CLAUDE.md`, per-repo `CLAUDE.md` files, and design docs under `docs/plans/`. As you work across projects, the same lessons get re-discovered, the same feedback gets re-given, the same architectural decisions get re-derived from scratch. There is no cross-project memory; there is no automatic recall; there is no token-budgeted way to bring relevant memory into a Claude session.

`mnemo` fixes this by aggregating that scattered memory into a typed graph, indexing it with embeddings, and shipping budget-capped retrieval results back to Claude via Claude Code plugin hooks. It runs entirely locally, ships as a single plugin, and updates incrementally as you work.

## 2. Goals & non-goals

### Goals

- **G1.** Aggregate every Claude memory file under `~/.claude/projects/*/memory/`, the global `~/.claude/CLAUDE.md`, repo-root `CLAUDE.md` files, and `docs/plans/` design docs into one queryable index.
- **G2.** Surface the most relevant memory to Claude on every prompt, capped at ≤ 800 tokens, citation-tagged.
- **G3.** Update incrementally as memory files are written or edited. No manual reindex required for normal use.
- **G4.** Give Claude a deterministic seven-step set of workflows that always pull project-specific memory at every phase.
- **G5.** Provide a local web UI for browsing the graph, searching, and editing nodes.
- **G6.** Ship as a community-installable Claude Code plugin with one-command install on Windows / macOS / Linux.
- **G7.** Optimize for token cost — never dump full files; rank, compress, cite.
- **G8.** No `Co-Authored-By` trailers on commits. Ever.

### Non-goals

- **NG1.** Cloud sync, multi-user collab, account systems. Local-first.
- **NG2.** Real-time multi-process write coordination. mnemo daemon is single-writer.
- **NG3.** Re-implementing existing skills like `superpowers:test-driven-development` or `superpowers:writing-plans`. mnemo's workflow skills *call* those when relevant.
- **NG4.** General-purpose document Q&A. mnemo is tuned for typed, structured memory; arbitrary PDFs are out of scope (use a separate RAG tool).
- **NG5.** Replacing the existing memory system. mnemo *reads* the same files Claude already writes; it does not change the on-disk format.

## 3. Architecture

### 3.1 Three-tier split

```
[Claude Code plugin]   markdown skills + hooks + slash commands  (lightweight)
        |
        v   HTTP localhost (or stdin/stdout for hooks)
[mnemo daemon]         Python + FastAPI on 127.0.0.1:7373
        |
        v
[mnemo store]          SQLite + sqlite-vec  (under ~/.claude/mnemo/)
```

**Tier 1 — plugin** is markdown skills, bash/PowerShell hook scripts, and slash command definitions. It contains no heavy logic. It calls into the daemon over localhost HTTP (or invokes the `mnemo` CLI for hook scripts that need exit codes).

**Tier 2 — daemon** is a Python process running FastAPI. It owns the index. It exposes `/query`, `/add`, `/reindex`, `/sources`, `/nodes/<id>`, `/graph`, `/ui` endpoints. It also runs the file watcher for incremental reindex.

**Tier 3 — store** is two SQLite files under `~/.claude/mnemo/`: `mnemo.db` (relational data) and `mnemo.vec` (sqlite-vec embeddings). Plus a `cache/` subdir for the MiniLM model.

### 3.2 Why local-first

| Concern | Local-first (mnemo) | Cloud DB |
|---|---|---|
| Privacy | Memory never leaves the machine | Requires trust in service |
| Latency | < 50 ms per query | 100-500 ms |
| Offline | Works on plane / no wifi | Doesn't |
| Auth | Zero auth surface | Account, API keys |
| Cost | Free forever | Recurring |

Local-first wins on every axis for this workload.

### 3.3 Why Python for the daemon

The Python ecosystem for embedding + chunking + vector search is mature and free: `sentence-transformers`, `sqlite-vec`, `faiss` (optional), `pydantic`, `fastapi`, `httpx`. The plugin layer (markdown + bash/PowerShell) is language-agnostic — only the daemon is Python.

Node.js was considered. Rejected because:
- `sentence-transformers` has no first-class Node port (transformers.js exists but is 5-10x slower CPU)
- `sqlite-vec` Node bindings are early
- The user already runs Python tooling (Pixellab pipeline)

### 3.4 Why FastAPI + HTMX + Alpine + Cytoscape (no Node toolchain)

The UI is utilitarian: search, list, edit, graph view. A Node SPA (React/Vue/Svelte) doubles install complexity (`npm install`, build step) for marginal UX gain. FastAPI serves Jinja2 templates; HTMX handles partial updates; Alpine.js handles tiny client state; Cytoscape.js (single CDN script) handles the graph canvas. Single Python process serves everything. One install command, one runtime.

## 4. Storage

### 4.1 SQLite schema

```sql
-- Nodes: every memory entry is a node.
CREATE TABLE nodes (
  id              TEXT PRIMARY KEY,            -- ULID
  type            TEXT NOT NULL,               -- see types list below
  name            TEXT NOT NULL,               -- frontmatter `name` or filename stem
  description     TEXT,                        -- frontmatter `description` (one-liner)
  body            TEXT NOT NULL,               -- markdown body
  source_path     TEXT NOT NULL,               -- absolute path on disk
  source_kind     TEXT NOT NULL,               -- 'memory_dir' | 'claude_md' | 'plan_dir'
  project_key     TEXT,                        -- e.g. 'D--Repository-aibox-prod-all'
  frontmatter_json TEXT,                       -- raw frontmatter as JSON
  hash            TEXT NOT NULL,               -- sha256 of file content (for change detection)
  created_at      INTEGER NOT NULL,            -- unix epoch
  updated_at      INTEGER NOT NULL
);

CREATE INDEX idx_nodes_type ON nodes(type);
CREATE INDEX idx_nodes_project ON nodes(project_key);
CREATE INDEX idx_nodes_updated ON nodes(updated_at DESC);

-- Edges: typed relationships between nodes.
CREATE TABLE edges (
  src_id          TEXT NOT NULL,
  dst_id          TEXT NOT NULL,
  relation        TEXT NOT NULL,               -- see relations list below
  weight          REAL NOT NULL DEFAULT 1.0,
  created_at      INTEGER NOT NULL,
  source          TEXT NOT NULL,               -- 'inferred' | 'user' | 'frontmatter'
  PRIMARY KEY (src_id, dst_id, relation),
  FOREIGN KEY (src_id) REFERENCES nodes(id) ON DELETE CASCADE,
  FOREIGN KEY (dst_id) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE INDEX idx_edges_src ON edges(src_id);
CREATE INDEX idx_edges_dst ON edges(dst_id);
CREATE INDEX idx_edges_rel ON edges(relation);

-- Sources: registered ingestion paths.
CREATE TABLE sources (
  path            TEXT PRIMARY KEY,
  kind            TEXT NOT NULL,
  project_key     TEXT,
  last_indexed_at INTEGER,
  enabled         INTEGER NOT NULL DEFAULT 1
);

-- Queries: audit trail for retrieval.
CREATE TABLE queries (
  id              TEXT PRIMARY KEY,
  prompt          TEXT NOT NULL,
  intent_tags     TEXT,                        -- JSON array
  retrieved_ids   TEXT,                        -- JSON array of node IDs
  scores          TEXT,                        -- JSON object
  ts              INTEGER NOT NULL
);

-- Schema version (for migrations).
CREATE TABLE schema_version (
  version INTEGER NOT NULL
);
INSERT INTO schema_version VALUES (1);
```

#### Node types

- `memory_user` — user profile, role, preferences (from `user_*.md`)
- `memory_feedback` — corrections and validated approaches (from `feedback_*.md`)
- `memory_project` — project facts, decisions, infrastructure (from `project_*.md`)
- `memory_reference` — external system pointers (from `reference_*.md`)
- `project_doc` — repo-root `CLAUDE.md` files
- `plan_doc` — design docs in `docs/plans/`
- `session_summary` — opt-in (Scope C); summaries of past sessions

#### Edge relations

- `applies_to` — feedback X applies to project Y
- `derived_from` — node Y was derived from node X (e.g., decision from analysis)
- `contradicts` — node X contradicts node Y
- `supersedes` — node X supersedes node Y (newer overrides older)
- `mentions` — node X mentions concept/file/system in node Y
- `co_occurs_with` — two nodes frequently retrieved together (learned, decays)

### 4.2 Embeddings (sqlite-vec)

```sql
-- Separate sqlite-vec virtual table.
CREATE VIRTUAL TABLE vec_chunks USING vec0(
  node_id TEXT,
  chunk_idx INTEGER,
  embedding FLOAT[384]                          -- MiniLM-L6-v2 dim
);
```

Chunks are **per node × per ~512 tokens**, with 50-token overlap. Most memory files are short (200-1500 tokens) and produce 1-3 chunks. Long design docs may produce 10+ chunks.

### 4.3 File layout

```
~/.claude/mnemo/
├── mnemo.db              SQLite (nodes, edges, sources, queries)
├── mnemo.vec             sqlite-vec virtual table (embeddings)
├── cache/
│   └── all-MiniLM-L6-v2/  HuggingFace model cache (22 MB)
├── logs/
│   └── daemon.log
└── pid                    daemon process ID file
```

`~/.claude/mnemo/` is the *runtime* directory. The repo (`D:\Repository\knowledge-base\`) holds source code only.

## 5. Ingestion

### 5.1 Sources (Scope B — approved)

Default ingestion targets:

1. `~/.claude/projects/<project>/memory/*.md` — typed memory entries (43 files at design time)
2. `~/.claude/CLAUDE.md` — global memory (504 lines at design time)
3. Repo-root `CLAUDE.md` files — added explicitly via `mnemo add-source <repo-path>`
4. `<repo>/docs/plans/*.md` — added when source is registered

Out of default scope (opt-in via `mnemo add-source --kind=transcripts`):
- Session transcripts under `~/.claude/transcripts/`
- Repo commit messages

### 5.2 File watcher

The daemon uses `watchfiles` (fast, cross-platform) to watch registered source paths. On every `create | modify | delete` event:
- Compute new hash
- If hash differs from stored hash → re-parse, re-embed, replace node
- If file deleted → mark node soft-deleted (kept for 7 days, then purged)

Watcher is debounced (200 ms) to coalesce rapid edits.

### 5.3 YAML frontmatter parsing

Existing memory files use this format:

```markdown
---
name: feedback-commit-style
description: User wants commits without Co-Authored-By trailers
type: feedback
originSessionId: 885ee0fa-...
---
**Hard rule for this repo:** ...
```

The parser:
- Reads the file
- If first line is `---`, parse YAML up to the next `---`
- Body = everything after the closing `---`
- Defaults: if no frontmatter, infer `name` from filename stem, `type` from filename prefix (`user_*` → `memory_user`), `description` from first heading or first 100 chars

### 5.4 Chunking

```python
def chunk(body: str, max_tokens: int = 512, overlap: int = 50) -> list[str]:
    # Split on markdown headings (##, ###) first; fall back to paragraph; fall back to fixed-window.
    # Always respect heading boundaries when possible.
    ...
```

Chunks are tokenized with the MiniLM tokenizer (BERT-style) for accurate counting.

## 6. Retrieval — Hybrid Graph-RAG

### 6.1 Why Graph-RAG over pure vector RAG

**Pure vector RAG** answers "which text chunks are semantically similar to the prompt?" That works for FAQ-style retrieval but misses:
- *Type filtering*: "Pull feedback before pulling project facts"
- *Structural recency*: "If a `supersedes` edge exists, prefer the newer node"
- *Project scoping*: "Boost nodes from the active project's directory"
- *Cross-project transfer*: "Hard-cooldown technique used in petrolimex applies to any IoU dedup"

**Graph-RAG** captures these through typed nodes + typed edges. mnemo's hybrid: vectors find candidates, graph re-ranks by structure, recency, and project scope.

### 6.2 Algorithm

```
def query(prompt, budget_tokens=800, k=20):
    # 1. Intent classify
    intent = classify_intent(prompt)
    # tags ⊆ {debug, feedback-recall, project-context, reference, design, none}

    # 2. Vector candidates
    embedding = embed(prompt)
    candidates = vec_search(embedding, k=k, type_filter=type_priority(intent))

    # 3. Graph expansion (1-hop)
    expanded = candidates.copy()
    for node_id, score in candidates:
        for edge in edges_from(node_id, relations=['applies_to', 'co_occurs_with']):
            expanded[edge.dst_id] += score * edge.weight * 0.5

    # 4. Score
    final = []
    for node_id, vec_score in expanded.items():
        node = get_node(node_id)
        s = (
            ALPHA * vec_score +
            BETA  * graph_proximity(node_id, candidates) +
            GAMMA * recency_decay(node.updated_at) +
            DELTA * type_priority(intent).get(node.type, 0) +
            EPS   * project_scope_boost(node.project_key, current_project)
        )
        final.append((node_id, s))

    final.sort(key=lambda x: -x[1])

    # 5. Budget compress
    out = []
    used = 0
    for node_id, score in final:
        node = get_node(node_id)
        cost = tokens(node.description)
        if used + cost > budget_tokens:
            break
        out.append({"id": node_id, "type": node.type, "description": node.description, "cite": f"[mnemo:{node_id}]"})
        used += cost

    # 6. Optional: include full body for top-1 if budget remains
    if final and used + tokens(final[0].body) <= budget_tokens:
        out[0]["body"] = final[0].body
        used += tokens(final[0].body)

    return {"results": out, "tokens_used": used, "query_id": persist(...)}
```

### 6.3 Scoring weights (initial)

| Coefficient | Symbol | Initial value | Tunable via |
|---|---|---|---|
| Vector cosine | α | 0.45 | `mnemo config set scoring.alpha` |
| Graph proximity | β | 0.20 | … |
| Recency decay | γ | 0.15 | … (half-life: 90 days) |
| Type priority | δ | 0.15 | … |
| Project scope boost | ε | 0.05 | … |

These are starting values. The audit log (`queries` table) lets us tune later from real usage.

### 6.4 Token-budget compression

Default budget = 800 tokens. Strategy:
1. Top-1 description (one-liner)
2. Top-2 description
3. … until budget runs out
4. If budget remains after all descriptions, include top-1 *body*
5. Always end with citation block: `[mnemo:<id1>] [mnemo:<id2>] …`

This means even with 20 hits, the response stays bounded. Claude can ask for full bodies of specific citations on demand (`/mnemo-show <id>`).

## 7. Embeddings

### 7.1 Model: `all-MiniLM-L6-v2`

| Property | Value |
|---|---|
| Size | 22 MB |
| Embedding dim | 384 |
| Max seq length | 256 tokens |
| Inference | ~5 ms / chunk on CPU |
| License | Apache 2.0 |
| Source | `sentence-transformers` |

This model is the standard choice for "good-enough local embedding". Quality is sufficient for memory retrieval (recall@10 on similar tasks: 85-90%).

### 7.2 Upgrade path

If retrieval quality proves insufficient, swap in:
- `BAAI/bge-small-en-v1.5` (130 MB, better English quality, same 384 dim → drop-in)
- `BAAI/bge-m3` (1.4 GB, multilingual, 1024 dim → schema migration required)

The `embeddings` table stores the model name; reindexing is a single command.

## 8. Plugin surface

### 8.1 Hooks

| Event | Trigger | Behavior | Token cost |
|---|---|---|---|
| `SessionStart` | New conversation | Inject "memory map" — counts by type, top-5 recent, daemon health | ~150 tok |
| `UserPromptSubmit` | Each user message | Run `mnemo query <prompt>`, inject result block | ≤ 800 tok (configurable) |
| `PostToolUse(Edit\|Write)` | After Claude writes a memory file | Async re-embed (no token cost) | 0 |

Hooks are **opt-in via `.claude/settings.json`** but enabled by the install script. Toggle anytime: `/mnemo-hooks off`.

### 8.2 Slash commands

| Command | What it does |
|---|---|
| `/mnemo-query <text>` | Ad-hoc query, returns top-k as a chat block |
| `/mnemo-add` | Capture current insight as a new memory node (uses `mnemo:add-knowledge`) |
| `/mnemo-reindex` | Full rescan |
| `/mnemo-ui` | Open `http://127.0.0.1:7373` in default browser |
| `/mnemo-status` | Daemon health, node count, last index time |
| `/mnemo-hooks <on\|off>` | Toggle auto-injection |
| `/mnemo-show <id>` | Show full body of a specific node |

### 8.3 UI (HTMX + Alpine + Cytoscape)

Pages served by FastAPI from `daemon/mnemo/ui/`:

| Path | Purpose |
|---|---|
| `/` | Search bar; recent entries list; type/project filters |
| `/graph` | Interactive node-link diagram (Cytoscape.js); click node to expand neighbors |
| `/node/<id>` | View/edit a single node — frontmatter form + markdown body |
| `/sources` | List ingested paths with last-indexed timestamps; per-source reindex button |
| `/audit` | Query log — what fired, what was retrieved, scores |
| `/settings` | Hook toggle, token budget, embedding model, scoring weights |

UI bind: `127.0.0.1:7373` only. No auth (single-user, localhost-only).

## 9. Workflow skills

Each ships as a markdown skill in `skills/<name>/SKILL.md`. Each skill defines its phases, done-criteria, and the artifacts each phase produces. Phase order is **rigid** for the first two; **flexible** for the rest.

### 9.1 `mnemo:implement-platform` (rigid)

Full feature build. Mandatory user-approval gate between phases 3 (Design) and 4 (Decision).

| # | Phase | Output | Done when |
|---|---|---|---|
| 1 | Requirements gathering | `docs/plans/<date>-<topic>-requirements.md` | stakeholders, use cases, constraints, success criteria captured |
| 2 | Analysis | `…-analysis.md` | similar patterns queried via mnemo, deps & conflicts mapped |
| 3 | Design | `…-design.md` | 2-3 alternatives with trade-offs, recommendation, **user approval** |
| 4 | Decision | mnemo node `decision_<topic>` | chosen approach + Why + alternatives rejected; committed to mnemo |
| 5 | Planning | `…-plan.md` (uses `superpowers:writing-plans`) | ordered tasks, deps, validation checkpoints |
| 6 | Specs | `…-specs.md` | per-task I/O, edge cases, test cases |
| 7 | Implementation | code + tests (TDD via `superpowers:test-driven-development`) | tests green, atomic commits |
| 8 | Verification | `…-verification.md` | full suite pass, manual smoke, perf check |
| 9 | Documentation | CLAUDE.md / MEMORY.md updated, lessons in mnemo | new project nodes + edges committed |

### 9.2 `mnemo:debug` (rigid; defers to `superpowers:systematic-debugging`)

`reproduce → hypothesize → instrument → bisect → fix (minimum) → verify → RCA-to-mnemo`

The RCA phase is non-skippable: every debug session ends with a `memory_project` node describing root cause, blast radius, and prevention.

### 9.3 `mnemo:refactor` (flexible)

`measure baseline → propose target shape → atomic commits (each green) → verify behavior + perf`

Uses `superpowers:test-driven-development` to ensure each commit is green before moving on.

### 9.4 `mnemo:add-knowledge`

`novelty check (query mnemo first) → categorize (user/feedback/project/reference) → write with Why + How-to-apply → graph-link to related nodes → reindex → update MEMORY.md`

The novelty check is critical: prevents duplicates. If a similar node exists with > 0.85 cosine similarity, the skill prompts the user: "supersede [existing]?" or "append?" or "cancel".

### 9.5 `mnemo:query-knowledge`

The retrieval flow used by both hooks and on-demand `/mnemo-query`:

`intent classify → hybrid retrieve (vector + graph) → budget compress → cite with [mnemo:<id>]`

### 9.6 `mnemo:onboard-project`

For first-time scan of a new repo:

`scan (CLAUDE.md, docs/, README, manifests) → extract conventions (test framework, deploy, branch strategy) → build initial project nodes → link to global patterns → user-confirm before commit`

### 9.7 `mnemo:review`

Code review enriched with project-specific feedback memory:

`pull project review-related memory ("we always check X in this repo") → build checklist → review → capture new lessons as memory_feedback nodes`

Parallels `superpowers:requesting-code-review` but adds the project-specific memory injection.

### 9.8 Cross-cutting safety rails

Applied to every workflow:

- **No co-author trailers** on commits (matches the existing global rule)
- Every workflow phase declares done-criteria; the next phase is blocked until they pass
- Every workflow ends by writing a `lessons` node to mnemo so the next session benefits
- Workflows run inside isolated git worktrees by default for non-trivial work (uses `superpowers:using-git-worktrees`)

## 10. Plugin packaging

### 10.1 Repo layout

```
mnemo/                                  (repo root = D:\Repository\knowledge-base\)
├── .claude-plugin/plugin.json          plugin manifest
├── skills/
│   ├── mnemo-implement-platform/SKILL.md
│   ├── mnemo-debug/SKILL.md
│   ├── mnemo-refactor/SKILL.md
│   ├── mnemo-add-knowledge/SKILL.md
│   ├── mnemo-query-knowledge/SKILL.md
│   ├── mnemo-onboard-project/SKILL.md
│   └── mnemo-review/SKILL.md
├── commands/
│   ├── mnemo-query.md
│   ├── mnemo-add.md
│   ├── mnemo-reindex.md
│   ├── mnemo-ui.md
│   ├── mnemo-status.md
│   └── mnemo-hooks.md
├── hooks/
│   ├── session-start.sh         session-start.ps1
│   ├── user-prompt-submit.sh    user-prompt-submit.ps1
│   └── post-tool-use.sh         post-tool-use.ps1
├── daemon/
│   ├── pyproject.toml
│   ├── mnemo/
│   │   ├── __init__.py
│   │   ├── cli.py               argparse / typer entry
│   │   ├── server.py            FastAPI app
│   │   ├── store.py             SQLite + sqlite-vec
│   │   ├── ingest.py            file scanning, parsing, watcher
│   │   ├── embed.py             MiniLM
│   │   ├── retrieve.py          hybrid scoring
│   │   ├── graph.py             edge inference
│   │   ├── intent.py            prompt → tag classification
│   │   ├── compress.py          token-budget compression
│   │   └── ui/{templates/, static/}
│   └── tests/{unit/, integration/}
├── install.sh / install.ps1
├── docs/{architecture.md, plans/, workflows/}
├── README.md  CONTRIBUTING.md  LICENSE  CLAUDE.md  .gitignore
```

### 10.2 plugin.json (manifest)

```json
{
  "name": "mnemo",
  "version": "0.1.0",
  "description": "Local-first knowledge memory system for Claude Code",
  "author": "mnemo contributors",
  "license": "MIT",
  "skills": "skills/",
  "commands": "commands/",
  "hooks": {
    "SessionStart": "hooks/session-start.{sh|ps1}",
    "UserPromptSubmit": "hooks/user-prompt-submit.{sh|ps1}",
    "PostToolUse": "hooks/post-tool-use.{sh|ps1}"
  }
}
```

### 10.3 Distribution

- GitHub repo, public, MIT
- Tags: `claude-code`, `claude-plugin`, `rag`, `graph-rag`, `knowledge-management`, `memory`, `local-first`
- Install via `git clone && ./install.sh`
- Future: PyPI publish (`pip install mnemo` for the daemon-only path)

## 11. Phased roadmap

Each phase = one commit (or a small set if cleanly separable). No co-author trailers.

| Phase | Scope | Commit message |
|---|---|---|
| 0 | scaffold (README, LICENSE, .gitignore, CLAUDE.md) | `chore: scaffold mnemo project` |
| 1 | full design doc → `docs/plans/2026-05-09-mnemo-design.md` | `docs: capture mnemo system design` |
| 2 | daemon: SQLite schema + store layer + tests | `feat(daemon): store layer` |
| 3 | daemon: ingestion (scan, YAML parse, file watcher, source registry) | `feat(daemon): ingestion pipeline` |
| 4 | daemon: MiniLM embedding + chunking | `feat(daemon): MiniLM embedding` |
| 5 | daemon: hybrid Graph-RAG retrieval + edge inference | `feat(daemon): hybrid retrieval` |
| 6 | daemon: CLI (typer) + FastAPI HTTP server | `feat(daemon): CLI + HTTP API` |
| 7 | UI: HTMX search, node view, graph view, sources, audit, settings | `feat(ui): HTMX web UI` |
| 8 | plugin scaffold + hooks + slash commands | `feat(plugin): plugin scaffold` |
| 9 | seven workflow skills | `feat(plugin): workflow skills` |
| 10 | install scripts (bash + PowerShell) | `feat: cross-platform install` |
| 11 | end-to-end smoke (index 43 files, run query, verify hook injection) | `test: e2e smoke` |
| 12 | community polish — CONTRIBUTING, ARCHITECTURE, examples | `docs: contributor guide` |

## 12. Open questions / future

- **Q1.** Should the daemon auto-start on Claude Code launch or run as a system service? (Initial: auto-start on first hook fire; daemonize via `mnemo daemon start`.)
- **Q2.** Edge inference: how aggressive? Initial pass: only frontmatter-declared and co-occurrence-learned. No LLM-inferred edges (deterministic, debuggable).
- **Q3.** Should `session_summary` nodes be opt-in or opt-out? Initial: opt-in (Scope C). Transcripts are noisy.
- **Q4.** Multi-machine sync: explicitly non-goal v1. Future: optional SQLite replication via Litestream → S3.
- **Q5.** Retrieval quality eval: build a small held-out test set of (prompt, expected node IDs) for regression testing. Phase 11+ work.

## 13. References

### External

- [sqlite-vec](https://github.com/asg017/sqlite-vec) — vector search in SQLite
- [sentence-transformers](https://www.sbert.net/) — local embedding models
- [HTMX](https://htmx.org/) — hypermedia-driven UI
- [Cytoscape.js](https://js.cytoscape.org/) — graph visualization
- [FastAPI](https://fastapi.tiangolo.com/) — Python web framework
- [Microsoft GraphRAG](https://github.com/microsoft/graphrag) — reference design for graph-augmented RAG (we use a much simpler typed-edge variant)

### Internal

- `~/.claude/CLAUDE.md` — global memory (durable patterns)
- `~/.claude/MEMORY.md` — global memory index
- `~/.claude/projects/<project>/memory/MEMORY.md` — per-project memory index
- Existing memory format: YAML frontmatter (`name`, `description`, `type`) + markdown body

---

**End of design document.** All sections approved by user 2026-05-09. Implementation proceeds via phased roadmap (§11).
