# Changelog

All notable changes to mnemo are documented here.

## [2.2.3] - 2026-05-14

**Nebula polish: visibility toggles + drag-stable edges + clean
cold paint + finally-visible pulse.** Four user-reported issues
addressed in one ship.

### Added

- **Edge + label visibility toggles** in the Nebula filter bar.
  Each toggle is a pill with a leading dot -- filled when ON,
  outlined when OFF. Both default to ON. Tapping each flips a
  cytoscape class (``edge.edges-off`` or ``node.labels-off``)
  inside a ``cy.batch()`` so the canvas redraws once.

### Fixed

- **Edges no longer disappear during pan / drag.**
  ``hideEdgesOnViewport`` was ``true`` since v2.0 to keep big
  graphs cheap during fast zooms. The pop-out behavior felt
  jarring -- the graph "collapsed" into bare nodes mid-drag,
  then snapped back. Set to ``false`` for v2.2.3. Labels still
  hide during fast viewport changes (``hideLabelsOnViewport:
  true``) because re-rendering label text isn't cheap and the
  busy-text effect is genuinely distracting.

- **Cold paint no longer flashes "edges first, then nodes".**
  The v2.2.1 chunked-paint hid nodes (via
  ``.preload-hidden`` opacity:0) but did NOT hide edges. Edges
  between two invisible nodes still drew their lines, so for
  the first ~720ms of a cold load the user saw a tangle of
  connections in empty space. Now ``.preload-hidden`` covers
  edges too (``display: none``), and edges fade in AFTER the
  final node chunk lands. Cold paint reads as "densest cluster
  appears, more nodes wave in, then the connection web settles
  in".

- **Canvas-tap pulse is now visible.** Despite v2.2.2 unifying
  the camera-pan + strengthening the pulse, the user still
  reported "no heart beat" on direct canvas taps. Two compounding
  causes:
  1. ``node.animate({style: ...})`` was being QUEUED behind the
     just-applied ``.hl`` class transitions (which animate
     ``border-width`` + ``underlay-padding`` over 220ms via the
     base ``transition-property``). The first half of the pulse
     was eaten by the queue wait. Now uses ``queue: false`` so
     it runs from frame 0.
  2. The pulse amplitudes were still too subtle without the
     camera framing the node center. Bumped further:

         peak underlay-padding 28 -> 36  (+22 from .hl baseline)
         peak underlay-opacity 0.95 -> 1.0
         peak border-width      2 -> 5  (NEW: thick bright stroke)
         period 600ms -> 500ms each leg (1.0s/cycle = clear beat)

  The headline change is the border-width pulse: ``border-width``
  IS in the base ``transition-property``, so cy.animate moves it
  reliably regardless of where the node is on screen.

- **Pulse cleanup also clears border-width bypass.**
  ``_stopPulse`` previously cleared only ``underlay-padding`` and
  ``underlay-opacity``; a de-selected node would keep its 5px
  pulsed border until the next ``.hl`` change overwrote it.
  ``_stopPulse`` now also clears ``border-width``.

### Tests + CI

545 unit tests pass. Ruff lint + format clean. The existing
``test_nebula_progressive.py`` cases still cover the chunked
paint, body streaming, and neighbors stagger from v2.2.1; no
new assertions needed for v2.2.3 (the changes are CSS + cy
config + amplitude tuning behind existing call sites).

## [2.2.2] - 2026-05-14

**Consistent select feedback in Nebula + stronger heart-beat pulse.**

### Fixed

- **Canvas-tap selection had no visual feedback on the node itself.**
  Clicking a node directly on the graph dimmed the rest of the
  canvas and opened the detail panel, but the selected node sat
  perfectly still -- no camera framing, no perceptible pulse.
  Clicking the same node from the file tree DID frame it
  (camera pan + zoom 1.4), so the same logical action felt
  totally different depending on the path. Two changes:

  - **Camera framing is now in ``selectFromCanvas``**, so every
    entry path (canvas tap, file-tree click, neighbor list click,
    ``?node=`` URL deep-link) runs the same ``cy.animate({center,
    zoom})`` over 350ms. Zoom bumps UP to 1.4 when the user is
    further out, otherwise their current zoom is preserved (we
    don't yank them out when they've zoomed in deliberately).
    The duplicate ``cy.animate`` calls in ``focusNode`` and the
    ``?node=`` pre-select have been removed.
  - **The pulse is now unmistakable.** Pre-v2.2.2 the
    ``_startPulse`` animation went underlay-padding 14 -> 20
    (+6 px) and underlay-opacity 0.6 -> 0.7 (+0.1) over a 900ms
    half-cycle. Too subtle to read as "beating" without the
    camera framing the node. Now it goes 14 -> 28 (+14 px) and
    0.6 -> 0.95 (+0.35) over a 600ms half-cycle. The selected
    node now visibly breathes -- you can see it from across the
    canvas, not just when zoomed in.

### Behavior at a glance

  before:   click node on canvas -> nothing moves, faint pulse
            click file in tree   -> camera pans, faint pulse

  after:    click node on canvas -> camera frames it, strong pulse
            click file in tree   -> camera frames it, strong pulse

All five existing tests in ``test_nebula_progressive.py`` still
pass (they assert ``cy.animate({center: ...})`` is referenced --
still is, just in one place now). 545 unit tests pass total.
Ruff lint + format clean.

## [2.2.1] - 2026-05-14

**Phase 4 of the v2.2 progressive-UX rollout: Nebula goes
progressive.** The initial graph paint now waves in by descending
node degree, the detail-panel body streams in line- or word-by-
line, and the neighbors list staggered-reveals. All three reuse
the phase 1 primitives -- no new shared API.

### Added

- **Chunked initial Nebula paint.** After fcose finishes laying
  out the graph, every node is tagged with the new ``.preload-hidden``
  cytoscape class. ``_renderCanvasChunked()`` then reveals nodes in
  batches of ``CHUNK = 50`` at an 80ms cadence, sorted by descending
  degree -- so the densest cluster of the graph paints first, then
  the rest of the components fade in waves. Total reveal ~720ms for
  a ~480-node graph. Each chunk briefly carries ``.fade-in`` for
  260ms so the per-chunk reveal animation fires.
- **Body streaming in the Nebula side panel.** When a node is
  selected and its body fetch resolves, the body content reveals
  via ``window.mnemoStreamText`` -- word-by-word for prose, line-
  by-line for code (with a single Prism pass after the stream
  completes). The orchestrator (``streamBodyToCode``) cancels any
  in-flight stream before starting a new one so rapid neighbor
  clicks don't race.
