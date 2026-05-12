# Changelog

All notable changes to mnemo are documented here.

## [1.2.0] - 2026-05-11

**Learning to Listen.** mnemo now closes the personalization loop:
every retrieval result can carry user feedback (explicit thumbs in
the UI / CLI, implicit detection of re-asked queries), and a
coordinate-descent auto-tuner reads those signals to nudge the
6-term scoring weights toward what THIS user actually finds useful.
Plus MMR diversification of the top-K, a clean version cliff on the
1.1-era 308 redirects, and HTTP-driven memory creation via
`POST /v1/nodes` so adapters like the VS Code "Add Note" command no
longer have to go through the filesystem.

8 phases, ~3 weeks. Full design: `docs/plans/2026-05-10-mnemo-v1.2-design.md`.

### Added

#### Feedback collection (phases 1-3)

- **`feedback_event` table** with FK cascades on `query_id` +
  `node_id`, UNIQUE on `(query_id, node_id, reason)` for idempotency.
  Indexes on each of the three filter dimensions.
- **`POST /v1/feedback`** writes one feedback row. Idempotent on the
  triple (double-clicks safe). `signal` is optional -- the daemon
  defaults from `reason` via `signal_for_reason`
  (thumbs_up=+1, thumbs_down=-1, cite_copied=+0.5, inferred_requery=-0.5).
- **`GET /v1/feedback?query_id=…&node_id=…`** lists events
  newest-first; requires at least one filter param.
- **Inferred-re-query detector** fires on every `POST /v1/query`:
  if a recent prompt has cosine >= 0.85 with the new prompt inside
  the configurable window (default 300s), write
  `signal=-0.5, reason='inferred_requery'` against the older
  query's top-N retrieved hits. Treats the re-ask as evidence the
  earlier hits missed.
- **Thumbs up/down buttons on every hit** in the UI. Click POSTs to
  `/v1/feedback` with optimistic state flip + rollback on error.
  Defined as the `hitsFeedback` Alpine factory in `base.html` so it
  survives HTMX swaps. Toggles between up/down still write two rows
  (one per reason) -- the auto-tuner uses the strongest signal.
- **`queries.embedding BLOB`** column persists the query vector so
  the re-query detector can cosine-compare future prompts against
  this one.
- **`queries.score_components TEXT`** column persists the per-hit
  unweighted 6-term breakdown so the auto-tuner can rescore with
  alternative weights without re-running the embedder.

#### Retrieval quality

- **MMR re-rank** on the top-K (`mnemo/rerank.py::mmr_select`).
  Penalizes near-duplicate candidates of already-picked hits so
  the top-5 stops being five paraphrases of the same node. Default
  `mmr_lambda = 0.7`; 1.0 bypasses MMR for the pre-1.2 behavior;
  0.0 is pure diversity. ~0.5ms overhead on top of existing scoring.

#### Auto-tuner (phases 5-6)

- **`mnemo/retune.py`** with `best_feedback_signal`,
  `rescore_with_weights`, `mrr`, `coordinate_descent`, and the
  high-level `retune(store, min_queries=30)` entrypoint.
  Optimizer: nudges of {-0.10, -0.05, +0.05, +0.10} across the 6
  keys, up to 4 passes, EPS=0.001 acceptance, 60s wall-clock cap,
  time-ordered 80/20 train/val split.
- **`POST /v1/retune`** returns a full `RetuneReportOut`
  (proposed/current/diff weights, before/after MRR for train+val,
  sample sizes, iteration count, log). Preview-only -- never
  mutates `/v1/config`. The UI's Apply button posts the proposed
  scoring through the existing `PUT /v1/config`.
- **`mnemo retune` CLI** with `--apply` / `--min-queries N` / `--json`.
  Renders a readable column-aligned diff + before/after MRR + log.
- **"Auto-tune from feedback" panel on `/settings`** with Run /
  Discard / Apply buttons, MRR grid, diff table with changed rows
  highlighted, collapsible optimizer log.
