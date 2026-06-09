# mnemo Phase 4b -- confirm-then-apply (the first node mutation) (v5.23.0)

> DoD-first design doc (pipeline #21). **STATUS: DESIGNED + APPROVED**
> (brainstormed + validated section-by-section with the user, 2026-06-01).
> Ready to implement as v5.23.0 (`release/5.23.0` + TDD + phased commits,
> like the prior analyzer releases). Leave this doc uncommitted until the
> ship -- it rides with the v5.23.0 commit (the 4a precedent).

## 0. Context

Phase 4 of the v6 vision = the proactive auditor (4a, shipped v5.22.0,
read-only) + **confirm-then-apply (4b, THIS doc) -- the FIRST node
mutation**. The user chose "4a first" so the first mutation lands in its
own focused, hard-gated release.

**Forever anti-goal:** NO SILENT EDITS -- mnemo never modifies a node
without explicit user confirmation. 4b is the first feature that writes a
node, so the gating IS the feature.

**Two decisions made in brainstorming:**
- **Scope = deterministic orphan-fix ONLY.** The apply operates on an
  `orphan_reference` finding and removes the dead `[mnemo:<id>]` citation
  token(s) from the node body via the existing `update_node` path. No LLM,
  no proposer, smallest blast radius, 18 live findings to dogfood. Stale-
  archive + LLM-proposed applies are later releases.
- **Gate = preview -> confirm handshake + stale-check.** PREVIEW (read-
  only) returns the exact before/after + the node's current hash; APPLY
  requires that hash back and re-verifies the node is unchanged (reject if
  it drifted), then edits + marks the finding resolved. Exposed as BOTH a
  `/analyze` "Apply (preview)" modal AND a new `risk=confirm` MCP tool
  (28 -> 29) -- the host's confirm-prompt is the second gate.

**Building blocks that already exist:** the `audit_queue` + `AuditFinding`
(v5.22.0); `Store.update_node` (the recoverable mutation primitive);
`analyzer._CITATION_RE` (the `[mnemo:<id>]` matcher); `node.hash` (the
stored content hash -> the stale-check token); `_node_labels_for_findings`.

## 1. What the orphan-fix does + the placeholder safety

The apply targets one `orphan_reference` finding (one node, the dead
target id set is recorded as the finding's `locus` = sorted
`missing_targets`). The fix:

- recomputes the **still-missing** targets against the LIVE graph (a
  target may have been re-created since the audit);
- keeps only **id-shaped** targets -- 32-hex mnemo node ids (e.g.
  `a86e4261dfea499383713577fedf95d7`), matched by `^[0-9a-f]{32}$`;
- removes only those dead `[mnemo:<id>]` tokens from the body (leaving
  every valid citation and all other text), collapsing orphaned
  whitespace/punctuation cleanly.

**Placeholder safety (critical for the FIRST mutation).** The live queue's
18 orphan_references are mostly documentation placeholders -- literal
`[mnemo:<id>]` / `[mnemo:node_id]` examples that TEACH the citation
format. Stripping those would corrupt docs. So the deterministic fix
**refuses non-id-shaped targets** (`<id>`, `id`, `ID`, `node_id`): such a
finding previews as `applyable=false` with reason "looks like a
documentation placeholder, not a dead citation". The id-shape gate is the
first net; the human preview is the second. A finding is applyable only
when it cites a real, id-shaped, currently-missing node.

On a successful apply: edit the body via `update_node`, then mark the
finding **`resolved`** in the queue (instant feedback; the next reindex
would auto-resolve it anyway since the token is gone). The node re-hashes
/ re-embeds on the next reindex like any edit.

## 2. The preview -> confirm handshake + surfaces

Core logic lives in a NEW `daemon/mnemo/apply.py` (keeps `analyzer.py`
detection-only; both the route and the MCP tool import it):

- `strip_dead_citations(body, dead_ids) -> (new_body, removed)` -- pure,
  unit-testable text op (reuses `_CITATION_RE`).
- `preview_orphan_fix(store, fingerprint) -> dict` -- looks the finding up
  via new `store.get_audit_finding(fingerprint)`, recomputes the still-
  missing id-shaped dead targets, computes `after`. Returns
  `{fingerprint, node_id, node_name, before, after, removed, applyable,
  reason, node_hash}`. READ-ONLY. `applyable=false` (+reason) when nothing
  id-shaped is still dead (all placeholders / already fixed / node gone).
- `apply_orphan_fix(store, fingerprint, confirm_node_hash) -> dict` --
  re-verifies the node's current `hash == confirm_node_hash` (reject as
  STALE if it drifted), recomputes, applies via `update_node(body=after)`,
  marks the finding `resolved`, returns the applied result. Raises typed
  errors the surfaces map to status codes.

