# mnemo roadmap

The v1.1 line generalized mnemo beyond Claude Code: versioned HTTP
protocol, VS Code extension with `@mnemo` chat participant, generic
PyPI middleware with provider shims, three new workflow skills,
PDF + plain-text ingest, BASE knowledge with project-isolation
hard-filter, in-UI source management.

The 1.0 line shipped a complete local-first knowledge memory system:
typed graph store, hybrid Graph-RAG retrieval, Claude Code plugin,
web UI, seven workflow skills, install scripts, end-to-end smoke,
and benchmark infrastructure.

Below is what's next.

Categorized by **horizon** (when) and **value** (why).

## 1.2 - SaaS-side ingestion + marketplace publish

- **VS Code Marketplace publish.** v1.1 ships `.vsix` to GitHub
  releases only; v1.2 wires `vsce publish` with a `VSCE_PAT` secret.
- **Notion / Confluence ingester**: integration token, walk a
  database / space, parse to memory nodes.
- **GitHub Issues ingester**: walk open + closed issues for a repo.
- **Slack ingester** (read-only, allowlist channels).
- **POST /v1/nodes**: full create endpoint so the VS Code "Add Note"
  command can land memory without writing files directly.
- **Drop the legacy 308 redirects** -- they were a v1.1-only bridge.

## 1.0.x - Bug fixes and small improvements

Patch-level releases for things that emerge from real-world use.

- **Faster auto-discovery**: scan `~/.claude/projects/*/memory/` once on
  daemon start and cache the result instead of re-globbing each
  `mnemo init`.
- **Better error toasts**: surface stack traces from the daemon to the UI
  toast so users don't have to tail the daemon log.
- **Tokenizer-accurate token counts** in `compress.count_tokens`. Today's
  word-count approximation overshoots on dense markdown. Wire MiniLM's
  tokenizer when the embedder is loaded.
- **Reindex progress**: HTMX-streamed progress bar on `/sources-page` so
  the user can see a long reindex in motion.

## 1.1 - UX polish (4-6 weeks out)

Make mnemo more pleasant for users who aren't deep on the architecture.

- **Onboarding flow**: a one-screen setup that walks a fresh user through
  their first source registration, indexes, and query.
- **Search highlighting**: bold the matching tokens in returned hit
  descriptions so the lexical match is visible.
- **Keyboard shortcuts**: `/` to focus search, `g g` for graph, `g s` for
  sources, `?` for cheatsheet.
- **Bulk node operations**: select multiple nodes from search results,
  delete or relabel in one click.
- **Light mode**: parameterize the dark theme so users on light terminals
  can flip it.
- **Per-project pages**: `/projects/<key>` showing nodes, edges, recent
  queries scoped to that project.

## 1.2 - Retrieval quality (6-12 weeks out)

Higher-quality recall for users with thousands of memory entries.

- **Swap to BGE-M3 embedder** for users who want better quality at the
  cost of disk (1.4 GB) and latency (40 ms vs. 17 ms). Behind a settings
  flag.
- **Tokenizer-aware chunking**: switch `chunk_body` to the embedder's
  tokenizer when loaded, so `max_tokens` is exact.
- **Query expansion**: rewrite the user's prompt into 2-3 paraphrases
  before vector search, then merge results. Uses a tiny rewriter model
  or a rules-based expander.
- **MMR re-ranking**: replace pure score-sort with Maximal Marginal
  Relevance to improve result diversity (avoids returning 5 similar
  feedback nodes when 1 is enough).
- **Cross-encoder rerank**: optional second-stage rerank with
  `ms-marco-MiniLM` for the top-20 vector candidates. ~10x quality
  improvement on ambiguous queries at +50 ms latency.
- **Auto-tune scoring weights** from the audit log: if the user fixes
  retrieval bugs by re-running with manual edits, learn from those
  signals to bias the weights.

## 1.3 - Multi-machine / sync (3+ months out)

Today mnemo is single-machine. Some users want their memory to follow
them.

- **SQLite replication via Litestream** to S3 or GCS. Read-only mirrors
  on other machines pull the latest snapshot. Write-only on the primary.
- **Conflict-free merging** for the case where two machines wrote new
  memory offline. Append-only by node ID; resolve duplicates via
  `supersedes` edges.
- **Encrypted-at-rest** option for users who want to back up to a less-
  trusted destination.

## 1.4 - Beyond Scope B (3-6 months out)

Today's default sources are project memory + global CLAUDE.md +
`docs/plans/`. There's more signal we could absorb.

- **Session transcripts** (Scope C): opt-in indexing of past Claude
  sessions, with PII detection to avoid embedding secrets.
- **Commit messages** of tracked repos: `mnemo source add <repo> --kind
  commits` walks `git log` and captures one node per commit.
- **Issue tracker integration**: pull from Linear / GitHub / Jira via
  optional connectors. Each issue becomes a `memory_reference` node with
  a `mentions` edge to related project nodes.