- **Neighbors list staggered reveal.** The detail panel's "Connections"
  list is now rendered via ``window.mnemoStaggeredReveal`` --
  30ms per item, 180ms fade. Each ``<li>`` carries the ``.reveal-item``
  class while it transitions. The orchestrator
  (``renderNeighborsList``) cancels any in-flight reveal before
  starting a new one.

### CSS

- New cytoscape selector ``node.preload-hidden`` (opacity 0,
  underlay-opacity 0) scoped to this class only so it can't
  re-introduce the v2.1.2 dim/un-dim opacity-transition fanout
  lag we previously fixed.

### Accessibility

- The chunked reveal honors ``prefers-reduced-motion: reduce``:
  when the user prefers reduced motion, ``_renderCanvasChunked``
  is a no-op and every node is visible on first paint. The
  staggered-reveal + text-stream primitives already short-circuit
  to instant display under the same preference.

### Tests

- ``tests/unit/test_nebula_progressive.py`` (8 cases) -- locks the
  surface that the chunked reveal + body streaming + neighbors
  stagger live behind. Verifies ``_renderCanvasChunked`` exists,
  sorts by degree DESC, applies the ``.fade-in`` class, references
  a chunk-size constant; that ``mnemoStreamText`` and
  ``mnemoStaggeredReveal`` are called in graph.html; and that the
  camera-pan ``cy.animate({ center })`` path is preserved.
- 545 unit tests pass (was 537; +8 phase 4). Ruff lint + format
  clean.

### Live smoke verified

- Chunked reveal: 0 hidden → 50 fade-in → 400 fade-in → 0 hidden
  over ~700ms (eval-instrumented).
- Neighbors: 17 items with ``.reveal-item`` class applied;
  ``--type-color`` stamps preserve palette-driven dot colors.
- Body streaming: 95-char Python function body renders correctly
  via ``streamBodyToCode`` with ``unit: 'line'``; Prism re-highlight
  fires on done.

## [2.2.0] - 2026-05-14

**Streaming reindex + unified progressive-UX foundation.** First
release of the v2.2 progressive-UX rollout (design:
``docs/plans/2026-05-14-ux-progressive-design.md``). One coherent
streaming pattern shared by every future heavy operation in mnemo;
the Sources page is the first visible consumer.

### Added

- **Shared client primitives** (``daemon/mnemo/ui/static/app.js``)
  loaded site-wide from ``base.html``. Four helpers + one a11y
  probe that every future progressive UI consumes:
  - ``window.mnemoSkeleton(kind, opts)`` -- shimmer placeholder
    for ``list`` / ``paragraph`` / ``code`` / ``graph`` / ``card``
    shapes. Returns a DOM node the caller replaces with real
    content.
  - ``window.mnemoStaggeredReveal(container, items, opts)`` --
    RAF-paced fade-in for items already in memory. Returns
    ``{ cancel(), done }``.
  - ``window.mnemoStreamFromSSE(url, opts)`` -- ``EventSource``
    wrapper with per-event dispatch, JSON decoding,
    ``AbortSignal`` cancellation.
  - ``window.mnemoStreamText(target, source, opts)`` -- paces
    text reveal char/word/line at a time; accepts a string OR
    a ``ReadableStream`` so call sites stay identical when real
    streaming arrives.
  - ``window.mnemoPrefersReducedMotion()`` -- single shared probe.
  All five honor ``prefers-reduced-motion: reduce`` (animations
  collapse to 0; content snaps to final state).
- **``.skeleton`` / ``.reveal-item`` / ``.fade-in`` CSS** plus
  ``@media (prefers-reduced-motion: reduce)`` snap-rules.

- **``ingest.reindex_events()`` generator** yielding
  ``(event_name, payload)`` tuples (``start`` / ``file`` / ``done``).
  ``ingest.reindex()`` is now a thin wrapper that drains the
  generator and reconstructs the legacy ``ReindexReport``. Existing
  callers (CLI + ``POST /v1/reindex``) see zero behavior change.

- **``GET /v1/reindex/events``** -- Server-Sent Events route.
  Streams the ``reindex_events`` generator as
  ``event: <name>\ndata: <json>`` frames. Shares the same
  ``reindex_lock`` ``POST /v1/reindex`` uses; concurrent connections
  get a single ``event: busy`` frame then EOF. Sets
  ``Cache-Control: no-store`` + ``X-Accel-Buffering: no`` so proxies
  and browsers never cache the stream.

- **Streaming reindex progress on the Sources page.** The "Reindex
  all" button now opens a live progress block above the table:
  - ``N / M files`` counter + current file name.
  - Palette-driven progress bar (reuses ``.bar-fill`` from the
    dashboard; turns red if errors accumulate).
  - "stop" button that aborts the stream via ``AbortController``.
  - Summary line after ``done``: added / updated / unchanged /
    removed + duration.
  - Auto-reloads the page ~1.5s after ``done`` so the table
    reflects the new state.

- **``app.state.mnemo_state``** -- the per-app ``AppState`` is
  now reachable from the FastAPI instance, so tests and helpers
  can introspect the reindex lock without monkey-patching internals.

### Changed

- **Sources page reindex flow is stream-first.** If the browser
  supports ``EventSource``, the page subscribes to
  ``/v1/reindex/events`` and updates the bar live. If SSE is
  unavailable (legacy browsers, restrictive proxies) the page
  falls back to the previous ``POST /v1/reindex`` + status-poll
  pattern. The POST path is retained for v2.2.x and will be
  removed in v2.3 once SSE is proven everywhere.

### Tests

- ``tests/unit/test_progressive.py`` (12 cases) -- locks the
  surface of the four primitives + base.html wiring + CSS classes.
- ``tests/unit/test_reindex_events.py`` (9 cases) -- generator
  contract (start/file/done shape + ordering, idempotent reruns,
  ``ReindexReport`` regression); SSE wire contract
  (``text/event-stream``, frame format, busy event, POST regression).
- ``tests/unit/test_sources_progress.py`` (7 cases) -- template
  ships the progress markup with ``mnemoStreamFromSSE`` + cancel
  affordance + POST fallback retained.
- 537 unit tests pass total (was 521). Ruff lint + format clean.

