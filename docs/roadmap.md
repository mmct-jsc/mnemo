# mnemo roadmap

The 1.0 line ships a complete local-first knowledge memory system: typed
graph store, hybrid Graph-RAG retrieval, Claude Code plugin, web UI,
seven workflow skills, install scripts, end-to-end smoke, and benchmark
infrastructure. Below is what's next.

Categorized by **horizon** (when) and **value** (why).

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

## 2.0 - Agentic curation (6-12 months out)

Move from "mnemo *holds* memory" to "mnemo *manages* memory".

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
