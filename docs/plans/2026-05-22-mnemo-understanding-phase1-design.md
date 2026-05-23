# mnemo v5.12.0 — Understanding Phase 1: Knowledge Auditor

> **Spec doctrine note (new pipeline #21):** This doc is the first to
> follow the DoD-first template installed at user direction
> 2026-05-22. Every future feature MUST start with this template
> filled out before any code is written. The Definition of Done
> section is the contract used for sign-off; the Comparison section
> is what we measure against; the Anti-goals section is the wall
> against scope creep.

## 1. One-line summary

Add a deterministic **knowledge-auditor** layer that walks the
existing node graph + surfaces three structural issues (stale,
duplicate, orphan-reference) via a new HTTP endpoint, MCP tool,
slash-command-callable skill, and UI page.

## 2. Why this matters

mnemo v1-v5 is hybrid Graph-RAG: it indexes, embeds, retrieves +
cites. It does NOT actively reason about the content. The user's
2026-05-22 directive: mnemo should evolve to **UNDERSTAND** the
corpus — concepts, dependencies, contradictions — and propose
fixes when the corpus is "a mess" (Vietnamese-law / internal-policy
/ messy codebase). v5.12.0 is Phase 1 of that arc — the foundation
substrate the LLM-augmented phases (v5.13.0+) sit on.

Full long-term vision: see memory entry
`project_mnemo_v6_vision_understanding`.

## 3. Spec — what this delivers

### 3.1 Three deterministic detectors

Each detector operates on the existing node graph; no LLM call
required.

#### 3.1.1 `stale`

A node is **stale** when its body or description contains the
literal token `SUPERSEDED` (case-insensitive). The convention is
already used across mnemo's session-handover memory entries (e.g.,
"SUPERSEDED by v5.11.0").

- Output shape: `{type: "stale", node_ids: [<id>], description:
  "Body/description marks this node as superseded by another;
  consider archiving"}`.
- Severity: `low` (informational; the user explicitly marked it).

#### 3.1.2 `duplicates`

A **duplicate pair** is two nodes with the SAME `node_type`,
different `id`, and cosine similarity of their embeddings
`>= 0.95`. (Embeddings already live in sqlite-vec; the detector
just does pairwise comparison within type buckets to keep the
cost manageable: O(N²) per type, but N per type is small —
typically 10-100.)

- Output shape: `{type: "duplicates", node_ids: [<id_a>, <id_b>],
  description: "Two <type> nodes with cosine similarity X.XX;
  consider merging or marking one as superseded", severity:
  "medium"}`.

#### 3.1.3 `orphan_references`

A node is an **orphan-reference source** when its body contains
a `[mnemo:<id>]` token where `<id>` doesn't exist in the
current node graph. (We use the existing citation convention
that v1.0+ mandates for retrieval results.)

- Output shape: `{type: "orphan_reference", node_ids: [<source_id>],
  description: "Node body references mnemo:<missing_id> which is
  not in the graph; either the target was deleted or never
  existed", severity: "high"}`.

### 3.2 HTTP endpoint

`POST /v1/analyze` (no body required; optional `{types: [...]}` to
filter).

Default behavior: run all 3 detectors, return aggregate.

Response shape:

```json
{
  "ran_at": "2026-05-22T16:00:00Z",
  "node_count_scanned": 12606,
  "findings": [
    {
      "type": "stale",
      "node_ids": ["memory_session/session_handover_v5_10_0_shipped"],
      "description": "...",
      "severity": "low"
    },
    ...
  ],
  "summary": {
    "stale": 6,
    "duplicates": 0,
    "orphan_references": 2
  }
}
```

### 3.3 MCP tool

`mnemo_analyze` is the 27th tool on the MCP stdio surface. Same
shape as `POST /v1/analyze`. Args: optional `types: list[str]`.

### 3.4 Skill: `mnemo:knowledge-auditor`

A new SKILL.md at `skills/mnemo-knowledge-auditor/SKILL.md`
documenting the workflow:

1. Call `mnemo_analyze` (no args).
2. Group findings by `severity`.
3. For each finding, propose a concrete action using existing
   primitives (`mnemo_update_node` to mark stale, `mnemo_delete_node`
   to remove a duplicate, etc.).
4. Print the findings + action proposals as a Markdown report.

The skill is read by both the dock pill (any mnemo page) and the
existing `/mnemo-prompt` slash-command flow (v5.8.0).

### 3.5 UI page

`/analyze` renders findings in a sortable table. Each row shows
type / severity / node_ids (linked to the existing
`/node/<id>` detail pages) / description / suggested action.

Cosmetic-only — no edit buttons in Phase 1. (Anti-goal: no silent
edits.)

### 3.6 Tests

- `tests/unit/test_analyze_detectors.py`: unit tests per detector
  on synthetic fixtures.
- `tests/integration/test_analyze_endpoint.py`: end-to-end
  endpoint contract.
- `tests/unit/test_mnemo_analyze_mcp_tool.py`: MCP tool registration
  + arg-handling.