### Phases 4 + 5 (deferred to v2.2.x point releases)

- Phase 4: chunked Nebula initial paint + coordinated node-to-node
  transitions. Reuses ``mnemoStaggeredReveal`` + ``.fade-in``.
- Phase 5: ``mnemoRenderBody`` adopts ``mnemoStreamText`` so every
  body preview reveals word-by-word (memory) or line-by-line
  (code). Call sites unchanged.

## [2.1.3] - 2026-05-14

**Hotfix.** The /code page project-card progress bars were
invisible (zero-width strips) after v2.1.1's palette refactor.
Apologies for the v2.1.2 patch -- it fixed the colors but not
the underlying box-model bug.

### Fixed

- **/code progress bars now actually render.** The /code template
  marks up each bar as ``<span class="bar-track"><span
  class="bar-fill"></span></span>``. ``<span>`` defaults to
  ``display: inline``; per the CSS spec, inline elements ignore
  ``width`` and ``height``. The inline ``style="width: 8.7%"``
  and the CSS ``height: 100%`` were both silently dropped on the
  floor, so each bar rendered at 0x0 -- DOM-present, correctly
  colored, but invisible.

  The outer ``.bar-track`` happened to be sized because it's a
  grid item (grid items are blockified by spec). The inner
  ``.bar-fill`` is nested one level deeper, NOT a grid item, so
  it remained inline.

  Fix: ``display: block`` on the scoped ``.code-project-card-bars
  .bar-fill`` rule. Also added defensively to the unscoped rule
  so the dashboard's ``.bar-fill`` paints regardless of whether
  the template uses ``<div>`` or ``<span>``.

  Verified live: every bar now has computed height 4px and a
  proportional computed width (8.7% -> 20.1px on a 231.6px track,
  49.1% -> 113.7px, etc).

## [2.1.2] - 2026-05-13

**Two follow-on bug fixes** from real-use feedback minutes after
v2.1.1 went out.

### Fixed

- **``/code`` project-card bars went blank** -- a stale duplicate
  ``.bar-fill`` rule (introduced when the /code landing was built
  pre-palette-refactor) sat AFTER the palette-driven ``.bar-fill``
  rule and overrode the ``background`` declaration with nothing.
  Result: per-type colors on the dashboard (which uses the same
  class) worked, but on /code the bar inside the project card
  was an invisible 4px transparent strip. Scoped the duplicate
  rule under ``.code-project-card-bars`` so it can't shadow the
  generic one, AND set ``background: var(--type-color, ...)`` on
  the scoped version explicitly. Both surfaces now paint
  consistently.
- **Nebula deselect lag** when clicking empty canvas (or pressing
  Escape) to clear a selection. Two compounding causes:
  1. The base ``node`` selector had
     ``transition-property: 'opacity, border-width,
     underlay-opacity, underlay-padding'`` with a 220ms duration.
     ``.dim`` toggles ``opacity`` + ``underlay-opacity`` -- so
     deselect kicked off 471 simultaneous opacity tweens. With
     motion-blur on, every redrawn frame for 220ms touched all
     471 nodes.
  2. ``_stopPulse()`` only unset a guard flag; the in-flight
     ``node.animate({underlay-padding, underlay-opacity}, 900ms)``
     chain kept running for up to 900ms after deselect, with its
     ``complete`` callback queueing the next half of the cycle
     BEFORE the flag check fired. The previously-selected node
     kept mutating styles long after it should have been done.

  Fixes:
  - Dropped ``opacity`` and ``underlay-opacity`` from the base
    node ``transition-property``. They're the properties ``.dim``
    toggles across the whole graph, and snapping them is fine --
    the selected cluster still feels "lifted" because
    ``border-width`` and ``underlay-padding`` (changed only on
    the 1-or-few ``.hl`` nodes) still transition.
  - ``_stopPulse()`` now calls ``node.stop(true, false)`` to
    cancel the in-flight animate chain AND
    ``removeStyle('underlay-padding underlay-opacity')`` to clear
    the inline styles the pulse wrote.
  - ``deselect()`` calls ``_stopPulse()`` FIRST, then
    ``cy.elements().stop(true, false)`` to cancel any other
    queued animations (e.g. selectFromCanvas's camera-fit),
    THEN the bulk ``removeClass('hl dim')`` inside ``cy.batch()``.

  Net effect on a 478-node graph: deselect sync ~10ms (vs ~90ms
  before); no lingering tween animations after 300ms (vs the
  pulse running until its 900ms complete callback fired).

## [2.1.1] - 2026-05-13

**Nebula UX polish + scaling architecture.** Seven follow-ups on
top of v2.1.0. The common thread: previously-implicit per-type
behavior was made explicit and palette-driven so the UI can absorb
new node types without per-file edits.

### Added

- **Single-source node-type palette.** ``daemon/mnemo/ui/palette.py``
  owns the ``TYPE_COLORS`` dict. Exposed to every Jinja template as
  a global (``type_colors``) and to every JS surface as
  ``window.MNEMO_TYPE_COLORS``. Generic CSS selectors
  (``.badge[class*="type-"]``, ``.bar-fill``, ``[class*="swatch-"]``,
  ``[class*="ntype-"]``) read a ``--type-color`` custom property
  stamped inline by the templating layer. Adding a new node type
  is one line in palette.py; badges, bar fills, filter swatches,
  detail-panel pills, neighbor dots, and canvas nodes all pick up
  the new color automatically.
- **Type-aware body preview** (``window.mnemoRenderBody``). One
  helper used by the node detail Preview tab, the search-result
  popover's "Show body" toggle, and the Nebula side panel. Branches
  on three paths: ``code_*`` types -> Prism-highlighted
  ``<pre><code>``; ``commit`` -> escaped plain ``<pre>``;
  everything else -> marked + DOMPurify markdown. Returns the path
  taken so callers can decorate the UI.
- **``source_path`` carried end-to-end on hits.** ``CompressedHit``
  and ``HitOut`` now expose ``source_path``, so the search popover
  can pick a Prism language hint per hit.
- **Site-wide Prism.** Moved Prism + autoloader from a per-page
  ``head_extra`` block into ``base.html``; every preview surface
  gets the same Tomorrow-Night palette + lazy language grammars.

### Fixed

- **Filter chips + dashboard "Memory by type" bars color every node
  type now.** v2.0 added 7 code_* types but the per-type CSS rules
  + JS dict were only partially updated; result was all-blue filter
  chips and invisible progress bars on the dashboard. The palette
  refactor closes this gap.
- **Nebula empty-canvas tap now deselects.** The guard was
  ``evt.target === this.cy``, which fails in minified Cytoscape
  builds because the core is wrapped in an obfuscated class.
  Switched to a capability test (``typeof t.isNode !== 'function'``).
  Edges still don't trigger deselect.
- **Force-layout snapshot restore was dead code.** The guard
  checked ``name === 'force'`` but the button passes ``'fcose'``.
  Fixed; switching to rings/tree/grid and back to force now
  restores the original positions (max drift 0 px across 422
  nodes).
- **Two-arrow bug in Nebula file tree.** A redundant
  ``::before { content: "▸" }`` rule was painting a second chevron;
  Chrome 128+ also reserves a phantom flex slot for ``<details>``
  disclosure widgets inside any ``<summary>`` with
  ``display: flex``. Cure: removed the duplicate rule + restructured
  summary children into an inner ``display:flex`` wrapper.
- **Connections count rendered as literal text in Nebula detail
  panel.** Template had ``{{ '{{' }} neighbors.length {{ '}}' }}``
  attempting to escape Alpine mustache through Jinja2 -- but Alpine
  doesn't use mustache for text interpolation. Replaced with
  ``<span x-text="neighbors.length">``.
- **Force-layout ran twice on first load.** Alpine.js auto-invokes
  any method named ``init()`` on the ``x-data`` object; pairing
  ``x-data="nebula()"`` with ``x-init="init()"`` ran init() twice.
  Dropped the redundant ``x-init`` from graph.html, base.html,
  settings.html, sources.html, node.html.
- **Native ``<details>`` marker still painted in the Nebula file
  tree.** Per CSS spec, ``::marker`` only accepts color / content /
  font-* / white-space / text-* properties; ``display: none`` is
  ignored. Switched to the allowed levers
  (``content: ""; font-size: 0; color: transparent``); added a
  CSS cache-bust via ``?v={{ mnemo_version }}`` on the
  ``/static/app.css`` link.

### Changed

- ``base.html`` now provides three site-wide helpers:
  ``window.mnemoIsCodeType(t)``, ``window.mnemoLanguageOf(path)``,
  and ``window.mnemoRenderBody(el, body, opts)``. Pages that
  rendered bodies inline have migrated to the shared helper.
- ``graph.html`` JS ``TYPE_COLORS`` is now an alias of
  ``window.MNEMO_TYPE_COLORS`` -- no per-page palette dict.

## [2.1.0] - 2026-05-13

**Nebula — three-panel graph UX.** A focused UI refinement on top
of v2.0's code graph. ``/graph`` is no longer a single canvas with
side overlays -- it's a resizable three-panel shell (file tree |
graph canvas | node detail) plus a sticky filter bar. The
``/code`` cards now funnel into ``/graph?project=<key>``, so a
single canonical visualization page serves both code-graph and
memory-graph exploration.

