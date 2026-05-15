# Nebula on Cosmograph (WebGL) — v2.6.2 design

Date: 2026-05-15
Status: approved (4-section brainstorm, user-validated)
Supersedes: the v2.6.0/2.6.1 cytoscape canvas approach for `/graph`

## Problem

v2.6.1 capped the Nebula canvas to the 2 000 most-connected nodes
because cytoscape's canvas renderer physically cannot paint
10 845 nodes / 15 418 edges smoothly (proven across 3 measured
tuning attempts). The cap created four user-facing failures:

1. **Misleads the future v3 chat companion.** The user's mental
   model is "what Nebula shows == what chat can reference". A
   capped canvas means the companion's connection analysis looks
   incomplete / wrong.
2. **Ring (concentric) layout doesn't match the "nebula" theme.**
   The user wants an organic, cloud-like star field, not rigid
   rings.
3. **Broken navigation.** Clicking a capped-out node from the file
   tree runs `focusNode → reload → ?node=` ego-fetch: the whole
   canvas swaps to one standalone node, the tree rebuilds to that
   single file, and there is no way back.
4. **Blur-disappear focus.** Selecting a node hides everything else
   (`display:none` via `.dim`), so the nebula visually collapses.

The cap is the common cause of 1, 3, 4; the layout engine is 2.

## Decision

Replace cytoscape entirely on `/graph` with **Cosmograph**
(`@cosmograph/cosmograph`), a GPU force-simulation graph renderer.
It handles 10 k–1 M nodes at 60 fps with a real-time organic
force layout — which *is* the nebula aesthetic. No cap.

User picked "best tech and approach"; Cosmograph is the only
option that satisfies every stated requirement simultaneously:
all nodes + all edges + organic + smooth + no blur + working
navigation.

## Section 1 — Renderer & aesthetic

- Load Cosmograph as a native ESM module from a CDN
  (`https://esm.sh/@cosmograph/cosmograph`). Zero build step;
  satisfies the repo's "no Node toolchain" rule; same delivery
  model as the current cytoscape `<script src>`.
- GPU many-body + link-spring simulation. Nodes are glowing
  points; positions emerge from connectivity. No fixed layout,
  no rings.
- Config:
  - transparent/dark background so the existing `.nebula-shell`
    starfield CSS shows through;
  - `pointColorBy` ← existing `TYPE_COLORS` palette (per-type);
  - `pointSizeBy` ← node degree;
  - `simulationGravity ≈ 0.25`, `simulationRepulsion` tuned so
    hubs separate into visible clusters;
  - simulation cools then settles; optional slow ambient drift.
- **No cap. All 10 845 nodes + 15 418 edges, always.** Canvas ==
  the graph the v3 companion analyzes.

Deleted: `GRAPH_CANVAS_CAP`, degree sort/slice, `tree_modules`
divergence, concentric log-buckets, `_renderCanvasChunked`,
the four-layout switcher.

## Section 2 — Selection & navigation

The cap-driven broken path (`focusNode → reload → ?node=`
ego-fetch → standalone node → collapsed tree → no back) is
**deleted**, not patched. Every node is always present, so:

- **Click point (canvas) or file (tree)** →
  `selectPoint(idx, false, true)` + `zoomToPointByIndex(idx, 700)`
  + `setFocusedPoint(idx)` (ring). Camera flies within the *same*
  full graph. No reload, no sub-graph, no tree rebuild.
- **No blur-disappear.** Cosmograph dims unrelated points by
  *opacity* (`pointGreyoutOpacity ≈ 0.25`), not `display:none`.
  Selected + connected stay bright; the rest fade to a faint haze
  but remain visible. Nebula stays whole.
- **Side-panel neighbors** ← `getConnectedPointIndices(idx)` (full
  real adjacency, not a capped subset).
- **Deselect** (Esc / empty-space) → `unselectAllPoints()` +
  `setFocusedPoint(null)` + `fitView()`. Always a way back.
- `/code → Nebula` deep-link `?node=<id>` still works: resolve id
  → point index → same select+zoom, never leaving the full graph.

Deleted JS: `contextNodeId`, `hops` selector + UI, server
ego-network branch, `reload()`-on-missing-node, `relayout`, layout
veil, `_heavyGraph`, `_stashedEdges`, `.edges-off` class trick.

## Section 3 — File tree

- **No-collapse:** tree is fed by the full node set on every
  render. Selecting a node only highlights its row +
  `scrollIntoView`; it never re-filters or rebuilds the tree.
- **VS Code icons:** `fileIcon(name)` returns a language-specific
  inline SVG keyed by extension, with brand colors:
  `.py .js .ts .tsx .jsx .go .rs .java .rb .php .json .yaml/.yml
  .md .sql .sh .html .css .toml .txt`, plus a node/feedback glyph
  for non-file nodes and an open/closed folder glyph for dirs.
  Reads like a VS Code explorer at a glance.

## Section 4 — Data flow, deletions, testing

- Server `/ui/graph-data` already returns the full node+edge set
  with no cap requested. `tree_modules` field stays (returns the
  full list) for test back-compat but no longer diverges from the
  canvas set.
- `toggleEdges` becomes a Cosmograph link-visibility flag (show /
  hide links), no `.edges-off` CSS.
- Tests:
  - `test_graph_data_workspace_scope.py` — drop cap/truncation
    assertions (already mostly done; re-verify).
  - `test_relayout_focus_state.py` — relayout removed → delete.
  - new: assert `graph.html` loads `@cosmograph/cosmograph` and
    contains no `GRAPH_CANVAS_CAP` / `cytoscape`.
  - existing workspace-scope + e2e tests must stay green.
- Preview-verify 10 845-node workspace: pan/zoom 60 fps, select
  hub < 200 ms, no blur-disappear, tree never collapses, deep-link
  works. Commit on `release/2.6.2`, PR to main, merge, handover.

## Risks

- Cosmograph ESM bundle size / CDN availability. Mitigation:
  esm.sh is stable; can vendor the bundle into `static/` later if
  needed (cytoscape is also CDN-loaded today).
- API drift: design uses the current `/websites/cosmograph_app_docs-lib`
  API (`new Cosmograph(container, config)`, `selectPoint`,
  `zoomToPointByIndex`, `getConnectedPointIndices`,
  `setFocusedPoint`, `fitView`, `onPointClick`).
- Light workspaces (< a few hundred nodes) also get the force
  cloud — acceptable; one consistent renderer everywhere.