- **`Config.retune_min_queries: int = 30`** threshold under which
  retune refuses to optimize (MRR is too noisy on small datasets).

#### Housekeeping (phase 7)

- **`POST /v1/nodes`** HTTP-driven memory creation. Validates
  `type` and `source_kind` against the store enums; auto-fills
  synthetic `http://api/<uuid>` source_path when omitted; embeds
  eagerly so the new node is searchable immediately. The VS Code
  "Add Note" command (palette `mnemo.addNote`) now POSTs through
  this endpoint instead of opening the dashboard.

### Removed

- **Legacy 308 redirect bridge.** v1.1 had a one-version-only
  middleware that translated `/health` -> `/v1/health` (and 6 more
  un-versioned roots). v1.2 ships the cliff -- those paths now
  return 404. The `X-Mnemo-Api-Version: 1` header has been
  stamping every response throughout v1.1.x to give adapters time
  to migrate.

### Changed

- **`Store.log_query(..., embedding=None, score_components=None)`**
  -- two new optional kwargs; backward compatible (pre-1.2 callers
  who omit them get NULL columns and downstream filters skip them).
- **Three new `Store` helpers**: `recent_queries_with_embeddings`
  (filter by time window + non-null embedding for the re-query
  detector), `recent_queries_with_components` (filter by
  non-null components + min feedback count for the auto-tuner),
  `get_chunk_embeddings` (bulk-fetch chunk vectors for the MMR
  pool via a CTE-VALUES JOIN).
- **`retrieve.query`** now (a) computes + logs the unweighted
  6-term components for the top pool, (b) calls the inferred-
  re-query detector before the audit-log write so the current
  query is never compared to itself, (c) runs MMR over the top
  `max(k*2, 20)` candidates when `mmr_lambda < 1.0`.

### Config additions

Four new keys on `Config` (all settable via `PUT /v1/config` and
the settings.json file):

- `requery_window_seconds: int = 300`
- `requery_cosine_threshold: float = 0.85`
- `requery_top_n_hits: int = 3`
- `mmr_lambda: float = 0.7`
- `retune_min_queries: int = 30`

### Tests

Roughly +60 tests across 8 phases (455+ pass total, 2 skipped, ruff
clean):

- 17 feedback_event store / endpoint tests (phase 1).
- 6 inferred-re-query detector unit tests + 4 store-helper tests
  (phase 2).
- 3 UI thumb-button render tests (phase 3).
- 10 MMR + `get_chunk_embeddings` tests (phase 4).
- 14 retune unit tests (math + optimizer + entrypoint) + 2 CLI
  tests (phase 5).
- 3 `/v1/retune` HTTP tests + 1 settings-panel render test (phase 6).
- 15 redirect-removal tests + 6 `POST /v1/nodes` tests + ~10
  retrofit edits to legacy-path callers (phase 7).

### Upgrade notes

- v1.1.x adapters that called un-versioned paths (`/health`,
  `/sources`, etc.) must now call `/v1/...` directly. The 308
  bridge is gone.
- VS Code extension "Add Note" command behavior changed -- previously
  opened the dashboard, now prompts for type/name/body inline and
  creates the node via `POST /v1/nodes`.
- Existing audit-log rows (pre-1.2) have NULL `embedding` and NULL
  `score_components` columns; they're invisible to the re-query
  detector and the auto-tuner but otherwise queryable as before.

### Open questions deferred to v1.3 / v2.0

- Cross-encoder re-rank (v1.3, paired with a quality-first scoring mode).
- Nightly auto-retune cadence (v1.3, once on-demand proves itself).
- NDCG@K objective (when labeled dataset gets large enough).
- Reciprocal Rank Fusion retrieval (v1.3).
- Code-graph parsing + sitemap (v2.0).
- Chat surface + MCP shim (v3.0).

## [1.1.1] - 2026-05-11