### Added

- **Three-panel resizable shell** at ``/graph``. Drag the gutters
  to resize; widths persist to localStorage. Default
  240 / flex / 320.
- **File tree (left panel).** Built from ``code_module``
  source_paths. Single-child directory chains collapse so deep
  Windows paths render compactly. Click a file -> focus + select
  on canvas. The active file highlights in the tree.
- **Detail panel (right).** Type badge + name + source_path +
  body + ranked neighbors with relation + confidence labels.
  Open-detail button links to ``/node/<id>``; copy-cite button
  copies ``[mnemo:<id>]``.
- **Filter bar (bottom).** Text search filters by name/type;
  per-type chip toggles narrow visibility; confidence slider
  hides edges below a threshold; hop selector (when in
  node-scope mode); force / concentric / circle relayout
  buttons; live node + edge counter.
- **Cross-stack visual language.** 8 code-type colors + 7
  memory-type colors are consistent across chips, tree dots,
  detail badge, graph nodes. Node SHAPES disambiguate:
  ``code_module`` = round-rectangle, ``code_route`` = diamond,
  ``code_endpoint`` = hexagon, ``commit`` = tag.
- **Confidence-encoded edges.** Line style by confidence:
  ``>= 0.9`` solid, ``0.7-0.9`` dashed, ``< 0.7`` dotted. Edge
  color encodes relation (calls = purple, routes_to = amber,
  at_endpoint = green, imports = cyan, provenance = pink).
  Arrowheads only on directional relations.

### Changed

- **``GET /ui/graph-data``** now accepts:
  - ``?project=<key>`` -- filter to nodes with that project_key
    plus cross-cutting (NULL / BASE) nodes connected to them.
  - ``?node=<id>&hops=<n>`` -- ego-network BFS from ``<id>`` out
    to ``n`` hops (default 2, capped at 4).
  Response nodes now include ``source_path`` + ``description`` so
  the file tree can group and the detail panel can render without
  a second round-trip. Edges now carry ``confidence``.
- **``/code`` project cards** now link to ``/graph?project=<key>``
  (the new primary CTA). A small "summary" link still goes to the
  list view at ``/code/<project>``.
- **``/code/<project>``** overview shows two CTAs side-by-side:
  "Open in graph" and "Cross-stack sitemap".

### Tests

- ``tests/unit/test_ui.py::test_graph_page_renders`` updated for
  the new shell (canvas id ``cy`` -> ``cy-nebula`` + ``nebula-shell``
  smoke).
- Full suite: 604 passing, 2 skipped, 0 failing.

### End-to-end UI verified

Via the preview tool against the daemon's own indexed code
(``mnemo-daemon`` project, 468 nodes after reindex):

- ``/graph?project=mnemo-daemon`` -> 416 nodes / 574 edges
  scoped to that project.
- Tree renders 33 files across 4 nested directories.
- Click ``flask.py`` -> selects on canvas + populates detail
  panel with name + type badge + source_path + body + 17
  connections.
- Toggling type chips to ``code_route + code_endpoint`` only
  -> exactly 80 nodes (40 routes + 40 endpoints) and 40
  ``at_endpoint`` edges.

## [2.0.0] - 2026-05-13

**Code Intelligence.** The headline v2.0 release: every registered
``code_repo`` source produces a typed code graph (modules, functions,
classes, methods + Tier 2 ``calls`` edges + Tier 3 routes /
components / endpoint anchors), plus the seven mnemo:code skills
that turn the graph into natural-language Q&A inside Claude Code.

