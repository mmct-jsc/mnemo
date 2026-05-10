# Changelog

All notable changes to mnemo are documented here.

## [1.0.3] - 2026-05-10

Bug-fix release for issues caught after 1.0.2 went out.

### Fixed

- **Graph node click did nothing** (no detail panel, no highlight).
  The inline ``x-data`` on ``.graph-pane`` defined methods using
  shorthand syntax that Alpine's expression parser was tripping on,
  silently failing to set up the component. Refactored into a
  named ``graphPane()`` factory function so x-data is just
  ``x-data="graphPane()"``. All state and methods (selectFromCanvas,
  copyCitation, typeColor) are now defined cleanly in one place.
- **Race condition between Cytoscape init and Alpine init**.
  The IIFE used to start before Alpine had hydrated, so
  ``Alpine.$data(graphRoot)`` returned ``undefined`` and clicks
  silently failed. Now wrapped in ``alpine:initialized`` so cy
  handlers only register after Alpine is ready.
- **Stale ``Alpine.$data(root)`` reference** in the post-1.0.2 graph
  script - ``root`` was never defined, threw on every node tap.
  Removed; replaced with the ``graphPane`` component's own methods.
- **Bell unread badge flickered on every page load** - the badge
  rendered before Alpine hydrated state from localStorage, briefly
  showing the wrong (or no) count. Added ``x-cloak`` so the badge
  is hidden until Alpine is ready.

### Added

- **Smooth page-load fade-in**: ``main`` containers animate in with
  a 240ms cubic-bezier translate+fade. Subtle but makes navigation
  feel less jarring.
- **Active navbar item now has an animated underline accent** that
  scales in when the page loads, so the active state is more
  noticeable.
- **Card hover micro-interaction**: stat cards and hit cards lift
  slightly and gain a soft shadow on hover (was just border color).
- **``prefers-reduced-motion``** honored everywhere - all
  animations and transitions collapse to ~0ms when the user has
  reduce-motion set.

## [1.0.2] - 2026-05-10

UI restructure release. Adds a dashboard, paginated lists, and a
notification history. Fixes several UI bugs from 1.0.1.

### Added

- **Dashboard at `/`** — overview screen with stat cards (memory,
  sources, learned connections, queries logged), a type-distribution
  bar chart, top connected nodes, recent queries, and a quick-search
  input.
- **`/nodes-page`** — dedicated nodes list with full-text search,
  filter by type and project, and pagination (25 per page).
- **Server-side pagination** on the audit log and the nodes list,
  rendered through a shared `_pagination.html` partial. Pagination
  preserves filter query params across pages.
- **Notification history** — bell icon in the topbar with an unread
  count badge. Click to open a dropdown of past toasts (last 50,
  localStorage-backed). Click "Clear" to wipe history.
- **Toast-after-reload** — `window.toastAfterReload(...)` queues a
  toast via sessionStorage so it shows after the next page load.

### Changed

- **Navigation restructure**: the topbar is now Dashboard / Nodes /
  Graph / Sources / Audit / Settings (was Search / Graph / ...).
  Search is a feature of the Nodes page, not its own item.
- **Active state fix**: when on a node detail page (`/node/<id>`),
  the navbar correctly highlights "Nodes".
- **Node detail page**: edges now render with the target/source
  node's badge + name (resolved server-side via the new
  `Store.get_nodes_by_ids` batched lookup), not just their truncated
  ID.

### Fixed

- **Graph 'Connected to' showed only colored dots** — the template
  bound to `n.name` but the Cytoscape node data field is `label`.
  Now also displays the type as a small mono label.
- **Connected-node click redirected away from the graph** — clicking
  an entry in the side panel's "Connected to" list now focuses that
  node on the canvas (animates pan + zoom + highlight + selects),
  rather than navigating to its detail page. The "Open node" CTA
  still goes to the detail page when you want it.
- **Reindex success toast disappeared instantly** — the page reload
  fired before the toast could render. Now uses
  `window.toastAfterReload()` so the toast surfaces after the new
  page loads.
- **Custom scrollbar inside dark panels** — thumb border now blends
  with the panel background instead of the page background, so the
  scrollbar doesn't have a halo around it inside cards / textareas /
  the graph detail panel.
- **Bell dropdown was empty + graph node click stopped working**
  (caught in self-test before push): a duplicate
  `const TOAST_HISTORY_KEY` declaration in two `<script>` blocks
  threw a SyntaxError that disabled all other UI scripts. Fixed by
  declaring it once, in the deferred head script.
- **Graph node click resolved to the wrong Alpine component** after
  the bell wrapper was added to the topbar:
  `document.querySelector('[x-data]')` returned the bell, not the
  graph pane. Now scoped to `.graph-pane` so node clicks correctly
  populate the side panel again.

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