**Hotfix.** Two source-management bugs surfaced in real use after the
1.1.0 release: removing a source left its nodes orphaned in the graph
forever, and the Reindex button could fire concurrent runs after a
page navigation. Both are fixed here without any API contract change
beyond two additive endpoint responses.

### Fixed

- **`DELETE /v1/sources` now cascades node deletion.** Previously the
  endpoint only deleted the row from the `sources` table; every node
  ingested from the removed source's path lingered in the graph
  forever because the reindex orphan-sweep only inspects nodes whose
  path matches a *still-registered* source. The UI's confirmation
  copy ("Existing nodes from this source will be removed on the next
  reindex") was actively misleading. Reported visually as "wipe all
  graph and replace with all README files" when a non-memory tree
  was mistakenly registered as `memory_dir`.
- **Concurrent `POST /v1/reindex` requests no longer race.** The
  daemon now serializes reindex requests with an in-process lock. A
  second request while another is in-flight returns `HTTP 409` with
  `{"error": "reindex_in_progress", "started_at": <ts>}`. The UI's
  client-only "running" flag was wiped on every page reload /
  navigation, so a user navigating away and back could fire a second
  reindex on top of an in-flight one.

### Added

- **`mnemo source orphans [--prune]`** CLI command. The cascade fix
  above stops *future* removals from leaking, but users who removed a
  source under the pre-1.1.1 behavior still have the leftover nodes in
  their store. Running `mnemo source orphans` lists every node whose
  `source_path` matches no registered source; `--prune` deletes them
  along with their vector chunks. Output is human-readable by default;
  `--json` available for scripts.
- **`mnemo source remove`** now prints the cascade count, so the user
  can verify the cleanup actually fired (`removed: /path  (3 nodes
  cleaned up)`).
- **`GET /v1/reindex/status`** returns `{"running": bool, "started_at":
  int|null}` so the Sources page can restore the disabled-button state
  after navigation. The UI polls this every 2 s when a reindex is
  in-flight and reloads once it flips back to idle.
- **`DELETE /v1/sources` response gained a `removed` field**
  (`{"ok": true, "removed": N}`) reporting the cascade count. The
  Sources page now shows "Source removed (N nodes cleaned up)" in
  the success toast.

### Changed

- **`mnemo.paths.path_under_source`** is now a public helper used by
  both the ingest reconciler and `Store.remove_source` so the two
  layers agree on what "owned by this source" means.
- **`Store.remove_source` returns `int`** (count of cascaded nodes).
  Previously returned `None`. Callers that ignored the return value
  still work.
- **`Store.find_orphan_nodes`** new method — returns nodes whose
  `source_path` matches no registered source (the inverse of the
  cascade). Used by the `mnemo source orphans` CLI.
- **Sources page modal copy** updated to truthfully describe the
  cascade ("removes every node that was ingested from it").

### Upgrade notes

If you removed a source under v1.1.0 or earlier and you still see its
old nodes in the graph / Nodes page, that's the pre-1.1.1 leak. After
upgrading, run::

    mnemo source orphans          # see what's left
    mnemo source orphans --prune  # clean them up

then restart the daemon so the reindex picks up the cleaner state.
For our reporter (the `D:\Repository\Duyen` case): after upgrade, those
README nodes leftover from the misregistered `memory_dir` will be
listed and cleanable in one command.

### Tests

- `test_remove_source_cascades_descendant_nodes` -- unit, store layer.
- `test_remove_source_cascade_respects_claude_md_exact_match` -- unit.
- `test_remove_source_unregistered_returns_zero` -- unit (idempotency).
- `test_find_orphan_nodes_returns_unregistered_sources` -- unit.
- `test_find_orphan_nodes_empty_when_all_match` -- unit.
- `test_find_orphan_nodes_no_sources_means_everything_orphan` -- unit.
- `test_delete_source_cascades_nodes_via_http` -- integration, full
  ingest-then-DELETE round trip.
- `test_reindex_status_idle_when_no_run_in_flight` -- integration.
- `test_reindex_status_reports_running_mid_flight` -- integration,
  uses a blocked-event monkeypatch on `ingest.reindex`.
- `test_concurrent_reindex_returns_409_with_started_at` -- integration.
- `test_reindex_lock_released_on_error` -- integration (lock cleanup
  even when ingest raises).
- `test_cli_source_remove_reports_cascade_count` -- CLI.
- `test_cli_source_orphans_empty` -- CLI.
- `test_cli_source_orphans_lists_then_prunes` -- CLI, end-to-end
  reproduction of the pre-1.1.1 leak path.
- `test_cli_source_orphans_json` -- CLI.

## [1.1.0] - 2026-05-10

**Beyond Claude Code.** mnemo now serves any IDE / any LLM SDK / any
common workflow, while staying local-first, token-budgeted, and
citation-back. Everything in this release is additive on top of the
v1.0.x line; existing Claude Code plugin users see no breakage.

### Added

#### Public protocol (versioned)

- **All HTTP endpoints under `/v1/...`** with auto-published OpenAPI
  spec at `/v1/openapi.json`. Internal UI/HTMX routes excluded from
  the spec via `include_in_schema=False`.
- **`X-Mnemo-Api-Version: 1` header** on every response so adapters
  can sanity-check the daemon they're talking to.
- **Legacy paths return 308** to their `/v1/...` equivalents
  (`/health`, `/sources`, `/reindex`, `/nodes`, `/query`, `/audit`,
  `/config`). Method + body preserved so adapters that haven't
  migrated keep working. The redirects are scheduled for removal in
  **v1.2**.
- **New endpoints:** `POST /v1/projects/resolve`,
  `GET|POST|DELETE /v1/projects/active`, `GET /v1/projects/known`,
  `PATCH /v1/sources`, `GET /v1/fs/suggest` (filesystem path
  suggestions for the UI).
- **`docs/protocol.md`** spec doc + canonical project_key derivation
  algorithm with a 40+ entry fixture file for cross-adapter drift
  detection.

#### Active-project state + project-key resolver

- Singleton `active_project` table with a hybrid contract: per-call
  `project_key` overrides the persisted active project; absence
  falls back to it.
- Active-project pill in the UI topbar with a popover for set /
  clear, accent-color when set.

#### Source patterns + management

- New `nodes.include` and `nodes.exclude` columns -- comma-separated
  gitignore-style globs -- compiled into `pathspec.PathSpec` at scan
  time. Defaults to `**/*.{md,markdown,txt,pdf}` for `memory_dir`
  sources; per-source overrides supported.
- `PATCH /v1/sources` for partial updates; UI `Add source` /
  per-row `edit` / `remove` flows on the Sources page with autocomplete
  for path (live filesystem suggestions + recents) and project_key
  (known-keys-from-DB).

#### File-format expansion

- New parser registry under `mnemo/parsers/`. Adding a format in
  v1.2+ is a 2-line change.
- **PDF parsing** via `pypdf`. Per-page `--- page N ---` headers so
  retrieval can cite specific pages. Corrupt PDFs degrade
  gracefully (log + empty body, no pipeline crash).
- **Plain text** (`.txt`, `.markdown`) parsing.

#### BASE knowledge + project isolation

- New `nodes.base` column. Frontmatter `base: true` flags a node as
  BASE. BASE nodes bypass project isolation and surface in every
  project's queries.
- `retrieve.query()` hard-filters to `(project_key == active OR
  base)` when an active project is set. Behavior gated by new
  `config.project_isolation_mode = 'strict' | 'boost'` (defaults to
  `strict`; `boost` restores v1.0 behavior).
- `Store.list_nodes` and `count_nodes` honor BASE inclusion. Nodes
  page type counts respect the project filter.
- BASE pill toggle on the node detail page; gold "base" badge in
  lists.

#### Workflow skills

- **`mnemo:plan`** (rigid, 6 phases): pull mnemo context ->
  brainstorm -> 2-3 approaches -> decisions -> emit
  `docs/plans/<date>-<topic>-design.md` -> done-criteria. Closes
  the gap between idea and `mnemo:implement-platform`.
- **`mnemo:retro`** (flexible, 4 phases): sweep recent activity ->
  propose 0-N candidate memory entries -> user triages
  accept / edit / reject -> write + reindex.
- **`mnemo:incident`** (rigid, 7 phases): severity + post-mortem
  stub -> pull priors -> stabilize BEFORE investigate -> RCA ->
  post-mortem doc -> promote durable lesson to memory_feedback.

#### `mnemo-middleware` Python package (PyPI)

- `clients/middleware-py/` with separate pyproject.toml. Single
  runtime dep: `httpx`. Provider SDKs are opt-in extras.
- **`retrieve_context(prompt, ...)`** helper. Returns a markdown
  block formatted like the Claude Code hook output. Always additive:
  daemon down / timeout / invalid JSON returns `""` so the caller
  drops the result into a system message unconditionally.
- **`patch(client, mode='auto'|'once'|'every')`** monkey-patcher
  with provider shims for OpenAI, Anthropic, Google (Gemini), and
  Ollama. `auto` (default) re-injects only on new conversations or
  topic shifts; `once` for persistent agents; `every` for one-shot
  evaluators. Anthropic shim emits `cache_control: ephemeral` on
  the system block when it's >= ~1024 tokens for the 90% cache
  discount.
- 20 unit tests against `httpx.MockTransport` + a fake openai-shaped
  client.

#### `mnemo-vscode` extension

- New `extensions/vscode/` TypeScript project. Ready to package
  with `vsce`; no marketplace publish in v1.1 (`.vsix` GitHub
  release artifact only -- marketplace is v1.2).
- Status bar pill (daemon health + active project), palette
  commands (Query / Add Note / Set Active Project / Open UI /
  Reindex), sidebar TreeView, **`@mnemo` chat participant** with
  slash subcommands `/recall`, `/sources`, `/add`. Hits stream as
  chat references with `[mnemo:<id>]` citations.

#### UI polish

- Custom-themed `<input type="checkbox">` + `<select>` (URL-encoded
  inline-SVG caret, `color-scheme: dark` for native popups).
- Source management table shows include / exclude patterns inline.
- Always-visible filter Clear button (disabled when no filter)
  instead of mounting/unmounting per toggle.

### Changed

- Default include patterns for memory_dir / plan_dir / transcripts
  widened to `**/*.{md,markdown,txt,pdf}`.
- `Store.count_nodes(project_key=...)` filter respects active
  project + BASE union.
- `_LegacyRedirectMiddleware` and `_ApiVersionHeaderMiddleware`
  added to the FastAPI app. Order matters: header middleware must
  be added **last** so it stamps headers on the inner middleware's
  308 short-circuit responses (captured the lesson in
  `feedback_starlette_middleware_order.md`).

### Fixed

- Filter empty-string normalization on the Nodes page
  (`?project=` no longer SQL-matches zero rows; route normalizes
  empty form values to None).
- Type-counts dropdown was showing global counts when the project
  filter was active. Now scoped to the project + BASE union.
- pathspec deprecation: switched from the deprecated
  `'gitwildmatch'` pattern style to `'gitignore'`.

### Hard rules (carry-over)

- No `Co-Authored-By` trailers on commits, ever.
- No emojis in code, docs, commits.
- Conventional commit prefixes.
- Daemon binds to `127.0.0.1` only.

### Migration notes

- The `nodes.base`, `sources.include`, `sources.exclude` columns
  are added by an idempotent SQLite migration on first daemon start
  after the upgrade. Existing nodes default to `base = 0`. Existing
  sources default to NULL include/exclude (treated as "use the kind
  default").
- Adapters can keep calling unversioned paths for the v1.1 series;
  in v1.2 these will be removed.

## [1.0.5] - 2026-05-10

Polish on top of 1.0.4. Three real bugs and two ergonomic upgrades.

### Fixed

- **Node-detail body would briefly show then disappear on page load.**
  ``x-data="nodePage({ raw: {{ node.body | tojson }} })"`` produced
  output where the JSON's inner ``"`` characters closed the HTML
  attribute prematurely, so Alpine saw an empty ``x-data`` and ``tab``
  was undefined -- which made ``x-show="tab === 'edit'"`` evaluate to
  false and hide the textarea. Switched the attribute to single
  quotes; Jinja's ``tojson`` already escapes apostrophes as
  ``'``, so the inner string is safe inside ``x-data='...'``.
- **Audit "Showing 1-25 of 129" pushed the right column down**, so
  TOP INTENTS sat 1rem lower than the first query. Moved the line
  above the dash-row and zeroed the ``query-log`` margin so both
  columns share the same first-row baseline.
- **Sliders had a misaligned thumb** at min/max, especially when
  zoomed. Replaced the browser-default range styling with explicit
  webkit/moz track + thumb styles so the thumb stays visually on the
  track at every position.

### Added

- **Stepper buttons** (``[−] [value] [+]``) on every Settings weight
  + default. Click steps the value by the natural increment for that
  field (0.05 for weights, 1 for k / recency, 50 for budget tokens),
  clamps to min/max, and rounds to mitigate JS float drift.
- Native number-input spinners are hidden when the field is inside a
  ``.stepper``; the explicit buttons are the only adjuster.

## [1.0.4] - 2026-05-10

UI polish release. Pages outside the dashboard now use the same
full-dive layout (hero, stat cards, multi-column grid). Body previews
render proper Markdown. Timestamps display in local time. Plus a few
alignment fixes carried over from 1.0.3 feedback.

### Added

- **Markdown body preview** on the node detail page (Edit / Preview
  tab toggle) and inside the graph side panel. Uses ``marked`` +
  ``DOMPurify`` from CDN; rendered output picks up dark-theme styling
  via the new ``.md-body`` class. Same renderer is reused across both
  pages -- no duplication.
- **Page hero** on Audit, Settings, Node detail, and Sources: title
  with gradient + subtitle + right-aligned actions area, mirroring the
  Dashboard's welcome header for visual consistency.
- **Audit page summary cards** at the top (total queries, hits
  delivered, avg hits/query, last query time) and a side rail with
  top-intent counts and the activity-window date range.
- **Node detail stat cards** (outgoing edges, incoming edges, body
  chars, last updated). The page now uses a 2-column main/aside grid
  with edges as a sticky side rail.
- **Local-time timestamps**: every Unix ``ts`` in the UI is rendered
  by a shared ``mnemoFormatTs(ts, fmt)`` helper into the user's
  locale. Server emits ``<time data-ts="...">`` tags; a single
  ``DOMContentLoaded`` pass + ``htmx:afterSwap`` hook converts them.
  Three formats: ``datetime`` (default), ``date``, ``relative``.

### Changed

- **Main content max-width** bumped from 1200px to 1600px so wider
  screens feel full instead of empty around the sides. Inner padding
  bumped to 2rem.
- **Settings page** restructured: full-dive hero with Save / Reset in
  the actions area, score-formula callout, then a 50/50 split between
  Scoring weights and Defaults -- both as ``dash-card``s with their
  own weight-grids.
- **Audit page** removed the ``max-width: 920px`` constraint that was
  keeping it narrower than the rest of the UI.
- **Graph side panel** widened to 380px so the markdown body preview
  has room to breathe.

### Fixed

- **Open node / Copy citation alignment** in the graph side panel.
  The two buttons used different box models (``<a>`` with padding vs
  ``<button>`` with padding + border), so they never lined up. New
  shared ``.btn-row`` class normalizes height + padding + border so
  any mix of ``<a>`` and ``<button>`` lines up cleanly.
- **Preview tab on node detail** sometimes rendered empty when
  ``marked`` / ``DOMPurify`` were still loading at Alpine init time.
  Render now retries on a short timer until both libs are hydrated.

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