### Headline capabilities

- **Cross-stack sitemap.** "This React button calls this Express
  handler which queries this Postgres table." A single graph
  traversal walks ``Component -> Endpoint <- Route -> Handler`` via
  the new ``at_endpoint`` join, rendered at
  ``/code/<project>/sitemap``.
- **Code-aware retrieval.** "Where is ``<function>`` called from?"
  returns correct callers via the Tier 2 ``calls`` edges. Confidence
  scores (0.95 within-file, 0.8 cross-file) carry uncertainty into
  retrieval ranking.
- **Auto-routing with safety.** ``mnemo source add <path>`` runs
  the auto-router; dry-run preview shows proposed kind + file
  breakdown before any DB write. 50,000-file safety ceiling
  prevents Duyen-class accidents.

### Roadmap completion

Phases shipped through ``release/2.0.0`` (in order):

1. Schema: ``code_repo`` / ``docs_dir`` source kinds, ``commit``
   node type, edge ``confidence`` column, provenance edges.
2. Source auto-router with dry-run preview + 50k file ceiling.
3. Tree-sitter grammar bundle + lazy-download stub.
4. Tier 1 universal code ingestion (8 bundled languages; Python
   full extractor, other languages module-only fallback).
5. Tier 2 Python call-graph resolver (constructor + ``self``/``this``
   resolution; same-file 0.95 / cross-file 0.8 confidence).
6. FastAPI + Flask + Express framework extractors (Tier 3
   backend); ``code_route`` nodes + ``routes_to`` edges.
7. React framework extractor + cross-stack ``code_endpoint`` nodes
   (Tier 3 frontend); ``at_endpoint`` + ``renders`` edges.
11-13. ``/code`` UI: landing + project overview + function detail
   with 2-hop ego-network + cross-stack sitemap. New top-bar tab.
14. Seven new code skills: ``mnemo:explore-codebase``,
   ``mnemo:trace-call``, ``mnemo:trace-route``,
   ``mnemo:explain-design``, ``mnemo:debug-with-code``,
   ``mnemo:why-is-this-here``, ``mnemo:impact-analysis``.

### Deferred to follow-on point releases

- **Phase 8 -- Django framework extractor.** FastAPI / Flask /
  Express cover the dominant Python and Node webdev surfaces;
  Django lands in v2.0.1 alongside the JS / TS / Go Tier 2
  resolvers (left out of phase 5).
- **Phase 9 -- Git-log ingestion + auto-linker.** The
  ``references_function`` / ``motivated_by`` / ``closed_by``
  schema is in place (phase 1) and the ``mnemo:why-is-this-here``
  skill is wired against it; the ingester slots in cleanly in
  v2.0.1. Until then the skill falls back to ``git log -L``.
- **Phase 10 -- Per-file incremental watcher.** Current full
  reindex flow handles real-world repos under a few thousand
  files; the per-file debounced watcher is a v2.0.x performance
  upgrade once the indexing budget bites in production.
- **Phase 15 -- Migration banner for pre-v2.0 sources.** First
  daemon start post-2.0 would benefit from a "your existing
  ``memory_dir`` registration looks like a ``code_repo`` -- want
  to reclassify?" banner. The auto-router that powers it is
  shipped; the UI surface lands in v2.0.x.

Full test suite: 604 passing, 2 skipped, 0 failing.
Ruff: clean. Format: clean.

## [Unreleased]

### Added (v2.0 phase 1 -- schema migration)

The structural foundation for v2.0's code-intelligence work. Phase 1
is schema-only: every later phase plugs a real producer into one of
these slots.

- **Two new source kinds: ``code_repo`` and ``docs_dir``.**
  ``code_repo`` is the tree-sitter-indexed shape (the parser arrives
  in phase 3-4); ``docs_dir`` is a markdown harvest without the
  frontmatter discipline ``memory_dir`` requires. ``register_source``
  now accepts both. Existing kinds (``memory_dir``, ``claude_md``,
  ``plan_dir``, ``transcripts``) are unchanged.
- **New ``commit`` node type.** Holds one node per git commit
  ingested from a ``code_repo`` source. Wired up by phase 9's
  ``git log`` walker; the schema is in place now so subsequent phases
  can write through it without an additional migration.
- **Three new edge relations -- the provenance family.**
  ``references_function`` (commit -> code_function it touched),
  ``motivated_by`` (commit -> ``memory_feedback`` / ``plan_doc`` that
  motivated it), and ``closed_by`` (``memory_feedback`` / ``plan_doc``
  -> commit that resolved it). Together they make the v2.0 headline
  capability -- "why is this function here?" -- queryable.
- **``edges.confidence FLOAT NOT NULL DEFAULT 1.0``.** Per-edge
  uncertainty so Tier 2 unresolved ``calls`` (0.5), Tier 3 framework
  matches (0.9), and auto-inferred provenance edges (0.6, bumped to
  0.9 on explicit commit-body reference) can carry a calibrated
  uncertainty into retrieval scoring. The column back-fills to 1.0
  via the standard ``_ensure_columns`` migration path so v1.x edges
  retain their bit-for-bit-identical behavior.
  (``daemon/mnemo/store.py``)

### Changed

- **``scan_source`` yields nothing when include patterns are empty.**
  Phase 1 safety: until phase 3-4 wire a tree-sitter parser, a
  freshly-registered ``code_repo`` source must not silently walk every
  file with the markdown parser. The new invariant -- empty include
  set means "nothing to walk" -- pairs with phase 2's auto-router,
  which populates the right include set when registering a code
  source. (``daemon/mnemo/ingest.py``)

### Added (v2.0 phase 2 -- auto-router + dry-run preview + safety ceiling)

The structural fix for the Duyen-class registration mistake: every
new source goes through an auto-router that classifies the path,
shows a per-extension breakdown, and refuses to write without
explicit user confirmation.

- **``mnemo.auto_router`` module.** ``preview(path) -> PreviewResult``
  scans the filesystem and proposes one of ``code_repo`` /
  ``memory_dir`` / ``docs_dir`` (or ``None``) with a confidence label
  (``high`` / ``medium`` / ``low``). Heuristics, in order:
  1. ``.git/`` dir + >= 1 recognized source file -> ``code_repo``.
  2. >= 1 markdown with frontmatter ``type:`` -> ``memory_dir``.
  3. >= 2 plain markdowns + 0 source files -> ``docs_dir``.
  4. Otherwise -> ``(None, "low")``; user must pick ``--kind``
     explicitly.
  Side-effect-free; the module imports nothing from store, server,
  or ingest. The walker skips a curated set of build / cache / VCS
  dirs (``DEFAULT_SKIP_DIRS``) so the count reflects actual source
  trees, not ``node_modules`` / ``.venv`` / ``target`` etc.