- `tests/unit/test_knowledge_auditor_skill.py`: skill file content
  contract (mirrors the prompt-architect skill tests).
- `tests/unit/test_analyze_ui_page.py`: UI page exists + renders
  the findings list.

Target: +15-20 unit tests; daemon suite goes from 1479 → ~1497-1500.

## 4. Definition of Done

A v5.12.0 release is COMPLETE when ALL of the following hold:

- [ ] All 5 new test files exist; all new tests pass.
- [ ] Daemon test suite passes (no regressions in the existing 1479).
- [ ] `ruff check` + `ruff format --check` both clean.
- [ ] CI 9/9 green on PR.
- [ ] `POST /v1/analyze` returns a well-formed response on a
  live daemon against the actual corpus (~12606 nodes).
- [ ] `mnemo_analyze` MCP tool is callable from Claude Desktop
  and returns the same shape.
- [ ] `/analyze` UI page renders findings.
- [ ] The 26-tool MCP surface contract test is updated to 27
  WITHOUT breaking the byte-stability of the existing 26 tools.
- [ ] The MEMORY.md canonical entry promotion is done + the new
  session_handover_v5_12_0_shipped doc is reachable.
- [ ] **Interactive test**: a human (me) runs `mnemo analyze`
  on the corpus + reviews the findings + confirms at least one
  finding is real + actionable.
- [ ] Tag `v5.12.0` published on `public`.

## 5. Anti-goals — what this release does NOT do

- **No LLM calls.** All Phase 1 detectors are deterministic.
  LLM-augmented detection (contradictions, semantic orphans,
  refactor proposals) is gated to v5.13.0+ behind an opt-in env
  flag, mirroring the bench's LLM judge pattern.
- **No automatic edits.** The auditor surfaces; the user acts.
  There is no `POST /v1/analyze/apply` in this release.
- **No domain lenses.** Vietnamese-law-specific / code-specific /
  research-notes-specific analyzers are Phase 3 (v5.14.0+).
- **No background scheduling.** The auditor runs on demand; the
  "proactive auditor that runs on every reindex" is Phase 4
  (v5.15.0+).
- **No new daemon dependencies.** Uses only embedder + store +
  graph; no new packages.
- **No MCP surface breakage.** The 26 existing tools stay
  byte-stable; `mnemo_analyze` is purely additive.

## 6. Scope — files touched + boundary

### In-scope (must change):

- `daemon/mnemo/analyzer.py` — NEW. The 3 detectors + the
  `analyze()` orchestrator.
- `daemon/mnemo/server.py` — ADD `POST /v1/analyze` route.
- `daemon/mnemo/mcp_server.py` — REGISTER `mnemo_analyze` tool.
- `daemon/mnemo/ui/templates/analyze.html` — NEW UI page.
- `daemon/mnemo/ui/__init__.py` (or wherever routes are wired) —
  ADD route for `/analyze`.
- `skills/mnemo-knowledge-auditor/SKILL.md` — NEW skill markdown.
- `daemon/tests/unit/test_analyze_detectors.py` — NEW.
- `daemon/tests/integration/test_analyze_endpoint.py` — NEW.
- `daemon/tests/unit/test_mnemo_analyze_mcp_tool.py` — NEW.
- `daemon/tests/unit/test_knowledge_auditor_skill.py` — NEW.
- `daemon/tests/unit/test_analyze_ui_page.py` — NEW.
- Existing `daemon/tests/unit/test_mcp_tools_surface.py` — UPDATE
  count assertion 26 → 27.
- `CHANGELOG.md` — `[5.12.0]` entry.
- `daemon/mnemo/__init__.py`, `daemon/pyproject.toml`,
  `daemon/uv.lock`, `.claude-plugin/plugin.json` — version bump.

### Out-of-scope (do NOT touch this release):

- Any `bench/` code (v5.11.0 is the bench release; v5.12.0 is
  daemon).
- Any retrieval logic — the auditor reads the graph as-is.
- Any reindex logic — the auditor doesn't trigger reindex.
- The 26 existing MCP tools — byte-stable contract.

## 7. Comparison — what we measure success against

This is the new pipeline-#21 mandate: every release names the
**baseline** we improve over.

### 7.1 Pre-v5.12.0 baseline

Today (v5.11.0), if a user wants to audit their mnemo corpus for
duplicates / stale / orphan-references, they have to:

1. Manually list nodes via `mnemo` CLI or the `/nodes` page (paginated, 461 pages).
2. Eyeball each node's body for `SUPERSEDED` markers.
3. Manually compare bodies for duplicates (no semantic-similarity
   surface).
4. Manually grep `[mnemo:<id>]` references + cross-check.

Estimated time on the 12606-node corpus: **multiple hours**.
Result: most users don't bother.

### 7.2 Post-v5.12.0 target

`mnemo analyze` returns the same audit findings in **under 30
seconds** on a 12606-node corpus. The findings are structured
(not free-text) so downstream tools (`mnemo_update_node` /
`mnemo_delete_node`) can act on them programmatically.

