# Progressive UX — unified streaming + staggering pattern

**Status:** design, validated through brainstorming session 2026-05-14.
**Target:** v2.2.0 (a multi-PR rollout across release/2.2.x).
**Owner:** mnemo core.

## Why

Four UX gaps shipped together because they share a root: heavy operations
without a feedback story. The user pasted them in one message because, in
their words, the system needs a "consistent, stable, scaleable" way of
showing work in flight.

| Gap | Today |
|---|---|
| Reindex progress | POST `/v1/reindex` blocks; UI shows a spinning button. No file-by-file detail; no ETA; you cannot tell whether mnemo is doing anything or hung. |
| Lazy Nebula | First paint of 478 nodes happens in one shot after fcose finishes; nothing visible for ~500-800ms. |
| Smooth body / detail | `mnemoRenderBody` pops the full body in a single tick; Prism colors the whole `<code>` block at once. The eye cannot track what loaded. |
| Smooth node-to-node | Clicking a neighbor snaps instantly: detail-panel content swaps, dim/hl flip, no transition. Feels jarring on a slow disk or after a context switch. |

Each one could be fixed in isolation, but the bandaids would diverge and
the next heavy operation (chat answers? graph re-ingest?) would need a
brand-new mechanism. We design one pattern, build it once, apply it to
all four surfaces.

## The unified pattern

Three phases, one mental model:

```
[Intent]      Skeleton appears in <100ms. Shape of expected content,
              shimmering placeholder rows / lines / nodes.
   |
   v
[Stream]      Real content arrives in CHUNKS. Each chunk fades in
              (180ms). Stagger between chunks (~30ms per item, or
              driven by server event arrival).
   |
   v
[Settle]     Skeleton fully replaced. Subtle fade-out on placeholders.
              Final state visible; user can interact.
```

Two delivery paths, same pattern:

* **A. Server-streamed** — data trickles in over the wire via Server-Sent
  Events. The client renders each chunk as it arrives.
* **B. Client-staggered** — data is already in memory; the client paces
  reveal with `requestAnimationFrame` + small delays.

Single accessibility floor: `prefers-reduced-motion: reduce` collapses
delays to 0 → everything snaps instantly to the final state.

## § 1 — Client primitives

A new module `daemon/mnemo/ui/static/app.js` loaded from `base.html` once
defines four helpers. Every page that does async UI uses them.

```js
// 1. Skeleton placeholder. Returns a DOM node that shimmers in the
//    shape of expected content. Replace it with real content via
//    .replaceWith(realNode) once data arrives.
window.mnemoSkeleton(kind, opts)
  // kind: 'list' | 'paragraph' | 'code' | 'graph' | 'card'
  // opts: { count?: 5, height?: '1.2em', className?: '' }

// 2. Staggered reveal. Renders items one-by-one with smooth fade-in.
//    Returns { cancel(), done }.
window.mnemoStaggeredReveal(containerEl, items, {
  renderOne: (item, i) => DOMNode,
  perItemDelayMs: 30,
  fadeInMs: 180,
  reducedMotion: 'auto',   // honors prefers-reduced-motion
})

// 3. SSE subscription with auto-reconnect + AbortController support.
//    Returns { cancel() }.
window.mnemoStreamFromSSE(url, {
  onEvent: (eventName, data) => {},
  signal: AbortSignal,
  onComplete: () => {},
  onError: (err) => {},
})

// 4. Streaming text reveal. Accepts a string OR a ReadableStream.
//    Walks characters/words/lines into the target with paced reveal.
//    Returns { cancel(), done }.
window.mnemoStreamText(targetEl, source, {
  unit: 'char' | 'word' | 'line',
  perUnitDelayMs: 12,
  formatLine: (line) => string,   // hook for per-line syntax highlight
})
```

Defaults are tuned for `~60fps` perceived smoothness: stagger 30ms,
text 12ms per word, fade-in 180ms. Tunable per call site.

**Why exactly these four:**