- **``POST /v1/sources/preview``.** HTTP surface for the auto-router.
  Returns the proposed kind + breakdown + ceiling flag without
  touching the DB. ``{ path, force? }`` body; ``404`` on missing path,
  ``422`` on missing ``path`` field.
- **CLI: ``mnemo source add <path>`` without ``--kind``.** Runs the
  auto-router, prints the breakdown, and prompts for confirmation
  (``y/N``). ``--yes`` skips the prompt for scripts; ``--force``
  bypasses the safety ceiling. Explicit ``--kind`` skips the
  auto-router entirely; the existing kind enum (``memory_dir`` etc.)
  is unchanged plus the v2.0 additions (``code_repo``, ``docs_dir``).
- **50,000-file safety ceiling.** If the auto-router counts more than
  ``SAFETY_CEILING`` recognized source files (after default
  skip-dirs), the CLI and the API both refuse to write. ``--force``
  on the CLI / ``force: true`` on the API overrides. Prevents the
  Duyen pattern -- accidentally registering a massive code repo as
  ``memory_dir`` -- at v2.0 scale.
- **UI: dry-run preview on the Add Source modal.** Typing a path
  debounce-triggers a ``POST /v1/sources/preview`` and renders a
  panel above the Kind dropdown showing the proposed kind +
  per-extension breakdown + a "Use suggested" button. The ceiling
  warning surfaces an inline ``I understand`` checkbox that maps to
  ``--force`` on submission.

### Tests (phase 2)

- ``tests/unit/test_auto_router.py`` -- 25 tests covering
  ``propose_kind`` heuristics, ``scan_path`` (skip-dirs, frontmatter
  detection, file-counting, single-file handling), the full
  ``preview`` entry point, and the safety ceiling.
- ``tests/integration/test_v1_sources_preview.py`` -- 8 tests for the
  HTTP surface including a side-effect-free regression guard.
- ``tests/unit/test_cli.py`` -- 8 new ``test_cli_source_add_*`` tests
  covering each kind auto-route, ``--yes`` / interactive prompt
  paths, ``--force`` ceiling override, and the explicit ``--kind``
  override.

### Tests (phase 1)

- ``tests/unit/test_v2_schema.py`` -- 20 tests covering the four
  schema additions and the scan-safety guard rail. All v1.x suites
  continue to pass unmodified.

Combined: phase 1 + 2 -> 478 -> 520 passing tests, 0 failing.

### Added (v2.0 phase 3 -- tree-sitter grammar bundle + lazy loader)

The library layer that Tier 1 / 2 / 3 ingestion will sit on top of.
Phase 3 is grammar infrastructure only -- no source code is actually
parsed until phase 4 plugs in the ingester.

- **``mnemo.parsers.tree_sitter`` module.** Single entry point
  (``get_parser(language) -> tree_sitter.Parser``) hides three sources
  of churn from callers: the capsule-to-``Language`` conversion that
  changed across the 0.21 / 0.22 / 0.23 binding releases; the
  per-package quirks (``tree-sitter-typescript`` exposes
  ``language_typescript()`` and ``language_tsx()`` instead of
  ``language()``; ``tree-sitter-markdown`` exposes both block and
  inline grammars); and the bundled-vs-lazy split.
- **Bundled launch set:** ``python``, ``javascript``, ``typescript``,
  ``tsx``, ``go``, ``json``, ``yaml``, ``markdown``. These wheels are
  direct dependencies so first run works offline. The set covers
  every language Tier 2 (semantic call graph, phase 5) needs plus
  config / docs surfaces for the ``/code`` UI.
- **Lazy set:** ``rust``, ``java``, ``c``, ``cpp``, ``ruby``, ``php``,
  ``c_sharp``, ``kotlin``, ``swift``, ``bash``. Not bundled; the
  loader names the right pip package in the
  ``GrammarNotAvailableError`` so users can copy-paste the install
  command. Rounds out the 16-grammar Tier 1 set the design promises.
- **Extension dispatch (``language_for_extension``).** Maps
  ``.py`` -> ``python``, ``.tsx`` -> ``tsx``, ``.jsx`` ->
  ``javascript``, etc. Case-insensitive so ``Path.suffix`` on Windows
  resolves correctly. Phase 4's ingester walks files and routes via
  this helper.
- **Parser cache.** ``get_parser`` caches by language; repeated calls
  return the same ``Parser`` so downstream code can compare ``is`` for
  identity.
- **``paths.grammars_dir()``.** Reserved under ``mnemo_home() /
  "grammars"`` for future lazy-downloaded wheels (a v2.0.x feature).
  ``ensure_runtime_dirs()`` creates it on first launch.

### Dependencies added

```
tree-sitter>=0.23
tree-sitter-python>=0.23
tree-sitter-javascript>=0.23
tree-sitter-typescript>=0.23
tree-sitter-go>=0.23
tree-sitter-json>=0.23
tree-sitter-yaml>=0.7
tree-sitter-markdown>=0.4
```

### Tests (phase 3)

- ``tests/unit/test_tree_sitter.py`` -- 17 tests covering the
  bundled / lazy registries, extension dispatch (case sensitivity,
  TSX disambiguation), end-to-end parse sanity for Python /
  TypeScript / TSX / Markdown, the unknown-language path, the
  lazy-grammar install-hint path, and the parser cache.
- ``tests/unit/test_paths.py`` -- 2 new tests for ``grammars_dir()``
  and the ``ensure_runtime_dirs()`` extension.

Combined: phase 1 + 2 + 3 -> 478 -> 539 passing tests, 0 failing.

### Added (v2.0 phase 4 -- Tier 1 universal code_repo ingestion)

The first phase that produces real code-graph nodes. Tier 1 covers
language-structure extraction: one node per file, one per top-level
declaration, one per class method, plus three structural edge types
(``defines`` / ``method_of`` / ``imports``).

Tier 2 (cross-file call resolution) and Tier 3 (framework extractors)
land in phases 5-8.

