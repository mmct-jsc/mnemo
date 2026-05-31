# mnemo Phase 4a — proactive audit queue (v5.22.0)

> DoD-first design doc (pipeline #21). **STATUS: DESIGNED + APPROVED**
> (brainstormed + validated section-by-section with the user, 2026-05-30).
> Ready to implement as v5.22.0. The next session builds directly from
> this spec (release branch + TDD + phased commits, like the prior 8
> analyzer releases).

## 0. Context

Phase 4 of the v6 vision (`project_mnemo_v6_vision_understanding`) is the
**proactive auditor + confirm-then-apply** — the under-built "FIX" half.
It splits into:
- **4a (THIS doc): proactive read-only queue** — the audit runs
  automatically on reindex; findings persist in a queue; surfaced via a
  nav badge + `/analyze` + a read-only Mnem tool. **ZERO mutation.**
- **4b (later release): confirm-then-apply executor** — the first
  mutation (apply a finding's proposed action via existing primitives,
  hard-gated). NOT in 4a.

The user explicitly chose "4a first" so the first mutation lands in its
own focused, hard-gated release.

**Forever anti-goal (vision doc):** NO SILENT EDITS — mnemo never
modifies a node without explicit user confirmation. 4a honours this
trivially: it performs no node writes at all.

**Building blocks that already exist:** the `analyze()` orchestrator +
the `stale` / `orphan_references` deterministic detectors;
`_node_labels_for_findings` (v5.21.0, resolves id→name/type/source_path,
chunked); the pagination pattern (`list_X(offset)` + `count_X_total`,
reference_mnemo_pagination); the `graph_layout` table as a precedent for
a new always-run table; the v5.9.0 stateful reindex-progress flow (the
post-reindex hook point).

## 1. Architecture + proactive trigger

A persisted **audit queue** + a trigger that audits automatically after
each reindex and reconciles findings into the queue. Strictly read-only.

**Trigger.** When a reindex completes in the daemon, kick off a
**scoped, async** audit (background thread; non-blocking so reindex
returns immediately and the corpus is never locked). Scope = the cheap
deterministic detectors only: **`stale` + `orphan_references`** (instant,
no embedder, no LLM). Deliberately NOT the embedding floods
(`semantic_orphans` ≈ 29k, `contradictions`) — the handover's explicit
warning. A future config flag may widen the scope; small-and-fast is the
default so the sweep stays invisible.

**Reconcile (the heart).** Each finding gets a stable **fingerprint** =
`sha1(type + "\n" + ",".join(sorted(node_ids)) + "\n" + (locus or ""))`
where `locus` = the problem locus (`missing_targets` joined / `concept` /
`symbol`). Then:
- fingerprint not in queue → insert as **`open`**
- fingerprint in queue (status `open`/`dismissed`) → bump `last_seen`,
  keep status (**`dismissed` is sticky**)
- fingerprint in queue with status `resolved` and re-detected → **reopen**
  to `open`
- an `open` finding the fresh audit no longer produces → **`resolved`**,
  but ONLY for finding types the audit actually ran (guard by
  `detector_types`), so an out-of-scope type is never wrongly resolved

The queue becomes a living, de-duplicated, status-tracked view that
converges as the user cleans up.

## 2. Data model

**New table `audit_queue`** in the always-run `SCHEMA_SQL` (NOT the lazy
`VEC_SCHEMA_SQL` — the documented 500-on-plain-stores gotcha):

```
CREATE TABLE IF NOT EXISTS audit_queue (
  fingerprint TEXT PRIMARY KEY,
  type        TEXT NOT NULL,
  severity    TEXT NOT NULL,
  node_ids    TEXT NOT NULL,         -- JSON array
  description TEXT NOT NULL,
  locus       TEXT,                  -- nullable
  status      TEXT NOT NULL DEFAULT 'open',  -- open|dismissed|resolved
  first_seen  INTEGER NOT NULL,
  last_seen   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_queue_status ON audit_queue(status);
```

`node_ids` resolved to names/paths at READ time via
`_node_labels_for_findings` — never stored stale.

**Three-state lifecycle** (no "seen" state — YAGNI): `open` (active; the
badge counts these) · `dismissed` (user "ignore"; sticky) · `resolved`
(auto when an open finding disappears; reopens if re-detected).

**Store methods** (pagination pattern — shared WHERE builder so page +
total never drift):
- `reconcile_audit_queue(findings: list[dict], detector_types: tuple[str, ...]) -> dict`
  → `{"new": n, "reopened": n, "resolved": n, "unchanged": n}`. Does the
  upsert + scope-guarded auto-resolve in one transaction.
- `list_audit_queue(*, status: str | None = "open", limit: int, offset: int) -> list[Row]`
- `count_audit_queue(status: str | None = None) -> int`
- `audit_queue_counts() -> dict` → `{"open": n, "dismissed": n, "resolved": n}` (one aggregate pass; badge + UI cards)
- `set_audit_finding_status(fingerprint: str, status: str) -> bool`
- helper `_finding_fingerprint(finding: dict) -> str` + `_finding_locus(finding: dict) -> str | None`

## 3. Surfaces

**Trigger hook.** After reindex completes in the daemon (right after the
v5.9.0 reindex-progress "done" transition), spawn a background task:
`analyze(store, types=["stale","orphan_references"])` →
`store.reconcile_audit_queue(result["findings"], ("stale","orphan_references"))`.
Non-blocking; guard with try/except so a sweep failure never breaks
reindex. (Confirm the exact hook point in `server.py` / the reindex
runner during implementation.)

**HTTP (read + one metadata write).**
- `GET /v1/analyze/queue?status=open&limit=25&offset=0` →
  `AnalyzeQueueOut { findings: [QueueFinding], total: int,
  counts: {open,dismissed,resolved}, node_labels: {id: NodeLabel} }`.
  Read-only. `status` omitted/`all` = no filter.
- `POST /v1/analyze/queue/{fingerprint}/status` body `{status}` →
  flips the row (open↔dismissed). The ONLY write, and it is queue
  metadata, NOT a node edit (user-initiated "ignore"/"restore").

**UI.**
- A small **open-count badge** on the nav **Analyze** link (polls the
  cheap counts).
- `/analyze` shows the **Queue** as the default landing view (replaces
  the stale localStorage-restore as the primary "what's here"): status
  chips (`open` default / dismissed / resolved), 25/page pagination, each
  finding with inline name/path/locus + colored badges (reuse v5.21.0) +
  a **Dismiss**/**Restore** button. The existing scope-chips + **Run
  audit** stay for ad-hoc on-demand audits.

**Companion.** New **read-only** MCP tool **`mnemo_audit_queue`**
(`status="open"`, `limit=20`) → lists open findings, so Mnem answers
"what's wrong with my corpus?" from the persisted queue. Additive →
**27 → 28 tools**; regenerate the wire snapshot; bump the surface-count
test. NO apply tool (that's 4b).

## 4. Anti-goals · testing · DoD · build sequence

**Anti-goals (4a):** no node mutation (only queue-status flips); no
blocking reindex (async after; no corpus-locking); no LLM / no embedding
floods in the proactive path; no new core dependency. Existing on-demand
`/analyze` + the 27 prior tools untouched (additive: +1 table, +2
endpoints, +1 read-only tool, +UI).

**Test plan (TDD):**
- `test_audit_queue_store.py`: reconcile inserts `open`; re-run bumps
  `last_seen` keeping status; disappeared in-scope finding → `resolved`;
  `dismissed` stays sticky on re-detect; `resolved` reopens on re-detect;
  out-of-scope type never auto-resolved; `list_audit_queue` pagination +
  `status` filter; `count_audit_queue` / `audit_queue_counts`;
  `set_audit_finding_status`; fingerprint stable + order-independent on
  node_ids.
- `test_analyze_queue_endpoint.py`: GET paginated + counts + node_labels;
  POST status flips; `status=open` excludes dismissed; 404 on unknown
  fingerprint.
- `test_proactive_reindex_trigger.py`: a reindex completion runs the
  scoped audit + populates the queue (test the hooked function;
  reconcile called with the right detector types).
- `test_mnemo_audit_queue_tool.py` + regen `mcp_tool_list.json` +
  surface-count test **27→28**. Tool is read-only (no mutation).
- `test_analyze_queue_ui.py`: `/analyze` has the queue view + status
  chips + dismiss control; nav badge markup in `base.html`.

**DoD (operator-green):** reindex the dogfood corpus → the queue
auto-fills with the live `stale` (~47) + `orphan_reference` (~17)
findings; the nav badge shows the open count; dismiss one → it leaves
`open` and stays dismissed across the next reindex; re-running reindex
does NOT duplicate rows; `mnemo_audit_queue` returns the open set; full
suite green; ruff clean; 28-tool snapshot.

**Build sequence (phased commits, one release `release/5.22.0`):**
1. Store — schema + `_finding_fingerprint`/`_finding_locus` + reconcile +
   list/count/counts/status methods (TDD).
2. Trigger — post-reindex hook → scoped audit → reconcile (async, guarded).
3. Endpoints — `GET /v1/analyze/queue` + `POST .../status` + schemas.
4. MCP tool — `mnemo_audit_queue` (read-only) + snapshot + count test.
5. UI — nav badge + `/analyze` queue view.
6. Version bump 5.21.0 → 5.22.0 (4 files) + CHANGELOG + this design doc.

**Then 4b (a LATER release):** the confirm-then-apply executor — one
audited mutation path over `mnemo_update/delete/create_node`, hard-gated
with a preview + explicit confirmation, driven off the queued findings'
`refactor_actions` proposals. Out of scope here.
