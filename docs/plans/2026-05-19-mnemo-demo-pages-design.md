# mnemo Demo Page (GitHub Pages) -- Design

**Date:** 2026-05-19
**Status:** VALIDATED (brainstorming, two pivotal forks confirmed with the user)

## Goal

A lean, static, GitHub-Actions-built, GitHub-Pages-published demo that
shows mnemo's functions "as realistically as possible" with **seeded,
fixed, synthetic data depicting mnemo itself** (NOT the live workspace
index, NO private data) -- and a link from the codebase to it.

## Pivotal decisions (user-confirmed)

1. **Scope:** ONE lean page -- a genuinely *interactive* Nebula
   galaxy (the real client renderer on baked data) as the centerpiece
   + compact static feature cards for the server-only features.
   Rejected: multi-page static export; scripted canned tour.
2. **Data:** synthetic graph that *depicts mnemo's own architecture*,
   deterministic + fixed, run through the REAL layout engine. NOT a
   snapshot of the live workspace; no real/private data.

## Hard constraint

GitHub Pages is static-only -- the FastAPI daemon cannot run there.
mnemo's centerpiece (`/graph` Nebula) is pure client-side WebGL
(`regl` + `nebula-gl.js`) consuming a precomputed layout JSON, so it
runs fully static once the JSON is baked. Server-only features
(Graph-RAG query, Mnem chat, `/code`) are shown as truthful static
cards generated from the same seed.

## Architecture

- **`demo/seed_mnemo.py`** -- deterministic synthetic graph depicting
  mnemo: `code_module/function/class` for the daemon packages
  (ui, graph_layout, agent_tools, retrieve, store, providers, ...),
  `memory_project/feedback/reference`, `commit` nodes; typed edges
  (`calls/defines/method_of/applies_to/touched_by/...`) with planted
  community structure so the spectral->log-spiral engine yields a
  believable ~1.2k-node galaxy. Fixed seed -> byte-identical output.
- **`scripts/build_demo.py`** -- imports `mnemo.ui.graph_layout`
  (the REAL engine), builds the seed graph, computes the layout,
  emits `nebula.json` ({nodes:[{x,y,size,color,name,type,deg}],
  edges:[{s,t}]}), renders `demo/index.html.tmpl` with generated
  feature-card snippets, and assembles `--out dist/`: `index.html`,
  `nebula.json`, `regl.min.js` + `nebula-gl.js` (COPIED from
  `daemon/mnemo/ui/static/vendor/`), `brain.svg`. ~5 served files.
- **`demo/index.html.tmpl`** -- hero (name, pitch, v4.6.2, repo
  link), the live Nebula `<canvas>` + `<canvas>` label overlay wired
  to `NebulaGL.create` exactly like `graph.html`, feature cards
  (Graph-RAG cited block, code-intelligence trace, a labelled Mnem
  sample, hooks one-liner), footer (local-install snippet + repo).
  Minimal inline CSS using the C1 token palette subset.
- **`.github/workflows/demo-pages.yml`** -- `push: [main]` +
  `workflow_dispatch`; jobs: build (`uv sync` daemon ->
  `python scripts/build_demo.py --out dist` ->
  `actions/upload-pages-artifact`), deploy
  (`actions/deploy-pages`). `permissions: { contents: read,
  pages: write, id-token: write }`. Built-in `GITHUB_TOKEN` only --
  **no PAT in any workflow or file, ever.**

## Data flow

seed_mnemo (deterministic) -> compute_graph_layout (real, cached
engine) -> nebula.json -> static index.html loads regl + nebula-gl.js
-> NebulaGL.create(canvas, {nodes, edges, ...}) -> interactive galaxy.
Feature-card snippets are generated from the SAME seed so the text is
consistent with the shown graph (truthful, not invented separately).

## Security / token handling

The user pasted a full-perm GitHub PAT in chat -- treated as
compromised; **must be rotated** after this session. It is used ONLY
transiently in this session's shell for three repo-admin ops the
CI token can't do: (1) enable Pages (build type = GitHub Actions),
(2) set the accurate repo description, (3) set homepage ->
`https://mmct-jsc.github.io/mnemo/`. The PAT is never written to a
file, commit, workflow, or log. A guard test (`test_demo_build.py`)
asserts no `github_pat_`/`ghp_`-shaped string exists anywhere in the
tree.

## Stale-info fixes (bundled)

- README version badge `4.6.1` -> `4.6.2`; add a "Live demo" badge/
  link to the Pages URL.
- `CLAUDE.md` already current; add the demo URL where the UI is
  described.
- Repo description (still the old v1 text; 403'd with the CI token)
  + homepage (empty) -> set via the transient PAT.

## Testing / verification

- `daemon/tests/unit/test_demo_build.py` (asset-contract style,
  GPU-free): builder produces schema-valid `nebula.json` with all
  finite coords; **deterministic** (two builds byte-identical); the
  rendered page references the vendored `regl.min.js` +
  `nebula-gl.js` and calls `NebulaGL.create`; the dist contains only
  the expected lean file set; **no secret-shaped string in any
  committed file**.
- Full daemon suite + ruff stay green.
- Post-merge: the workflow deploys; verify the live URL 200s and the
  page references the renderer (curl).

## Landing

A normal feature PR -> CI -> merge. **No daemon version bump** (this
is infra/docs; Pages deploys from the workflow on merge, not a tag).
Post-merge the demo-pages workflow runs and publishes.

## YAGNI / out of scope

No multi-page export, no canned chat engine, no screenshots/video, no
backend, no SPA framework. One page, one baked JSON, two vendored JS,
one builder, one workflow.