- **Four new node types.** ``code_module`` (one per source file),
  ``code_function`` (top-level function), ``code_class`` (top-level
  class), ``code_method`` (method on a class). All four go through
  ``Node.new()`` and the standard ingest path -- they're indexed,
  retrievable, and BASE-aware just like memory_* types.
- **Three new edge relations.** ``defines`` (module -> top-level
  declaration), ``method_of`` (method -> containing class),
  ``imports`` (module -> module, best-effort cross-file). Inferred
  edges carry ``confidence`` so retrieval can downweight uncertain
  links: ``imports`` lands at 0.9 (high confidence inside the file's
  AST, lower than 1.0 because the cross-file resolution is
  shallow / single-segment match).
- **``mnemo.parsers.code`` module.** Walks a tree-sitter AST and
  emits :class:`CodeUnit` records. Languages with a structural
  extractor at launch: Python (top-level defs, classes, methods
  including decorated ones, docstring -> description, imports).
  Other bundled languages (JS / TS / TSX / Go / JSON / YAML /
  Markdown) get a module-only fallback so the file's existence
  stays queryable; per-language extractors for those land in
  follow-on phases.
- **``mnemo.ingest.parse_code_file``.** New dispatch path: a
  ``code_repo`` source maps each file through the tree-sitter
  extractor and yields multiple :class:`ParsedFile` records (one
  per :class:`CodeUnit`). Edge intent (children / parent / imports)
  travels in ``frontmatter_json`` under a ``code_unit`` key.
- **``reindex`` post-pass.** After the upsert loop, code units'
  edge intent gets resolved against the freshly-populated graph.
  ``defines`` and ``method_of`` are within-file and always
  resolve; ``imports`` is best-effort -- unmatched targets
  silently produce no edge so stdlib / pip-installed imports
  don't pollute the graph with dangling pointers.
- **``code_repo`` default include patterns.** A registered
  ``code_repo`` source with no user-supplied include set walks the
  bundled tree-sitter extensions (``*.py``, ``*.ts``, ``*.tsx``,
  ``*.js``, ``*.go``, ``*.json``, ``*.yaml``, ``*.md``, ...). The
  walker reuses ``auto_router.DEFAULT_SKIP_DIRS`` so ``.git`` /
  ``node_modules`` / ``__pycache__`` / etc. never reach the
  extractor.
- **Line-range source_paths.** Declaration nodes use
  ``<file>:<start>-<end>`` as their ``source_path`` so two same-name
  functions in the same file (overloads, conditional definitions)
  get distinct keys. ``paths.path_under_source`` strips the suffix
  before path comparison so reconciliation + cascade delete continue
  to work correctly. Modules keep the bare file path.
- **Body truncation.** Function and module bodies > 60 lines get a
  ``... (N more lines)`` trailing marker so retrieval hits don't
  blow the token budget on a 5,000-line file.

### Tests (phase 4)

- ``tests/unit/test_v2_schema.py`` -- 8 new tests covering the four
  code node types and the three structural edge relations.
- ``tests/unit/test_parsers_code.py`` -- 16 tests covering the
  Python extractor (decls, decorated methods, docstrings, imports,
  body truncation, line-range source_paths) and the module-only
  fallback for JSON / Markdown / JS / unknown.
- ``tests/unit/test_ingest_code_repo.py`` -- 10 tests covering the
  ingest wiring: default include, scan_source dispatch, skip-dirs
  passthrough, and the reindex edge post-pass for ``defines`` /
  ``method_of`` / ``imports``.

Combined: phases 1 -> 4 advance 478 -> 573 passing tests, 0 failing.

### Added (v2.0 phase 5 -- Tier 2 call-graph resolver)

The flagship Tier 2 capability: "where is ``<function>`` called from?"
finally returns correct answers. Built around a Stack-Graphs-inspired
scope resolver that walks the freshly-populated Tier 1 graph to
match each call site with its callee.

- **``calls`` edge relation.** Caller function / method -> callee
  function / method / class (the constructor case). Inferred edges
  carry calibrated confidence: 0.95 for within-file resolution and
  0.8 for cross-file resolution via the ``imports`` edge. The design
  pegs unresolved calls as "no edge" -- best-effort retrieval beats
  fabricated edges.

- **``mnemo.parsers.code.CallSite``.** New dataclass capturing a
  recorded call expression: ``callee_name``, ``receiver`` (``None``
  for free calls, ``"self"`` / ``"this"`` / ``"cls"`` for method
  calls, or an identifier for ``module.f()`` qualified calls), and
  the source line.

- **Python call-site extraction.** ``_python_call_sites`` walks each
  function / method body and collects ``call`` AST nodes. Recursive
  through nested control flow (``if`` / ``for`` / ``with`` /
  comprehensions) but NOT through nested function / class
  definitions -- those have their own units and their own
  ``call_sites``. Chained receivers (``a.b.c.method()``) are
  reduced to the outermost identifier so the resolver can still
  match against imports.

