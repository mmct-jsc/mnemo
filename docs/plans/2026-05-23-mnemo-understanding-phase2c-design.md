# mnemo v5.15.0 — Understanding Phase 2c: Refactor Actions

> **Spec doctrine (pipeline #21, installed v5.12.0):** This doc
> follows the DoD-first template. Sections in order: Summary / Why /
> Spec / Definition of Done / Anti-goals / Scope / Comparison / Build
> sequence / Open questions resolved.

## 1. One-line summary

Add an opt-in **refactor_actions** enrichment pass to the knowledge
auditor: for each high/medium-severity finding (across all 5
detectors), an LLM proposes ONE concrete action mapping to an
existing mnemo primitive (`mnemo_update_node` / `mnemo_delete_node`
/ `mnemo_create_node`). The action travels with the finding through
the HTTP / MCP / UI surfaces. Severity-gated + hard-capped + NEVER
auto-applied. The 27-tool MCP surface stays count-stable;
`mnemo_analyze` gains one optional backward-compatible parameter.

## 2. Why this matters

The auditor (v5.12.0 → v5.14.0) now surfaces five classes of
structural issue: stale, duplicates, orphan_references,
contradictions, semantic_orphans. But every finding stops at
"here is a problem" — the USER still has to decide what to DO.

The user's stated v6 vision is explicit: the system should "tackle
any problem to fix/enhance" and "actually suggest if need to
refactor because it's actually a mess." Phase 2c is precisely that
turn — from **detection** to **prescription**.

Today the `mnemo-knowledge-auditor` SKILL.md carries per-type
proposed-action templates, but those only help a skill-aware Claude
session. By moving action generation into the daemon (LLM-generated,
structured, attached to the finding), ANY MCP client — and the HTTP
API, and the `/analyze` UI — gets actionable output, not just prose.

The canonical examples:

- **duplicates** → "merge node B's unique paragraph into node A,
  then `mnemo_delete_node(B)`" with A/B chosen by recency + length.
- **contradiction** → "node A (2026-03) supersedes node B (2026-01);
  `mnemo_update_node(B, description=… + ' SUPERSEDED by A')`".
- **semantic_orphan** → "create a `memory_reference` node named
  `MQTTBridge` defining the broker-auth wrapper referenced in node X".
- **orphan_reference** → "the `[mnemo:gone]` citation in node X is
  dead; `mnemo_update_node(X, body=…)` with the token removed".
- **stale** → "fully superseded; `mnemo_delete_node(X)`".

## 3. Spec

### 3.1 The action shape

Each enriched finding gains an `action` field (a dict). Findings
that are not enriched (severity below the gate, or beyond the cap,
or proposer unavailable) have `action: null`.

```
action = {
  "kind": "merge" | "supersede" | "delete" | "create_definition"
        | "add_reconciliation_note" | "fix_citation" | "none",
  "primitive": "mnemo_update_node" | "mnemo_delete_node"
        | "mnemo_create_node" | null,
  "target_node_id": "<id>" | null,
  "args_hint": { ...suggested kwargs for the primitive... },
  "rationale": "<one short paragraph>",
}
```

`kind = "none"` + `primitive = null` is the graceful-degradation
result (parse error, network error, or the LLM declined to
propose). The finding still ships; only its `action` is empty.

### 3.2 The proposer

A new generator class (NOT a binary classifier like the v5.13.0 /
v5.14.0 judges):

```
@dataclass
class LLMRefactorProposer:
    client: Any
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 700
    rationale_log: list[dict] = field(default_factory=list)

    def propose(self, *, finding: dict, node_bodies: dict[str, str]) -> dict:
        # Returns an action dict (kind="none" on every error path).
```

The prompt gives the LLM: the finding's `type`, `description`,
`node_ids`, the severity, and the body excerpts of the cited nodes
(capped). It asks for ONE action as strict JSON. The system prompt
enumerates the valid `kind` values + their primitive mappings and
the hard rule that actions are proposals the user reviews — never
auto-applied.

### 3.3 The enrichment pass

```
def propose_refactor_actions(
    store, findings, *, proposer=None,
    max_actions=DEFAULT_MAX_REFACTOR_ACTIONS,    # 50
    severities=("high", "medium"),
) -> tuple[list[dict], int]:
    # Returns (enriched_findings, n_skipped_due_to_cap).
```

- Only findings whose `severity` is in `severities` are eligible.
  Default `("high", "medium")`: actionable, confirmed findings.
  `candidate` (unconfirmed deterministic) and `low` (stale, already
  user-marked) are NOT enriched by default — they're noise until
  promoted. This bounds cost: with the LLM judge enabled, the
  judge promotes real findings to `high` and drops the rest, so the
  eligible set is small.
- Eligible findings beyond `max_actions` are left with
  `action: null` and counted into `n_skipped`. **No silent caps**
  (pipeline rule): the orchestrator surfaces the skipped count in
  the summary so the operator knows coverage was bounded.
- Without a proposer (`None` + env off), the pass is a no-op:
  returns the findings unchanged + `0` skipped. Byte-stable default.

### 3.4 Orchestrator wiring

`analyze()` gains `propose_actions: bool | None = None` +
`proposer: Any | None = None`:

- `propose_actions is None` → resolve from env
  (`MNEMO_ANALYZE_PROPOSE_ACTIONS` truthy). Mirrors the
  `judge=`/`judge_from_env()` precedence exactly.
- When enabled, after all detectors run, `propose_refactor_actions`
  enriches the findings. The skipped count lands in
  `summary["_refactor_actions_skipped"]` (underscore-prefixed so it
  doesn't collide with a detector-type bucket).

### 3.5 HTTP / MCP / Schema / UI

- `AnalyzeFinding` gains `action: dict | None = None` AND `concept:
  str | None = None` (backfills the v5.14.0 omission — the
  semantic_orphans `concept` field was being stripped on HTTP
  serialization because the model didn't declare it).
- `AnalyzeIn` gains `propose_actions: bool | None = None`.
- `mnemo_analyze` MCP tool gains an optional `propose_actions`
  boolean param + description mentions the enrichment. Tool COUNT
  stays 27; the name is unchanged; the new param is optional +
  backward-compatible (existing callers are unaffected).
- `/analyze` UI renders the proposed action (kind + primitive +
  rationale) in an expandable cell when present.

### 3.6 Tests

- `tests/unit/test_refactor_actions.py` — proposer.propose()
  returns a well-formed action per finding type; graceful failure
  → kind="none"; rationale_log audit trail; propose_refactor_actions
  enriches only high/medium; respects + reports the cap; leaves
  candidate/low untouched; no-proposer no-op.
- `tests/unit/test_refactor_proposer_env.py` — env-flag gate
  (`MNEMO_ANALYZE_PROPOSE_ACTIONS` + `ANTHROPIC_API_KEY` + anthropic
  importable), graceful None.
- `tests/unit/test_analyze_detectors.py` — extend: `analyze(…,
  propose_actions=True, proposer=MagicMock())` attaches actions;
  default leaves `action` absent/None.
- `tests/integration/test_analyze_endpoint.py` — extend: the new
  fields serialize; default response has no action.
- `tests/unit/test_mnemo_analyze_mcp_tool.py` — extend: the param
  passes through.
- `tests/unit/test_analyze_ui_page.py` — extend: action cell.
- `tests/unit/test_knowledge_auditor_skill.py` — extend: auto-action
  section.
- `tests/unit/_snapshots/mcp_tool_list.json` — regen.

Target: +18-22 new tests; daemon suite 1560 → ~1580.

## 4. Definition of Done

- [ ] Design doc (this file) with Spec / DoD / Anti-goals / Scope /
  Comparison (pipeline #21 check).
- [ ] `LLMRefactorProposer.propose(finding, node_bodies)` returns a
  structured action dict; every error path → `kind="none"`.
- [ ] `refactor_proposer_from_env()` reads
  `MNEMO_ANALYZE_PROPOSE_ACTIONS` + `ANTHROPIC_API_KEY` + anthropic
  importable → instance OR None.
- [ ] `propose_refactor_actions(store, findings, …)` enriches only
  `("high","medium")` by default, hard-caps at 50, returns
  `(findings, n_skipped)`.
- [ ] `analyze(…, propose_actions=None, proposer=None)` wires it
  through; skipped count in `summary["_refactor_actions_skipped"]`.
- [ ] `AnalyzeFinding` carries `action` + `concept`; `AnalyzeIn`
  carries `propose_actions`.
- [ ] `mnemo_analyze` gains the optional param; 27-tool count
  unchanged; wire snapshot regenerated.
- [ ] `/analyze` UI renders the action when present.
- [ ] 18+ new tests pass; full daemon suite green (no regressions
  on the 1560).
- [ ] Ruff check + format clean.
- [ ] CI 9/9 green on PR.
- [ ] **Interactive test**: `POST /v1/analyze {}` (default, no
  proposer) returns findings with `action: null` and no
  `_refactor_actions_skipped` inflation — byte-stable deterministic
  path on the live 12k corpus.
- [ ] Schema accepts the new `action`/`concept` fields end-to-end
  (the v5.14.0 `concept` field now survives HTTP serialization).
- [ ] MEMORY.md promoted to v5.15.0 canonical; v5.14.0 demoted.
- [ ] Tag `v5.15.0` published on `public`.

## 5. Anti-goals (preserved + new)

- **NEVER auto-apply.** Still the Phase 1 anti-goal. The proposer
  generates a PROPOSAL; the user (or a future explicit confirm-mode
  in Phase 4) acts. `propose_refactor_actions` calls NO mutating
  primitive.
- **No per-finding LLM by default.** The enrichment is opt-in
  (env flag / param) AND severity-gated AND hard-capped. The
  default deterministic audit makes zero LLM calls and adds zero
  `action` fields.
- **No new MCP tool.** Count stays 27. `mnemo_analyze` gains one
  optional backward-compatible param (not a break — existing
  callers omit it and get identical behavior).
- **No new detector.** refactor_actions is an enrichment over
  existing findings, not a 6th detector. `KNOWN_DETECTOR_TYPES`
  stays at 5.
- **No silent cap.** The skipped-due-to-cap count is surfaced in
  the summary.
- **No domain lenses.** Phase 3 (v5.16.0+). The proposer is
  domain-agnostic.
- **No proactive scheduling.** Phase 4 (v5.16.0+).
- **No new daemon dependencies.** `anthropic` already a runtime dep.

## 6. Scope — files touched + boundary

### In-scope (must change):

- `daemon/mnemo/analyzer.py` — ADD `LLMRefactorProposer`,
  `refactor_proposer_from_env()`, `propose_refactor_actions()`,
  `DEFAULT_MAX_REFACTOR_ACTIONS`; wire `propose_actions`/`proposer`
  into `analyze()`.
- `daemon/mnemo/api_schemas.py` — `AnalyzeFinding.action` +
  `.concept`; `AnalyzeIn.propose_actions`.
- `daemon/mnemo/server.py` — pass `propose_actions` through the
  `/v1/analyze` route.
- `daemon/mnemo/agent_tools.py` — `mnemo_analyze` `propose_actions`
  param + description.
- `daemon/mnemo/ui/templates/analyze.html` — render action cell.
- `skills/mnemo-knowledge-auditor/SKILL.md` — auto-action-mode
  section; note that the daemon can now pre-propose actions.
- 6-7 new/extended test files.
- `tests/unit/_snapshots/mcp_tool_list.json` — regen.
- `CHANGELOG.md` — `[5.15.0]` entry.
- `daemon/pyproject.toml`, `daemon/mnemo/__init__.py`,
  `daemon/uv.lock`, `.claude-plugin/plugin.json` — 5.14.0 → 5.15.0.

### Out-of-scope (do NOT touch this release):

- The 5 detectors (stale / duplicates / orphan_references /
  contradictions / semantic_orphans) — unchanged byte-for-byte.
- The retrieval / reindex / chat paths — untouched.
- Any mutating-primitive call path — the proposer is read-only.
- Domain lenses + proactive auditor — later phases.

## 7. Comparison

### 7.1 Pre-v5.15.0 baseline

A finding is `{type, node_ids, description, severity}`. To act, the
user reads the description, opens each cited node, decides the right
operation, and constructs the primitive call by hand. For a
duplicate pair that means reading both bodies and composing a merge.

### 7.2 Post-v5.15.0 target

With the enrichment enabled, each actionable finding arrives with a
ready-to-review `action`: the kind, the primitive, the target, and
suggested args + a rationale. The user reads ONE proposal and
accepts or rejects. The structured action is consumable by the HTTP
API, the MCP response, and the UI alike.

### 7.3 Specific measurable claims

| Metric | Baseline (v5.14.0) | Target (v5.15.0) |
|---|---|---|
| Action proposal | manual, per finding | LLM-generated, attached |
| Default-path LLM calls | 0 | 0 (enrichment is opt-in) |
| Enriched findings per audit (judged corpus) | n/a | ≤ 50 (capped, reported) |
| MCP tool count | 27 | 27 |
| `mnemo_analyze` signature | (types, project_key) | + optional `propose_actions` |
| Daemon test suite | 1560/2skip | 1580+/2skip |
| `concept` field on HTTP | dropped (v5.14.0 bug) | preserved |
| LLM cost (enriched path) | n/a | ≤ $0.10 per audit (≤50 actions × ~1.5k tok on Sonnet) |

### 7.4 Failure-mode comparison

Pre-v5.15.0: the auditor is a problem-finder; the human is the
problem-solver. The graph keeps rotting because acting on each
finding is high-friction. Post-v5.15.0: the auditor is a
problem-finder AND a solution-proposer; acting is one review per
finding. The friction drops enough that maintenance actually
happens.

## 8. Build sequence (TDD)

1. **RED — proposer shape.** `test_refactor_actions.py`: failing
   tests for `LLMRefactorProposer.propose()` returning a structured
   action (mocked client) + graceful kind="none". Run → red.
2. **GREEN — `LLMRefactorProposer`** in `analyzer.py`. Run → green.
3. **RED — env gate.** `test_refactor_proposer_env.py`. Run → red.
4. **GREEN — `refactor_proposer_from_env()`**. Run → green.
5. **RED — enrichment pass.** Extend `test_refactor_actions.py`:
   severity gate, cap + skipped count, no-proposer no-op. Run → red.
6. **GREEN — `propose_refactor_actions()`**. Run → green.
7. **RED — orchestrator wiring.** Extend `test_analyze_detectors.py`:
   `analyze(propose_actions=True, proposer=MagicMock())`. Run → red.
8. **GREEN — wire into `analyze()`**. Run → green.
9. **RED — schema + endpoint + MCP.** Extend
   `test_analyze_endpoint.py` + `test_mnemo_analyze_mcp_tool.py`.
   Run → red.
10. **GREEN — schema fields + route param + tool param.** Run →
    green.
11. **RED — UI.** Extend `test_analyze_ui_page.py`. Run → red.
12. **GREEN — UI template.** Run → green.
13. **Skill update** + `test_knowledge_auditor_skill.py` extension.
14. **MCP wire snapshot regen.**
15. **Full pytest** + ruff. Confirm 1580+/2skip + clean.
16. **Live test** — default path byte-stable on the 12k corpus.
17. **Version bump + CHANGELOG** + ship.
18. **Post-merge daemon restart + reindex + handover.**

## 9. Open questions resolved

- **Q: New MCP tool or a param on `mnemo_analyze`?**
  - A: A param. A new tool would break the 27-count anti-goal and
    fragment the audit surface. An optional param with a default is
    a backward-compatible signature evolution — existing callers
    omit it and get byte-identical behavior. The count stays 27.

- **Q: Enrich every finding, or gate by severity?**
  - A: Gate. The deterministic detectors emit thousands of findings
    (24,776 semantic_orphans alone on the live corpus). A per-finding
    LLM call across all of them is untenable + mostly noise. Gate to
    `("high","medium")` — the confirmed/actionable tier. With the
    LLM judge on, real findings are promoted to `high` and the rest
    dropped, so the eligible set is naturally small.

- **Q: What about the hard cap — why 50 + why report it?**
  - A: Defense in depth. Even within high/medium a pathological
    corpus could have hundreds. 50 bounds worst-case cost (~$0.10).
    Reporting the skipped count honors the "no silent caps" pipeline
    rule — the operator must know coverage was bounded, never read
    "50 actions" as "everything is covered".

- **Q: Why a separate generator class, not reuse a judge?**
  - A: The judges are binary classifiers (`bool`). The proposer is a
    structured generator (an action dict). Different return type,
    different prompt, different `max_tokens`. A sibling class is
    clearer than overloading.

- **Q: Should the proposer ever call a mutating primitive?**
  - A: Never. Phase 1 anti-goal. It returns a PROPOSAL string/dict.
    A confirm-then-apply mode is explicitly Phase 4 (v5.16.0+) behind
    a separate opt-in.

- **Q: Backfill the `concept` field on `AnalyzeFinding`?**
  - A: Yes. v5.14.0 added `concept` to semantic_orphan finding dicts
    but never declared it on the pydantic model, so HTTP silently
    dropped it (pydantic strips unknown fields). Adding it now is a
    pure additive fix that makes the v5.14.0 field survive
    serialization. The MCP path already preserved it (returns the
    raw dict).

- **Q: Where does the skipped count live so it doesn't collide?**
  - A: `summary["_refactor_actions_skipped"]`. The underscore prefix
    keeps it out of the detector-type bucket namespace
    (stale/duplicates/…); summary consumers that iterate detector
    types ignore underscore keys.