* `mnemoSkeleton` decouples "show intent" from data arrival. ANY async
  flow can start here.
* `mnemoStaggeredReveal` is the workhorse for graphs / lists when data is
  local. Pure RAF + CSS. No server changes needed.
* `mnemoStreamFromSSE` is the foundation for the reindex events stream
  and any future server push. One subscription helper, reused.
* `mnemoStreamText` is the body-typing helper. Wraps either a string (no
  streaming, paced reveal only) or a `ReadableStream` (true streaming)
  with the SAME API — call sites don't care which.

Single opinionated default: all four respect
`prefers-reduced-motion: reduce` via one shared check at module init.

## § 2 — Server-side: SSE for reindex

One new endpoint:

```
GET /v1/reindex/events     -> Server-Sent Events stream
```

Wire protocol (newline-delimited SSE events):

```
event: start
data: {"total_files": 47, "started_at": 1715712345}

event: file
data: {"idx": 1, "total": 47, "path": "memory/feedback_X.md",
       "status": "indexed", "added": 1, "updated": 0,
       "unchanged": 0, "errors": []}

event: file
data: {"idx": 2, "total": 47, ...}

event: done
data: {"added": 5, "updated": 0, "unchanged": 41, "removed": 1,
       "errors": [], "duration_ms": 12340}
```

**Why SSE, not WebSockets:**

* One-way (server → client) — exactly what we need.
* Plain HTTP. Works through every proxy + the existing FastAPI app. No
  upgrade dance.
* Auto-reconnect built into the browser `EventSource`.
* FastAPI's `StreamingResponse(content=async_generator(),
  media_type="text/event-stream")` pattern — no new dependency.

**Server code changes:**

* `mnemo/ingest.py` — refactor `reindex()` into a generator yielding
  `(event_name, payload_dict)` tuples per step. Existing synchronous
  callers consume the generator into a summary helper. Zero behavior
  change to today's `POST /v1/reindex` path.
* `mnemo/server.py` — new `GET /v1/reindex/events` route:
  * Acquires the same `reindex_lock` we already have.
  * Iterates the generator, encoding each event as
    `event: <name>\ndata: <json>\n\n`.
  * Releases the lock in a `finally`.
  * Second client connecting mid-flight gets a single `event: busy`
    then EOF — no fan-out (YAGNI; one daemon, one user).

**Cancellation:** client closes the EventSource → server's async iterator
hits a `ClientDisconnect` → `finally` cleans up. No explicit
`DELETE /v1/reindex` for now (YAGNI; ingestion is fast enough).

## § 3 — Sources page wiring (first end-to-end demo)

Replace the polling `/v1/reindex/status` indicator with a streaming bar:

```
┌─────────────────────────────────────────────────────────┐
│ Reindex          [● 14 / 47]  memory/feedback_X.md  ❚❚  │
│ ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  29%  │
└─────────────────────────────────────────────────────────┘
```

Markup (sources.html, inside the existing reindex section):

```html
<div class="reindex-progress" x-show="progress.active" x-cloak
     :class="{ 'reindex-done': progress.done }">
  <div class="reindex-progress-row">
    <span class="reindex-counter">
      <span class="dot pulse"></span>
      <span x-text="progress.idx + ' / ' + progress.total"></span>
    </span>
    <span class="reindex-current" x-text="progress.currentFile"></span>
    <button @click="cancelReindex()" :disabled="progress.done">stop</button>
  </div>
  <div class="bar-track">
    <div class="bar-fill"
         :style="'width: ' + (progress.total ? (progress.idx*100/progress.total) : 0) + '%;
                 --type-color: ' + (progress.errors.length ? '#f87171' : '#7ee7e0')">
    </div>
  </div>
  <p class="reindex-summary" x-show="progress.done" x-cloak
     x-text="`${progress.added} added · ${progress.updated} updated · ${progress.unchanged} unchanged · ${progress.removed} removed${progress.errors.length ? ' · ' + progress.errors.length + ' errors' : ''}`">
  </p>