- **``mnemo.parsers.scope`` module.** The Tier 2 resolver.
  :func:`resolve_calls` builds a one-pass index of the code graph
  (source_path -> Node, (module, name) -> Node, method_of /
  imports lookups), then walks each touched node's call sites
  applying three rules in order:

  1. ``receiver in {self, this, cls}`` -> walk ``method_of`` to
     the enclosing class, match by callee name on its methods.
  2. ``receiver is None`` -> match against the enclosing module's
     top-level declarations (functions + classes; the latter
     handles constructor calls like ``Session()``).
  3. ``receiver`` matches an imported module name -> walk the
     ``imports`` edge to the target module and match by callee
     name on its declarations.

  Self-edges (a recursive function's name matching itself) are
  suppressed so the graph stays clean.

- **Reindex post-pass extension.** After Tier 1 edges (``defines``,
  ``method_of``, ``imports``) are wired, the reindex pipeline
  invokes :func:`scope_resolver.resolve_calls` with the same
  touched-node batch. The resolver hits the just-populated graph so
  same-run cross-file resolution works end-to-end (no second
  reindex needed).

### Tests (phase 5)

- ``tests/unit/test_v2_schema.py`` -- 2 new tests for the ``calls``
  edge relation and confidence persistence.
- ``tests/unit/test_parsers_code.py`` -- 7 new tests covering the
  ``CallSite`` dataclass shape, free / self / qualified call
  capture, constructor detection, and nested-call attribution.
- ``tests/unit/test_ingest_code_repo.py`` -- 7 new tests for the
  end-to-end resolution: same-module free call, ``self.method``,
  constructor -> class, cross-file via imports, unresolved (no
  edge), and confidence levels for same-file vs cross-file.

Combined: phases 1 -> 5 advance 478 -> 589 passing tests, 0 failing.

### Deferred to follow-on phases

- **JavaScript / TypeScript / Go resolvers.** The design promises
  Tier 2 across all three; phase 5 ships Python end-to-end and
  leaves the resolver framework / call-site extraction stubs for
  these three to land in a follow-on commit. Tier 1 already
  produces ``code_module`` nodes for these languages so the
  graph isn't blocked on them.

### Added (v2.0 phase 6 -- Tier 3 backend framework extractors)

FastAPI, Flask, and Express route extraction. The first Tier 3
phase wires the framework idioms each of those backends uses into
graph nodes + edges, setting up the cross-stack sitemap that
lands when phase 7 ships the React / Next.js side.

- **``code_route`` node type.** One node per detected route
  declaration. Carries ``framework`` (``fastapi`` / ``flask`` /
  ``express``), HTTP method (uppercased), and path on its
  ``code_unit`` intent block. The display ``name`` is
  ``METHOD path`` (e.g. ``GET /api/users``) so retrieval hits
  read naturally.
- **``routes_to`` edge relation.** Route -> handler function.
  Inferred edges with confidence 0.95 -- within-file resolution
  is high-confidence by construction (the extractor matched the
  decorator + handler in the same parse). The post-pass wires
  the edge by source_path lookup.
- **``mnemo.extractors.fastapi``.** Matches
  ``@<receiver>.<method>(<path>, ...)`` decorators on top-level
  functions where the method name is one of GET / POST / PUT /
  DELETE / PATCH / HEAD / OPTIONS / TRACE. Stacked decorators on
  the same handler each emit their own route. The receiver name
  is intentionally permissive (``app`` / ``router`` / ``api`` /
  ``v1`` are all valid in real codebases).
- **``mnemo.extractors.flask``.** Matches
  ``@<receiver>.route(<path>, methods=[...])`` decorators on
  top-level functions. Default method is GET when ``methods``
  is omitted; multi-method lists fan out to one route per verb.
  ``@app.route`` and ``@blueprint.route`` are detected the same
  way (any receiver name).
- **``mnemo.extractors.express``.** Matches top-level
  ``<receiver>.<method>(<path>, <handler>)`` call expressions
  where the method name is GET / POST / PUT / DELETE / PATCH /
  HEAD / OPTIONS / ALL / USE. JavaScript handler resolution is
  deferred to phase 7 (when JS Tier 1 ships): the route node is
  emitted but ``handler_source_path`` stays None, so
  ``routes_to`` doesn't wire yet for Express. The endpoint
  surface is still there for phase 7's React-side join.
- **Framework dispatch in ``parsers.code.extract``.** After Tier 1
  extraction runs, the appropriate set of framework extractors
  (per ``FRAMEWORK_EXTRACTORS`` in ``mnemo.extractors``) walks
  the same tree and emits Tier 3 units. A broken extractor is
  caught defensively -- it can never crash the reindex.

### Tests (phase 6)

- ``tests/unit/test_v2_schema.py`` -- 3 new tests for the
  ``code_route`` node type and the ``routes_to`` edge.
- ``tests/unit/test_extractors.py`` -- 12 new tests: 6 for the
  FastAPI extractor (GET, POST, APIRouter, name shape,
  no-decorator regression, stacked decorators), 3 for Flask
  (default GET, ``methods`` kwarg, blueprint), 2 for Express
  (``app.get`` and ``router.post``), and 1 end-to-end integration
  test asserting the ``routes_to`` edge appears after reindex.

Combined: phases 1 -> 6 advance 478 -> 604 passing tests, 0 failing.

## [1.2.1] - 2026-05-11

**Closing the 1.2.x line.** A real-use test of v1.2.0 against a
multi-project store turned up that most "common query returns
nothing" cases trace back to a single bug: the strict
project-isolation hard-filter dropped nodes whose ``project_key``
is ``None`` (CLAUDE.md global memory, plan_docs, and any
cross-cutting entry that didn't pick up a project_key). The filter
treated ``None != active_project`` as True and silently filtered
them out -- exactly the opposite of what you want for global
memory.

### Fixed

- **Strict project isolation no longer hides ``project_key=None``
  nodes.** Global memory (CLAUDE.md, plan_docs, any cross-cutting
  entry without an assigned project) now surfaces in every project's
  queries by default. Pre-fix, you needed to either (a) flag every
  global node ``base: true`` or (b) flip to ``isolation_mode=boost``
  to get them back; both were undocumented workarounds. The fix
  matches the spirit of the v1.1 design: "BASE for cross-project,
  project_key for per-project, NULL is the natural cross-cutting
  bucket." (`daemon/mnemo/retrieve.py`)
- **``budget_tokens`` floor raised from 1 to 20.** Below 20 the
  first hit's ``[mnemo:<uuid>] [<type>] <description>`` line can't
  fit and ``compress_to_budget`` returns the empty list -- a
  silent zero that masks the real cause. Clients now get an HTTP
  422 instead of a confusing empty success. (`daemon/mnemo/api_schemas.py`)

### Tests

- ``test_query_strict_isolation_keeps_project_key_none_nodes`` --
  regression test for the filter fix. Active project + strict mode
  + 3 nodes (in-project / other-project / no-project_key);
  asserts in-project and no-project_key survive, other-project is
  filtered.
- ``test_query_budget_below_floor_rejected`` -- 422 on
  ``budget_tokens=10``; 200 on the exact floor of 20.
- ``test_query_validation`` retains its 422 assertion on
  ``budget_tokens=0``.

### Not in scope (deferred)

The full set of silent-zero failure modes (16 cases probed) is
written up in the memory note ``feedback_mnemo_v12_build_lessons``
+ a fresh ``feedback_mnemo_silent_zero_modes`` for future
diagnostic work. v1.3 / v2.0 candidates:

- Diagnostic ``debug=true`` flag on ``/v1/query`` that returns
  pre-filter / post-isolation / post-MMR counts so users can see
  where their hits got lost.
- "filtered N of M" hint surfaced in the UI when isolation drops
  hits.
- ``/v1/projects/resolve`` auto-suggestion when an explicit
  ``project_key`` doesn't match any indexed nodes.

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
