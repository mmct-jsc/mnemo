# mnemo /analyze UX overhaul + companion feature-awareness (v5.21.0)

> DoD-first design doc (pipeline #21). Written 2026-05-30 from live user
> review of the `/analyze` (Knowledge auditor) page.

## 0. Problems (from the user, verbatim intent)

1. **"analyze ran for too long"** — `/analyze` auto-runs the FULL agnostic
   suite on load; `semantic_orphans` alone = 29,035 candidates,
   `contradictions` = 781. The embedder cold-loads + the floods dominate.
2. **"always reload / re-analyze when enter page"** — `x-init="run()"`
   re-runs the whole audit on every visit.
3. **"no pagination so the page too long"** — all findings (tens of
   thousands) render in one table.
4. **"badges don't have color"** — severity badges (`badge-<sev>`) have NO
   CSS rule; the Type column is a plain `<code>`; node-type badges (e.g.
   `commit`) aren't emitted with `--type-color`.
5. **"cannot show where the problem actually is, only when click on
   node"** — findings show opaque node-id hashes
   (`9cb96dd4...`), not the node name / file / problem locus.
6. **"less boring"** — the page is a flat table; no visual life.
7. **Companion** — "must know ALL the features and [be] interactable with
   [them]": Mnem's system prompt omits the auditor + the full feature map.

## 1. Goal

Turn `/analyze` into a fast, scannable, on-brand audit console: it opens
instantly (no auto-run; shows the last result or a clear CTA), lets the
user scope what runs (the floods are opt-in), paginates, colors every
badge by severity/type, and shows WHERE each problem is inline. And make
Mnem aware of + able to drive every feature.

## 2. What to do

### 2.1 Backend — `node_labels` enrichment (so the table shows WHERE)
- `AnalyzeOut` gains `node_labels: dict[str, NodeLabel]` where
  `NodeLabel = {name: str, type: str, source_path: str | None}`.
- `analyze_route` collects every `node_id` across `findings`, batch-
  resolves via `store.get_nodes_by_ids` **chunked at 400 ids** (SQLite
  variable-limit safe — a heavy opt-in run can surface tens of thousands
  of ids), and builds the map. Missing ids are simply absent (UI falls
  back to the raw id).
- Pure additive: pre-existing callers ignore the new field; deterministic
  paths unchanged. The MCP `mnemo_analyze` tool is NOT changed (raw-dict
  path; node_labels is a UI-response convenience only — keep the 27-tool
  surface + wire snapshot byte-stable).

### 2.2 Frontend — `analyze.html` (the bulk)
- **No auto-run.** `x-init="restore()"`: on load, read the last result
  from `localStorage['mnemo.analyze.last']`; if present render it with an
  "audited <relative-time> · re-run" header; else show a landing CTA
  (icon + "Run an audit" + the scope controls). NEVER auto-POST.
- **Scope controls** (the "ran too long" fix): a control bar of toggle
  chips for the 5 agnostic detectors. **Defaults: `stale` + `orphan_references`
  ON** (instant, no embedder, high-signal); `duplicates`,
  `contradictions`, `semantic_orphans` **OFF** (embedder-bound / floods).
  Plus a **lens** select (`none` (knowledge) / `code`); when `code`, the
  agnostic chips are disabled and the run sends `lens=code`. A "Run audit"
  button POSTs `{types:[…checked…]}` or `{lens:"code"}`. Chip state
  persists in `localStorage`.
- **Pagination** — client-side over the fetched `findings` array,
  `PAGE_SIZE = 25`, reusing the prev/next pattern; show "N of M". Findings
  are severity-sorted (high → candidate → medium → low) before paging.
- **Colored badges**:
  - severity → new `app.css` rules `.sev-high/.sev-candidate/.sev-medium/.sev-low`
    (red / amber / blue / slate), used by a `.badge.sev-<s>` element.
  - finding type → a colored badge driven by a per-detector palette
    (JS `FINDING_TYPE_COLORS`) injected as `--type-color` on a
    `.badge.type-finding` element (reuses the existing `[class*="type-"]`
    CSS).
  - node-type → `.badge type-<nodetype>` with `--type-color` from
    `window.mnemoColorFor(type)` (so `commit` etc. get their palette
    color — fixes the uncolored node badges).
- **Inline "where"** — each finding row shows, per cited node: the node
  **name** (link to `/node/<id>`) + a muted **source_path** + a colored
  node-type badge, all from `node_labels`. The problem locus is surfaced
  explicitly: `missing_targets` (orphan_reference), `concept`
  (semantic_orphan), `symbol` (dead_code / god_object / duplicate_code).
  The raw description stays as a secondary line.
- **Less boring** — severity-accented row left-border; summary stat cards
  become clickable severity/scope filters; a real empty state ("no issues
  — your graph is clean") with the mnemo mark; staggered reveal of rows
  via the existing `mnemoStaggeredReveal` primitive.
- **Caching** — after a successful run, store the result (+ scope +
  timestamp) in `localStorage`; wrap in try/catch and SKIP caching if it
  exceeds ~2 MB (a 29k-finding flood) — then the header notes "result too
  large to cache; re-run to view". Re-entry shows the cached result
  instantly (fixes "always re-analyze").

### 2.3 Companion — Mnem knows + drives every feature
- Expand `DEFAULT_SYSTEM` (`chat.py`) into a concise FEATURE MAP: retrieval
  (`mnemo_query` / `mnemo_search_by_type` / `mnemo_traverse`), the graph
  (`mnemo_select_node` / `mnemo_highlight_nodes` / `mnemo_set_filter` /
  `mnemo_navigate`), nodes CRUD (`mnemo_get_node` / `mnemo_create_node` /
  `mnemo_update_node` / `mnemo_delete_node`), the **knowledge auditor**
  (`mnemo_analyze` — stale/duplicates/orphan_references/contradictions/
  semantic_orphans + the `code` lens: dead_code/god_object/cyclic_imports/
  duplicate_code), tuning (`mnemo_apply_retune` / `mnemo_change_settings`),
  skills (`mnemo_list_skills` / `mnemo_run_skill`), sources
  (`mnemo_add_source` / `mnemo_reindex_source`). Keep it TIGHT (a token
  budget) — a categorized one-liner per area, not prose. Preserve the
  in-page-first / navigate-last discipline already there.
- No new tools needed (all 27 exist); this is awareness, not capability.

## 3. What NOT to do (anti-goals)

- **No async/streaming/job-queue** for analyze — opt-in scope + no-auto-run
  + cache solve "too long" without that machinery (YAGNI).
- **No server-side analyze cache** — client localStorage is enough and
  keeps the daemon stateless here.
- **Do NOT change the MCP `mnemo_analyze` tool** schema/description or the
  27-tool count / wire snapshot. `node_labels` is HTTP-response only.
- **Do NOT auto-run any detector on page load.** Ever.
- **Do NOT change detector logic / thresholds** — this is UX + an additive
  response field only. Deterministic detector output stays byte-stable.
- **No new dependency.** No emojis in code/UI copy.
- Don't cap/curate the FINDINGS silently — paginate (show the true total).

## 4. Definition of Done

- [ ] `AnalyzeOut.node_labels` populated; `/v1/analyze` returns name/path/
      type per cited node; chunked resolution (>400 ids works).
- [ ] `/analyze` does NOT POST on load (verified: no network call until
      "Run audit"); a prior result restores from localStorage.
- [ ] Default run = `stale` + `orphan_references` only (no embedder load,
      no 29k flood); the 3 heavy detectors are opt-in chips; `lens=code`
      selectable.
- [ ] Findings paginate (25/page) with a correct total; severity-sorted.
- [ ] Every badge colored: severity (red/amber/blue/slate), finding type,
      node type (incl. `commit`).
- [ ] Each finding shows node name + source_path + problem locus inline
      (no click needed to see WHERE).
- [ ] Empty + landing states; severity row accents.
- [ ] Mnem's system prompt enumerates all feature areas incl. the auditor.
- [ ] Full daemon suite green; ruff clean; MCP wire snapshot UNCHANGED
      (27 tools).
- [ ] Live (preview): run a default audit → fast, colored, paginated,
      inline "where"; toggle on semantic_orphans → still paginates; the
      companion, asked "what can you do / audit my graph", references the
      auditor and can call `mnemo_analyze`.

## 5. Test plan (TDD)
- `tests/unit/test_analyze_node_labels.py`: route/analyze returns
  `node_labels` with name+source_path+type for cited nodes; chunking over
  >400 ids; missing id omitted; empty findings → empty map.
- Extend an analyze schema test: `AnalyzeOut` accepts `node_labels`.
- Companion: a test asserting `DEFAULT_SYSTEM` mentions `mnemo_analyze`
  (feature-awareness contract) — cheap regression guard.
- UI page test (`test_analyze_ui_page` if present): the page no longer
  auto-runs (no `x-init="run()"`); control bar + pagination markup present.