</div>
```

Alpine wiring (sourcesPage factory):

```js
progress: { active: false, done: false, idx: 0, total: 0,
            currentFile: '', added: 0, updated: 0, unchanged: 0,
            removed: 0, errors: [] },

async reindex() {
  this.progress = { active: true, done: false, idx: 0, total: 0,
                    currentFile: 'starting...', added: 0,
                    updated: 0, unchanged: 0, removed: 0, errors: [] };
  this._reindexCtrl = new AbortController();
  this._reindexStream = window.mnemoStreamFromSSE(
    '/v1/reindex/events',
    {
      onEvent: (name, data) => {
        if (name === 'start')  this.progress.total = data.total_files;
        if (name === 'file') {
          this.progress.idx         = data.idx;
          this.progress.currentFile = data.path;
          if (data.errors?.length) this.progress.errors.push(...data.errors);
        }
        if (name === 'done') {
          Object.assign(this.progress, data, { done: true });
          setTimeout(() => { this.progress.active = false; }, 4000);
        }
      },
      signal: this._reindexCtrl.signal,
    },
  );
},

cancelReindex() { this._reindexCtrl?.abort(); },
```

The progress bar IS the same palette-driven `.bar-fill` primitive used on
the dashboard + /code, stamped with `--type-color: #7ee7e0` (or `#f87171`
on errors). One bar component, three call sites, single source.

**Behavior beats:**

1. Click "Reindex" → bar appears instantly at 0/0 "starting...".
2. First `start` event → total locks in; bar shows `1 / 47`.
3. Each `file` event → counter ticks; current-file crossfades (120ms);
   bar width tweens (300ms via existing CSS `transition: width`).
4. `done` event → bar settles to 100%; summary fades in; auto-dismiss 4s.
5. Click "stop" → `AbortController` fires; SSE closes; "cancelled" label.

## § 4 — Lazy Nebula + smooth node transitions

### Initial paint — chunked load with prioritization

```
T=0ms     Skeleton: dim canvas + 1 line "loading graph...".
          fetch('/ui/graph-data') kicks off.
T=~200ms  JSON arrives. Sort nodes by degree, descending.
T=210ms   chunk 1: top 50 highest-degree nodes + edges between them.
          cy.add() + fcose layout(animate: false). Sparse skeleton visible.
T=310ms   chunk 2: next 50 by degree. cy.add() + fcose(randomize: false).
          Existing nodes stay put; new ones nestle in.
          Each chunk fades opacity 0 -> 1 over 200ms via .fade-in class.
T=410ms   chunk 3, etc. ~7-9 chunks total at 50 nodes each.
T=~1000ms full graph rendered.
```

Critical: re-introduce a per-chunk `opacity` transition via a `.fade-in`
class (NOT in the base node selector — that was the lag source in v2.1.2).
The class is added at chunk insert and removed 260ms later, so the
transition fires only during reveal, never during dim/un-dim.

```js
async _renderCanvasChunked(elements) {
  const sortedNodes = elements
    .filter(e => !e.data.source)
    .sort((a, b) => degree(b) - degree(a));
  const edges = elements.filter(e => e.data.source);

  const CHUNK = 50;
  for (let i = 0; i < sortedNodes.length; i += CHUNK) {
    const slice = sortedNodes.slice(i, i + CHUNK);
    const sliceIds = new Set(slice.map(n => n.data.id));
    const sliceEdges = edges.filter(e =>
      sliceIds.has(e.data.source) && sliceIds.has(e.data.target));
    this.cy.batch(() => {
      const added = this.cy.add([...slice, ...sliceEdges]);
      added.addClass('fade-in');
      setTimeout(() => added.removeClass('fade-in'), 260);
    });
    if ((i / CHUNK) % 2 === 1 || i + CHUNK >= sortedNodes.length) {
      this.cy.layout({
        name: 'fcose', animate: false, randomize: false,
        nodeRepulsion: 6500, idealEdgeLength: 80, fit: true, padding: 50,
      }).run();
    }
    await new Promise(r => requestAnimationFrame(r));
  }
}
```