**Store:** add `get_audit_finding(fingerprint) -> AuditFinding | None`
(single-row fetch; the `node_ids`/`locus` reconstruct the target + dead
set).

**HTTP (additive):**
- `POST /v1/analyze/queue/{fingerprint}/apply/preview` -> `ApplyPreviewOut`
  (read-only).
- `POST /v1/analyze/queue/{fingerprint}/apply` body `{node_hash}` -> 200
  applied / **404** unknown fingerprint / **422** not-applyable / **409**
  stale node_hash.

**MCP:** new tool **`mnemo_apply_finding(fingerprint,
confirm_node_hash=None)`**, `risk=confirm`: no hash -> returns the preview;
with hash -> applies. **28 -> 29 tools** (regen wire snapshot; bump the
surface-count test). The host's confirm-prompt is the second gate.

**UI:** `/analyze` Queue -- `orphan_reference` rows get an **Apply**
button -> fetch the preview -> a modal shows the before/after + the exact
removed `[mnemo:<id>]` tokens -> **Confirm** posts the apply with
`node_hash` -> the row flips to `resolved` + a toast. Not-applyable
findings show the reason (no Confirm). Non-orphan findings get no Apply
button this release.

## 3. Anti-goals -- testing -- DoD -- build sequence

**Anti-goals (4b):** no silent edits (preview + node-hash confirm; MCP
tool `risk=confirm`); no LLM in the apply path (deterministic strip);
`orphan_reference` + id-shaped targets ONLY (no placeholder strips, no
stale/duplicate/code applies yet); **no "apply all"** (one finding at a
time -- per-finding human judgment IS the gate). Additive: +1 module, +2
endpoints, +1 tool (28->29), +1 store getter, +UI; the 28 prior tools +
the read-only queue stay byte-stable.

**Test plan (TDD):**
- `test_apply_orphan_fix.py`: `strip_dead_citations` removes only dead
  id-shaped tokens, keeps valid citations + text; placeholder targets
  refused (`applyable=false`); multi-target strips only the still-missing;
  preview returns before/after + node_hash + is read-only; apply rejects a
  stale hash; apply edits the body + marks the finding resolved;
  already-fixed -> not-applyable; unknown fingerprint -> not found.
- `test_apply_queue_endpoint.py`: preview is read-only (no mutation); apply
  happy path (body edited + finding resolved); 404 / 422 / 409.
- `test_mnemo_apply_finding_tool.py` + regen `mcp_tool_list.json` +
  surface-count **28->29**; tool is `risk=confirm`; no-hash -> preview,
  with-hash -> applies.
- `test_apply_ui.py`: `orphan_reference` rows have an Apply button; modal
  binds before/after + removed; confirm posts `node_hash`; anti-goal: no
  "apply all" control.

**DoD (operator-green):** seed a REAL dead citation (create node A whose
body cites the 32-hex id of a deleted node B), reindex -> the queue shows
the orphan; `preview` shows the before/after + the removed token; `apply`
-> A's body no longer cites it + the finding is `resolved`; a placeholder
orphan (`[mnemo:<id>]` doc) previews as **not-applyable** (refused); full
suite green; ruff clean; 29-tool snapshot. (The live 18 are mostly
placeholders -> correctly refused; the synthetic real one proves the
end-to-end fix.)

**Build sequence (phased commits, one release `release/5.23.0`):**
1. `apply.py` -- pure `strip_dead_citations` + the id-shape filter (TDD).
2. Store `get_audit_finding` + `preview_orphan_fix` / `apply_orphan_fix`
   (stale-check + mark-resolved) (TDD).
3. Endpoints (preview + apply) + `ApplyPreviewOut` / apply schemas.
4. MCP tool `mnemo_apply_finding` (`risk=confirm`) + snapshot + count test.
5. UI -- Apply button + preview/confirm modal on orphan rows.
6. Version bump 5.22.0 -> 5.23.0 (4 files) + CHANGELOG + this design doc +
   live dogfood (synthetic real dead citation).

**Then (later releases):** stale-archive apply (a destructive delete/
archive -- its own hard-gated release); the orphan detector `<...>`
placeholder-skip refinement (shrinks the noise so the queue's orphans are
real); LLM-proposed-action apply for the harder finding types
(duplicates/merge, supersede) off the `refactor_actions` proposer.