### 7.3 Specific measurable claims

We claim that v5.12.0 delivers:

| Metric | Baseline (v5.11.0) | Target (v5.12.0) |
|---|---|---|
| Time to audit 12606 nodes | hours (manual) | < 30 s |
| Stale-node detection (precision) | n/a (manual eyeball) | ≥ 95% on known SUPERSEDED markers |
| Duplicate-pair detection (recall) | 0 (no tool) | ≥ 1 real pair if any exist in corpus |
| Orphan-reference detection | 0 (manual grep) | finds 100% of dangling `[mnemo:X]` |
| Daemon MCP surface | 26 tools | 27 tools (additive) |
| Daemon test suite | 1479/2skip | 1497-1500/2skip |
| Interactive smoke test | n/a | runs against live corpus, finds ≥ 1 real issue |

### 7.4 Failure-mode comparison

The pre-v5.12.0 baseline silently corrodes — stale handovers
accumulate, duplicates pile up, citations rot. The post-v5.12.0
baseline surfaces drift on demand so the user can act on it
before it becomes unmaintainable. The substrate is the same;
the FEEDBACK LOOP is what's new.

## 8. Build sequence (TDD)

1. **RED — analyzer detectors.** Write
   `tests/unit/test_analyze_detectors.py` with failing tests for
   each detector against synthetic fixtures. Run → red.
2. **GREEN — `daemon/mnemo/analyzer.py`.** Implement 3 detectors
   + `analyze()` orchestrator. Run → green.
3. **RED — HTTP endpoint.** Write
   `tests/integration/test_analyze_endpoint.py` with failing tests
   for the new route shape. Run → red.
4. **GREEN — `server.py` route.** Add the route. Run → green.
5. **RED — MCP tool.** Write
   `tests/unit/test_mnemo_analyze_mcp_tool.py`. Run → red.
6. **GREEN — `mcp_server.py` tool.** Register. Run → green +
   surface-test goes 26 → 27.
7. **RED — skill.** Write
   `tests/unit/test_knowledge_auditor_skill.py`. Run → red.
8. **GREEN — SKILL.md.** Write the skill markdown. Run → green.
9. **RED — UI page.** Write
   `tests/unit/test_analyze_ui_page.py`. Run → red.
10. **GREEN — UI page + route.** Implement. Run → green.
11. **Full pytest** + ruff. Confirm 1497-1500 / 2 skipped + clean.
12. **Live interactive test.** `curl POST /v1/analyze` against
    the running daemon (~12606 nodes) + review findings + confirm
    ≥ 1 real issue.
13. **Version bump + CHANGELOG** + ship through standard release
    pipeline (branch → PR → CI → merge → tag).
14. **Post-merge interactive verification**. Confirm the dock
    pill / `/analyze` UI / MCP tool / skill all surface the new
    capability end-to-end.
15. **Handover doc** + MEMORY.md promotion.

## 9. Open questions resolved

These were considered during design + closed before writing code:

- **Q: Use cosine 0.90, 0.95, or 0.98 for duplicate threshold?**
  - A: 0.95. 0.98 misses real near-duplicates (different wording,
    same meaning); 0.90 false-positives on closely-related
    siblings. 0.95 is the well-known sentence-transformers
    near-duplicate threshold.
- **Q: O(N²) cost on 12606 nodes — is this acceptable?**
  - A: We bucket by `node_type` first; the largest bucket is
    ~4500 (`code_method`) which is 20M pairs at ~1µs each = 20s
    worst case. sqlite-vec can do this much faster via batch
    lookups, so realistically under 5s. If we ever exceed,
    Phase 2 switches to ANN (approximate nearest neighbor) over
    the existing sqlite-vec index.
- **Q: What about cross-type duplicates (a memory_feedback that
  duplicates a memory_project)?**
  - A: Out of scope for Phase 1. Type-bucketing keeps the
    contract simple. Phase 3 (domain lenses) revisits.
- **Q: Should `stale` be lexical or semantic?**
  - A: Lexical for Phase 1 — we already control the SUPERSEDED
    convention; we don't need an LLM to detect it. Semantic
    stale-detection ("this looks like an older version of X")
    is Phase 2.
- **Q: How does the auditor handle BASE-flagged nodes?**
  - A: It runs on all nodes including BASE. BASE-flagged nodes
    typically have rich bodies; the duplicate detector would
    catch if a BASE-flagged reference is mis-duplicated.

## 10. The new pipeline #21 (BASE-flagged update)

After this design lands, update `reference_mnemo_pipelines.md`
(BASE-flagged) with pipeline #21:

> **#21 DoD-first specs — every feature ships with a design doc
> that includes Spec, Definition of Done, Anti-goals, Scope, and
> Comparison sections BEFORE any code is written.** The spec is
> the contract for "what we're building" + the DoD is the
> sign-off checklist + the Comparison is the baseline we measure
> success against. Anti-goals is the wall against scope creep.
> Use this design doc (2026-05-22-mnemo-understanding-phase1-design.md)
> as the canonical template.