### Node-to-node transitions — coordinated cross-fade

```
T=0ms     User clicks neighbor "X".
T=0-80ms  detail-panel body + neighbors-list fade to 0.3 opacity (CSS).
          Camera tween starts: cy.animate({center, zoom}, duration: 200).
T=80ms    old .hl removed; this.selected = X; fetch /v1/nodes/X starts.
T=80-180ms new .hl applied to X's neighborhood (no transition; instant).
          Camera arrives at X.
T=180ms   detail-panel content swaps (name/type already in node.data).
          Body renders via mnemoStreamText (word-by-word) as fetch
          response arrives.
T=180-380ms neighbors list staggered-reveals via mnemoStaggeredReveal
            (30ms per item, fade 180ms).
```

Orchestrator method (replaces today's `focusNode` body):

```js
async focusNode(id) {
  if (!this.cy || this.selected?.id === id) return;
  const target = this.cy.getElementById(id);
  if (!target.length) { this.contextNodeId = id; return this.reload(); }

  this._detailFadeOut();   // CSS class toggle, 80ms

  this.cy.animate({ center: { eles: target }, zoom: 1.4 },
                  { duration: 200, easing: 'ease-out' });
  await wait(80);
  this.selectFromCanvas(target);   // existing path (snap hl/dim)
}
```

Both pieces reuse `mnemoStaggeredReveal` (neighbor list) and
`mnemoStreamText` (body) from § 1.

**Performance budget:**

* Initial paint visible in ~210ms (was ~500-800ms).
* Node-to-node transition ~200-380ms (was instant snap, jarring).

Same overall throughput, much better feel.

## § 5 — Body streaming + cross-cutting concerns

### Body streaming everywhere

`mnemoRenderBody` keeps its current 3-branch decision tree but uses
`mnemoStreamText` as the inner reveal pipe:

| Branch | Unit | Effect |
|---|---|---|
| `code_*` | line | each source line slides in (8ms/line); Prism re-highlights every 8 lines so colors fill in chunks |
| `commit` | line | escaped pre line-by-line |
| markdown | word | walk text-node descendants of the marked HTML; reveal word spans 20ms apart |

Wrapper signature is **identical** to today's call sites:

```js
window.mnemoRenderBody(targetEl, body, { type, sourcePath })
```

Every existing call site (`node.html` Preview tab, `_search_results.html`
Show-body, Nebula side panel) gets streaming for free — zero call-site
changes.

For true server-streamed bodies (later): if a body fetch is over
`Transfer-Encoding: chunked`, feed the `ReadableStream` directly into
`mnemoStreamText`. Same helper, different source. User feels no
difference once data starts arriving.

### Cross-cutting concerns

**1. `prefers-reduced-motion`** — one check in `mnemoProgressive` module
init:

```js
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
// All four primitives consult this. When true:
//   perItemDelayMs / perUnitDelayMs / fadeInMs / camera duration → 0.
// Everything snaps to final state.
```

A11y floor achieved in ONE branch.

**2. Cancellation** — every primitive returns `{ cancel() }`:

* `mnemoStaggeredReveal.cancel()` — clears pending RAFs.
* `mnemoStreamText.cancel()` — clears the pending timer + renders the
  full content immediately. No half-rendered state.
* `mnemoStreamFromSSE.cancel()` / `AbortController` — closes the
  EventSource.

When the user clicks a different node mid-transition, the previous
focusNode's stream/stagger are cancelled before the new one starts.
No racing fades.

**3. Error handling** — three failure modes, each visible:

| Failure | Behavior |
|---|---|
| SSE connection drops | bar turns amber; reindex falls back to polling `/v1/reindex/status` until done |
| Body fetch fails | `mnemoStreamText` finishes immediately with whatever is in hand; a trailing "…" indicator turns red |
| Chunked layout error (cytoscape throws) | abort chunking; fall back to one-shot render with a toast |

**4. Cache** — `/v1/reindex/events` sets `Cache-Control: no-store` —
never cache event streams. Browser `EventSource` already handles per-event
cache headers correctly.

### File layout

```
daemon/mnemo/ui/static/
  app.css                  (existing) + .fade-in, .skeleton, .reveal-item rules
  app.js                   (NEW) — defines window.mnemoProgressive + 4 primitives;
                                    loaded via <script defer> in base.html.

daemon/mnemo/ui/templates/
  base.html                + <script src="/static/app.js?v={{ mnemo_version }}" defer></script>
  sources.html             adopts the new reindex bar (§ 3)
  graph.html               _renderCanvasChunked + focusNode orchestration (§ 4)
  node.html /
  _search_results.html     (no changes — call sites unchanged)

daemon/mnemo/ingest.py     reindex() becomes a generator (§ 2)
daemon/mnemo/server.py     + GET /v1/reindex/events (SSE)
                           POST /v1/reindex unchanged

daemon/tests/unit/
  test_progressive.py      (NEW) — ingest.reindex generator + SSE wire format
```

### What we're NOT building (YAGNI)

* No WebSockets (SSE is enough).
* No SSE for query results (popover is already fast; revisit with chat).
* No true chunked body streaming over HTTP yet — fake it client-side via
  setTimeout. Switch when a real producer exists.
* No multi-client fan-out (single user, single browser tab).
* No sub-file embedding progress.

All can land later behind the SAME primitive API. Today's design doesn't
lock them out.

### Acceptance criteria

| Gap | Pass when |
|---|---|
| Reindex progress | Sources page shows `N / M` + current file + colored bar within 200ms of click; updates per-file; auto-dismisses after `done` |
| Lazy Nebula load | First 50 nodes visible within ~300ms of /graph; remaining chunks fade in every ~100ms; no jank during chunked layout |
| Smooth body | `mnemoRenderBody` reveals word-by-word for memory, line-by-line for code; Prism highlights in chunks; `prefers-reduced-motion: reduce` honored |
| Smooth redirects | Click a neighbor → cross-fade panel + camera pan in parallel; new body streams in over the fade-completion |

## Implementation phases

Each phase is one PR onto a release/2.2.x branch. CI gates each.

1. **`feat(ui): mnemoProgressive primitives + .skeleton/.fade-in CSS`** —
   pure client-side; no server changes. Lands the four helpers + JSDOM
   tests. Visible nowhere yet; demonstrated only in tests.
2. **`feat(daemon): reindex generator + SSE /v1/reindex/events`** —
   server-side only. New endpoint + ingestion refactor + unit/integration
   tests. UI still uses the existing synchronous path.
3. **`feat(ui): streaming reindex progress on Sources page`** — wires
   § 3 end-to-end. The pattern's first visible win.
4. **`feat(ui): chunked Nebula initial paint + coordinated node
   transitions`** — § 4 in full.
5. **`feat(ui): mnemoRenderBody adopts mnemoStreamText`** — § 5 body
   streaming. Drops in below existing call sites; no template changes.

Each phase ships independently; each is a complete unit of behavior.
At any cut point, mnemo works.

## Roll-back plan

Each phase is feature-additive (no removal of existing behavior until the
new path proves out):

* Phase 1 ships dormant primitives. If buggy, no UI uses them; remove the
  module and the page works as before.
* Phase 2 adds a new endpoint. POST `/v1/reindex` keeps working
  unchanged. Roll back = revert the route + ingest changes.
* Phase 3 wraps the Sources page's `reindex()` in a new path. The old
  POST + status-polling code stays as a fallback for one minor release.
* Phases 4 + 5 likewise: each adds new code paths; existing call sites
  keep working until the migration is complete.

Worst case (any phase): revert that phase's commit, ship a patch
release. No data migration, no schema change.