- **Slack threads**: opt-in connector for capturing decisions made in
  chat.

## 2.0 - Code Intelligence (approved 2026-05-11)

Full design: [docs/plans/2026-05-11-mnemo-v2.0-design.md](plans/2026-05-11-mnemo-v2.0-design.md).

Move from "typed knowledge memory" to "typed knowledge memory + typed
code graph", over the same hybrid Graph-RAG retrieval. The killer
query is the **cross-stack sitemap**: "this React button calls this
Express handler which queries this Postgres table" -- one graph
traversal once the schema + extractors are in place.

- **Explicit source typing**: `code_repo` (tree-sitter) + `docs_dir`
  (markdown harvest) + `memory_dir` (existing) as discrete kinds.
  Auto-router classifies on `mnemo source add` with a dry-run preview
  so a repo of READMEs never gets silently misclassified as memory
  (the v1.1.0 "Duyen" bug becomes structurally impossible).
- **Tiered code graph**: Tier 1 universal structure (16 grammars),
  Tier 2 call-graph (Python / TS-JS / Go via Stack-Graphs-style scope
  resolution), Tier 3 framework extractors (FastAPI, Express, React,
  Next.js, Django, Flask).
- **Cross-stack composition** via new `linked_project` edge.
- **`/code` UI family** with drill-down navigation and lazy
  ego-network expansion (2 hops default, click-to-expand).
- **5 new skills**: `mnemo:explore-codebase`, `mnemo:trace-call`,
  `mnemo:trace-route`, `mnemo:explain-design`, `mnemo:debug-with-code`.
- **Per-file incremental watcher** with 2.5s debounce window.
- **Migration banner** auto-detects pre-v2.0 misclassified
  `memory_dir` sources and offers re-classification (no silent data
  loss).
- **50k file safety ceiling** on the auto-router.

14 phases, ~3 weeks. v1.2 (Learning to Listen) ships first as a
small orthogonal release; v2.0 inherits its auto-tuner.

Carried forward as **hard non-goals**: chat surface (deferred to v3),
LSP integration (v2.x candidate), refactoring tools (out of scope
indefinitely), unified everything-on-one-canvas graph view (known
scale failure).

## 2.x - Agentic curation (deferred)

The "manage, not just hold" direction is still on the roadmap but
shifted past v2.0:

- **Auto-archive stale entries**: nodes that haven't been retrieved in
  N months and have no recent edits get auto-archived (still retrievable
  via `--include-archived`).
- **Auto-supersede**: when two nodes have similar embeddings AND the
  newer one's body covers the older one's body, propose a supersedes
  edge. User accepts/rejects in the UI.
- **Recurring review**: "your memory has 12 nodes that look stale,
  review them" — a daily/weekly UI nudge.
- **Cross-project pattern detection**: surface clusters of nodes that
  look like the same pattern across different projects. Promote the
  cluster to a global `feedback` node.
- **Agentic ingestion**: a small agent watches your shell history and
  proposes new memory entries when it sees a non-obvious decision being
  made (e.g., "you just tweaked an MQTT config; capture the why?").

## 3.0 - Companion / chat (sketch)

Chat surface in the UI with BYO API key across providers. Consumes
v2.0's typed code graph as retrieval context. `mnemo:doc` skill lands
here (deferred from v1.1). Direction sketch in
`project_mnemo_future_versions.md` memory note.

## 2.1+ - Ecosystem

- **PyPI release** for the daemon (`pip install mnemo`).
- **Homebrew tap** for macOS install without Python toolchain.
- **VS Code extension** that exposes search + node-edit inline (without
  needing the web UI).
- **Public benchmark suite**: a community-curated set of (prompt,
  expected) pairs against synthetic memory, so different retrieval
  configurations can be ranked.
- **Plugin marketplace integration**: when Claude Code's plugin
  marketplace exists, list mnemo there.

## Out of scope (deliberate)

These are flagged as **non-goals** so contributors don't propose them
without first opening a discussion.

- **Multi-user / accounts.** mnemo is single-user by design. If multiple
  people want shared memory, they should use a wiki + index it.
- **General-purpose RAG.** mnemo is tuned for typed, structured memory.
  Throwing arbitrary PDFs at it works but the retrieval signal degrades.
- **LLM-inferred edges.** Graph edges come from frontmatter (declared)
  and co-occurrence (learned). LLM-inferred edges sound nice but are
  non-deterministic, hard to debug, and quickly become a black box.
- **Cloud-only** mode. We will never require a hosted backend.
  Litestream-backed sync (1.3) is opt-in and never load-bearing.

## How to influence this

Open an issue with the `roadmap` label. Real-world friction always wins
over speculative features. If you hit a problem the roadmap doesn't
address, describe the workflow you wanted, not the feature you imagined.
