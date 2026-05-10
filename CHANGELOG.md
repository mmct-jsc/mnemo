# Changelog

All notable changes to mnemo are documented here.

## [1.0.1] - 2026-05-10

UI enhancement release. No backend changes.

### Added

- **Custom scrollbar styling**: thin, themed scrollbars across all
  scrollable surfaces (Webkit + Firefox via `scrollbar-color`). Track
  is transparent, thumb uses the muted border color and brightens to
  the accent on hover. Inside dark panels (cards, code blocks,
  textarea, the graph detail panel) the thumb border blends with the
  panel background instead of the page background.
- **Themed modal component** (`window.modal()`) that returns a
  `Promise<boolean>`. Drop-in replacement for `window.alert` /
  `window.confirm` with consistent dark-theme styling, escape-to-
  cancel, click-backdrop-to-cancel, and focus-trap on the confirm
  button. Supports `level: 'danger'` for destructive actions.

### Changed

- `settings.html` "Reset to defaults" now uses `window.modal()` with a
  danger-styled confirm button instead of the browser's `confirm()`.
  Going forward, every confirm/alert in the UI uses the themed modal.

### How to use

```js
const ok = await window.modal({
  title: 'Delete this node?',
  body:  'This is permanent.',
  confirm: { text: 'Delete', level: 'danger' },
  cancel:  { text: 'Cancel' },
});
if (ok) { /* user confirmed */ }
```

## [1.0.0] - 2026-05-10

First stable release. mnemo is a local-first knowledge memory system for
Claude Code: aggregate memory across projects, retrieve via hybrid
Graph-RAG, and inject budget-capped context on every prompt.

### Highlights

- **Hybrid Graph-RAG retrieval**: 6-term scoring (vector cosine + graph
  proximity + recency + intent-driven type priority + project scope +
  lexical overlap). 100% top-1 accuracy and MRR=1.000 on the curated
  benchmark.
- **Local-first**: SQLite + sqlite-vec, sentence-transformers MiniLM-L6
  (22 MB). No cloud, no API keys, no network calls.
- **Token-budgeted**: every retrieval ships <= 800 tokens by default,
  ranks descriptions before bodies, always cites with `[mnemo:<id>]`.
- **Auto-update**: file watcher reindexes on every memory edit;
  hash-gated so unchanged files are no-ops.
- **Web UI** at `127.0.0.1:7373/`: search, interactive graph
  (Cytoscape + fcose), node editor, source registry, audit log,
  editable settings. Toast notifications for every action.
- **Seven workflow skills**: implement-platform, debug, refactor,
  add-knowledge, query-knowledge, onboard-project, review.
- **Cross-platform install**: `install.sh` (Linux/macOS/Git Bash) and
  `install.ps1` (Windows PowerShell), both idempotent.

### Architecture

- Three-tier: Claude Code plugin (markdown + hook scripts) -> Python
  daemon (FastAPI on 127.0.0.1:7373) -> SQLite + sqlite-vec store.
- Daemon: ~13 modules. Store / ingest / watcher / embed / intent /
  graph / compress / retrieve / api_schemas / server / cli / daemon /
  paths / config / ui.
- Plugin: `.claude-plugin/plugin.json` + 7 skills + 7 slash commands +
  3 hooks (each cross-platform).

### Performance (38-node real-data benchmark)

- Query latency: 17 ms median, 22 ms p95 (single-thread CPU).
- Reindex: 1,157 nodes/sec (hash-gated, no-op on unchanged files).
- DB footprint: 2 MB for the 38 nodes + 160 co-occurrence edges.
- Model cache: 22 MB for MiniLM-L6.

### Quality (curated benchmark)

- 7/7 top-1 (100%), MRR 1.000.
- 273 tests (240 unit + 33 integration), all green.

### Configuration

- Settings persist to `~/.claude/mnemo/settings.json`.
- Editable from the web UI at `/settings` or via `PUT /config`.
- Six scoring weights: alpha (vector), beta (graph), gamma (recency),
  delta (type), epsilon (project), zeta (lexical).
- Defaults: alpha 0.40, beta 0.15, gamma 0.10, delta 0.10, epsilon 0.05,
  zeta 0.20.

### Known limitations (non-blockers)

- Daemon-spawn integration test is skipped on Windows because detached
  uvicorn under `subprocess.Popen` is fragile to test deterministically.
  Manual smoke verifies the path.
- `intent` classifier is regex-based; some phrasings will not fire the
  matching tag. Edit `mnemo.intent.INTENT_PATTERNS` to extend.
- Single-machine. Multi-machine sync is on the 1.3 roadmap.

### Documentation

- [README.md](README.md) - quick start
- [docs/architecture.md](docs/architecture.md) - architecture overview
- [docs/plans/2026-05-09-mnemo-design.md](docs/plans/2026-05-09-mnemo-design.md) - full design
- [docs/workflows/index.md](docs/workflows/index.md) - 7 workflow skills
- [docs/examples/sample-queries.md](docs/examples/sample-queries.md) - real query results
- [docs/benchmarks.md](docs/benchmarks.md) - benchmark methodology + tips
- [docs/roadmap.md](docs/roadmap.md) - what's next
- [CONTRIBUTING.md](CONTRIBUTING.md) - contributor guide

### Breaking changes from 0.1.0

None: 0.1.0 was never released. This is the first public version.

### Acknowledgments

Built with: SQLite, sqlite-vec, sentence-transformers, FastAPI, Typer,
HTMX, Alpine.js, Cytoscape.js, fcose, ruff, pytest, uv.
