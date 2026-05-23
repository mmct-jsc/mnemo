# mnemo v5.14.0 — Understanding Phase 2b: Semantic Orphans Detector

> **Spec doctrine (pipeline #21, installed v5.12.0):** This doc
> follows the DoD-first template. Sections in order: Summary / Why /
> Spec / Definition of Done / Anti-goals / Scope / Comparison / Build
> sequence / Open questions resolved.

## 1. One-line summary

Add an opt-in **semantic_orphans** detector to the knowledge auditor:
deterministic per-node concept extraction (CamelCase / snake_case /
ALL_CAPS technical identifiers) + cross-reference lookup against
every node's `name` and `description` field, optionally escalated to
a Claude judge for binary "needs definition or not" grading. The
27-tool MCP surface stays byte-stable; the existing 4 detectors
(stale / duplicates / orphan_references / contradictions) stay
unchanged byte-for-byte.

## 2. Why this matters

v5.13.0 shipped Phase 2a — pair-based contradiction detection. Phase
2b ships the second LLM-augmented detector, **semantic_orphans**, on
the other axis: per-NODE concept extraction, not per-PAIR.

The use case (from the user's v6+ vision):

- Vietnamese-law corpus: an article references "Decree 99/2020" but
  no node in the graph DEFINES Decree 99/2020. The reference dangles.
  A reader can't follow the citation. The auditor should surface
  these "ghost concepts".
- Internal docs: a memory says "we use the MQTT-bridge pattern" but
  no other node defines what the MQTT-bridge pattern actually IS. A
  newcomer searching the graph for "MQTT-bridge" finds the mention
  but no anchor.
- Codebase: a docstring mentions a `RetryHandler` class that no
  longer exists (or never existed) in any indexed code node.

The deterministic detector is fast + permissive (catches many
candidates). The opt-in LLM judge filters to actual gaps —
distinguishing "Redis" (common knowledge, doesn't need a node) from
"PetroLimexEdgeOrchestrator" (project-specific term, real orphan).

Contradictions (v5.13.0) was the per-pair LLM detector. Semantic
orphans is the per-node LLM detector. Together they cover the two
fundamental shapes of LLM-augmented structural analysis.

## 3. Spec

### 3.1 Deterministic concept extraction (per-node)

For each node N, walk its body and extract candidate concepts via
three regex patterns:

1. **CamelCase identifiers** with at least 2 internal uppercase
   transitions: `\b[A-Z][a-z]+(?:[A-Z][a-zA-Z0-9]+)+\b`
   - Matches: `MQTTBridge`, `RetryHandler`, `BossEnemyAI`,
     `SonTinhSkills`
   - Skips: `Phase`, `This`, `Note` (single-segment capitalized
     words — too generic)

2. **snake_case identifiers** with at least 2 underscores OR length
   ≥ 12 chars: `\b[a-z][a-z0-9]+(?:_[a-z0-9]+){2,}\b` (for 2+
   underscores) or `\b[a-z][a-z0-9_]{11,}\b` (for length ≥ 12)
   - Matches: `son_tinh_ai`, `petrolimex_detection_model`,
     `knowledge_auditor_phase_1`
   - Skips: `do_not`, `is_thread`, `add_node` (short utility names
     — too generic)

3. **ALL_CAPS constants** with at least 1 underscore:
   `\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b`
   - Matches: `MAX_RETRIES`, `DUPLICATE_COSINE_THRESHOLD`,
     `MNEMO_ANALYZE_LLM_JUDGE`
   - Skips: `URL`, `API`, `SDK` (no underscore — generic acronyms)

Length filter: 6 ≤ len(concept) ≤ 60. Stop-list (skip even when
matched): a small set of common code idioms like `__init__`,
`__main__`, `self_test`, that aren't domain concepts. Per-concept
deduplication within a node (a concept mentioned 10× counts once).

### 3.2 Definition lookup

For each extracted (source_node_id, concept) tuple:

- Search all nodes M where M ≠ source_node_id
- Match condition: case-insensitive substring match against
  `M.name` OR `M.description` (NOT body — a body mention is just
  another reference, not a definition)
- If at least one M matches → concept is DEFINED elsewhere, drop
  the candidate
- If no M matches → concept is a candidate ORPHAN

This is a fast lexical operation. On a 12700-node corpus, the
all-nodes lookup is one in-memory pass + per-concept substring scan
against ~12k name/description pairs. Expected runtime: well under
60 seconds for a full audit.

### 3.3 Opt-in LLM judge confirmation

When `MNEMO_ANALYZE_LLM_JUDGE=1` is set AND `ANTHROPIC_API_KEY`
is present AND the `anthropic` package is importable, the analyzer
escalates each candidate orphan to a Claude judge (default model
`claude-sonnet-4-6`, override via `MNEMO_ANALYZE_JUDGE_MODEL`).

The judge prompt:

```
You are a strict knowledge-graph completeness grader. Given a
concept extracted from a document + the document's surrounding
context, decide whether the concept is a PROJECT-SPECIFIC term that
should have its own dedicated definition node, OR a COMMON term
(industry standard, well-known library, language keyword) that
doesn't need a dedicated definition in this corpus.

Respond ONLY with JSON of the shape:

  {"needs_definition": true|false, "rationale": "<one short paragraph>"}
```

Pairs the judge confirms become severity `high`; pairs the judge
rejects are silently dropped. Network/parse failures degrade
gracefully to the deterministic candidate (severity `candidate`)
+ log a warning.

The judge reuses the same env flag (`MNEMO_ANALYZE_LLM_JUDGE`) as
the v5.13.0 contradictions detector — a single opt-in toggle for
all LLM-augmented detectors. Same model env override
(`MNEMO_ANALYZE_JUDGE_MODEL`). The judge CLASS is sibling to
`LLMContradictionJudge`, not shared (different prompt + different
return semantics).

### 3.4 HTTP / MCP / Skill / UI

Additive changes only:

- `KNOWN_DETECTOR_TYPES` gains `"semantic_orphans"` so callers can
  filter `{"types": ["semantic_orphans"]}`.
- `mnemo_analyze` tool description mentions the new detector + the
  opt-in flag (tool name + signature unchanged).
- `mnemo-knowledge-auditor` SKILL.md gains a "Semantic Orphans"
  section + the proposed-action workflow.
- `/analyze` UI surfaces a new "semantic_orphans" stat card; the
  existing sort order (high → candidate → medium → low) already
  handles `candidate` (from v5.13.0).

### 3.5 Tests

- `tests/unit/test_semantic_orphans_detector.py` — unit tests for
  concept extraction (regex patterns, length filter, stop-list) +
  definition lookup (name/description match, exclude-self).
- `tests/unit/test_semantic_orphans_judge.py` — opt-in LLM judge
  path (mock Anthropic client; verify graceful failure).
- `tests/unit/test_analyze_detectors.py` — extend orchestrator
  test to cover `types=["semantic_orphans"]`.
- `tests/integration/test_analyze_endpoint.py` — extend to cover
  the new types filter value.
- `tests/unit/test_mnemo_analyze_mcp_tool.py` — extend to cover
  the new code path + updated description.
- `tests/unit/test_knowledge_auditor_skill.py` — extend to verify
  the Semantic Orphans section.
- `tests/unit/test_analyze_ui_page.py` — extend to verify the
  new stat card.
- `tests/unit/_snapshots/mcp_tool_list.json` — regen for the
  updated description.

Target: +15-18 new tests; daemon suite goes 1529 → ~1545.

## 4. Definition of Done

- [ ] Design doc (this file) exists with Spec / DoD / Anti-goals
  / Scope / Comparison sections (pipeline #21 check).
- [ ] `detect_semantic_orphans(store, judge=None)` exists in
  `daemon/mnemo/analyzer.py` + emits candidate findings.
- [ ] Three concept-extraction regex patterns + length filter +
  stop-list implemented and unit-tested.
- [ ] Definition lookup excludes the source node + only matches
  against `name` and `description` (not `body`).
- [ ] Opt-in LLM judge via `LLMSemanticOrphanJudge` reusing
  `MNEMO_ANALYZE_LLM_JUDGE` + `ANTHROPIC_API_KEY` env contract.
  Sibling class to `LLMContradictionJudge`, not shared.
- [ ] `analyze()` orchestrator wires the judge through when
  `semantic_orphans` is in the requested types.
- [ ] `KNOWN_DETECTOR_TYPES` gains `"semantic_orphans"` (4 → 5).
- [ ] 15+ new tests all pass.
- [ ] Full daemon suite passes (no regressions on the 1529).
- [ ] Ruff check + format clean.
- [ ] CI 9/9 green on PR.
- [ ] `POST /v1/analyze {"types": ["semantic_orphans"]}` returns
  a well-formed response on the live daemon.
- [ ] `mnemo_analyze` MCP tool returns the same shape.
- [ ] `/analyze` UI renders the new stat card.
- [ ] MEMORY.md promoted to v5.14.0 canonical; v5.13.0 handover
  demoted to SUPERSEDED.
- [ ] **Interactive test**: live audit against the actual corpus
  with `types=["semantic_orphans"]` returns at least one candidate
  orphan finding.
- [ ] Tag `v5.14.0` published on `public`.

## 5. Anti-goals (preserved + new)

- **No LLM-based concept extraction.** Concept extraction stays
  deterministic in v5.14.0; the LLM is the judge only. Per-node
  LLM extraction is a substantial cost increase + would require
  per-node ledger storage. Deferred to v5.15.0+.
- **No body-based definition match.** Definition lookup matches
  `name` + `description` ONLY. A body mention is a REFERENCE,
  not a definition. This keeps precision high.
- **No required LLM.** The default path is deterministic
  candidate detection only; the LLM judge is opt-in. Mirrors
  v5.13.0's pattern.
- **No silent edits.** Still in force from Phase 1. The auditor
  surfaces; the user acts.
- **No MCP surface count change.** Still 27 tools.
  `mnemo_analyze` gains a new detector branch internally; the
  tool name/signature is unchanged.
- **No refactor_action generation this release.** Phase 2c
  (v5.15.0). For each orphan, generating a proposed scaffold
  ("create a definition node for X") is a different ergonomic.
- **No domain lenses.** Phase 3, v5.14.0+. The semantic_orphans
  detector here is domain-agnostic.
- **No proactive scheduling.** Phase 4, v5.15.0+.
- **No new daemon dependencies.** `anthropic` already a runtime
  dep from v5.13.0.

## 6. Scope — files touched + boundary

### In-scope (must change):

- `daemon/mnemo/analyzer.py` — ADD `detect_semantic_orphans`,
  `LLMSemanticOrphanJudge` class, `semantic_orphan_judge_from_env()`
  helper; add `"semantic_orphans"` to `KNOWN_DETECTOR_TYPES`;
  update `analyze()` to wire the judge.
- `daemon/mnemo/agent_tools.py` — update `mnemo_analyze`
  description to mention the new detector.
- `daemon/mnemo/ui/templates/analyze.html` — render the
  `semantic_orphans` stat card; no other UI changes.
- `skills/mnemo-knowledge-auditor/SKILL.md` — add a Semantic
  Orphans section describing the workflow + env flag.
- 7-8 new/extended test files (per §3.5).
- `tests/unit/_snapshots/mcp_tool_list.json` — regen.
- `CHANGELOG.md` — `[5.14.0]` entry.
- `daemon/pyproject.toml`, `daemon/mnemo/__init__.py`,
  `daemon/uv.lock`, `.claude-plugin/plugin.json` — 5.13.0 → 5.14.0.

### Out-of-scope (do NOT touch this release):

- The 4 existing detectors (stale, duplicates, orphan_references,
  contradictions) — unchanged byte-for-byte.
- The `/analyze` UI layout — only the stat-card row may grow.
- The retrieval / reindex / chat paths — untouched.
- The bench package — its LLM judge stays in its own module.

## 7. Comparison

### 7.1 Pre-v5.14.0 baseline

Today (v5.13.0), if a user wants to find dangling concept references
in their mnemo corpus, the auditor doesn't help. They must:

1. List the nodes (paginated).
2. Manually scan bodies for ALL_CAPS, CamelCase, snake_case terms.
3. Manually search the graph to see if each term is defined elsewhere.

On a 12700-node corpus with potentially 100k+ extracted terms this
is intractable.

### 7.2 Post-v5.14.0 target

`mnemo analyze` returns candidate semantic-orphan findings in
**under 60 seconds** on the same corpus. With the opt-in LLM judge
enabled, confirmed orphans are tagged `high` severity and ordered
first.

### 7.3 Specific measurable claims

| Metric | Baseline (v5.13.0) | Target (v5.14.0) |
|---|---|---|
| Semantic-orphan detection time on 12700 nodes | intractable (manual) | < 60s (deterministic) / < 300s (LLM judge) |
| Candidate-finding precision (deterministic) | n/a | ≥ 20% are real project-specific orphans (rest are common-term false positives) |
| LLM-confirmed precision | n/a | ≥ 80% are real project-specific orphans |
| Daemon MCP surface | 27 tools | 27 tools (no change) |
| Daemon test suite | 1529/2skip | 1545+/2skip |
| Detector count | 4 | 5 |
| LLM cost (judged path) | n/a | ≤ $0.20 per audit on 12700 nodes (Sonnet at ~100 candidate concepts × 1.5k tokens each) |

### 7.4 Failure-mode comparison

Pre-v5.14.0: dangling references accumulate silently; the corpus
gradually grows holes where every reader thinks "wait, what's that?"
and the knowledge graph fails its primary job. Post-v5.14.0: dangling
concepts surface on demand; the user creates definition nodes for
the high-severity ones.

## 8. Build sequence (TDD)

1. **RED — concept extraction.** Write
   `tests/unit/test_semantic_orphans_detector.py` with failing
   tests for the three regex patterns + length filter + stop-list.
   Run → red.
2. **GREEN — `_extract_concepts(body)` helper** in `analyzer.py`.
   Run → green.
3. **RED — definition lookup.** Extend the same test file with
   failing tests for the cross-reference logic (exclude self;
   match name/description not body).
   Run → red.
4. **GREEN — `_concept_is_defined(...)` helper** in `analyzer.py`.
   Run → green.
5. **RED — orchestrator integration (deterministic path).**
   Extend the same test file with a fixture that wires concept +
   nodes + asserts a candidate finding emerges.
   Run → red.
6. **GREEN — `detect_semantic_orphans` function** that ties
   extraction + lookup together. Run → green.
7. **RED — LLM judge module.** Write
   `tests/unit/test_semantic_orphans_judge.py` with mocked
   Anthropic client. Run → red.
8. **GREEN — `LLMSemanticOrphanJudge` + factory helper** in
   `analyzer.py`. Run → green.
9. **RED — orchestrator wiring (judge path).** Extend
   `test_analyze_detectors.py` with `types=["semantic_orphans"]`
   + judge=MagicMock variant. Run → red.
10. **GREEN — wire judge through `analyze()`** in `analyzer.py`.
    Run → green.
11. **RED — endpoint + MCP.** Extend
    `test_analyze_endpoint.py` + `test_mnemo_analyze_mcp_tool.py`.
    Run → red.
12. **GREEN — endpoint passes types through** (already does;
    probably no code change) + update `mnemo_analyze`
    description.
13. **RED — UI.** Extend `test_analyze_ui_page.py` to assert the
    `semantic_orphans` stat card appears.
14. **GREEN — UI template update.** Run → green.
15. **Skill update.** Add Semantic Orphans section to SKILL.md;
    extend `test_knowledge_auditor_skill.py`.
16. **MCP wire snapshot regen.**
17. **Full pytest** + ruff. Confirm 1545+/2skip + clean.
18. **Live test (deterministic).** `curl POST /v1/analyze
    {"types": ["semantic_orphans"]}` against the running daemon.
19. **Live test (LLM judge, if user has API key).** Same call
    with `MNEMO_ANALYZE_LLM_JUDGE=1` set (skipped in autonomous
    session if no key).
20. **Version bump + CHANGELOG** + ship.
21. **Post-merge daemon restart + reindex + handover doc**.

## 9. Open questions resolved

- **Q: Why deterministic extraction first, not LLM extraction
  from the start?**
  - A: Cost + complexity. Per-node LLM extraction on a 12700-node
    corpus is expensive (hundreds of LLM calls minimum) and
    ergonomically awkward (must store per-node concept ledger;
    invalidate on body change). Deterministic extraction is
    free, fast, and the regex patterns above catch the
    high-signal cases. The LLM JUDGE filters false positives.
    LLM extraction can land in v5.15.0+ as an opt-in
    enrichment pass once the surface ergonomics are clear.

- **Q: Definition lookup against name+description only — why
  not body?**
  - A: A body mention is a reference, not a definition. If node
    A mentions "MQTTBridge" in its body and node B's body also
    mentions "MQTTBridge", neither node DEFINES MQTTBridge.
    The definition would be a node whose primary topic IS
    MQTTBridge — and that signal lives in name/description by
    convention. Matching body would yield trivially many
    "definitions" (any cross-reference counts) and the detector
    would silently produce no findings.

- **Q: Why share the env flag (`MNEMO_ANALYZE_LLM_JUDGE`)
  between contradictions and semantic_orphans?**
  - A: One opt-in toggle for "use LLM in the auditor" keeps the
    surface simple. Users don't have to remember 4 env vars for
    4 detectors. The judge model is also shared
    (`MNEMO_ANALYZE_JUDGE_MODEL`). Per-detector model overrides
    can land later if needed.

- **Q: Why a separate judge CLASS vs reusing
  `LLMContradictionJudge`?**
  - A: Different prompts + different return semantics. The
    contradictions judge returns "yes this is a contradiction"
    (binary classifier over a pair). The orphan judge returns
    "yes this needs a definition" (binary classifier over a
    single concept-in-context). Reusing one class would
    overload the prompt with conditional branches; two sibling
    classes are clearer.

- **Q: Stop-list — how is it bootstrapped?**
  - A: Hand-picked initial list of common code idioms that
    aren't domain concepts (`__init__`, `__main__`, `self_test`,
    `do_not`, common 2-segment CamelCase like `IsValid`). v6+
    domain lenses (Phase 3) can extend it.

- **Q: What about very small corpora (e.g., 10 nodes)?**
  - A: The detector still runs. It's likely to produce more
    findings (proportional to body content) but at small scale
    the user can review them all. The deterministic path is
    safe — no LLM cost.

- **Q: Cost-cap the LLM judge?**
  - A: Implicit via the deterministic candidate gate. On a
    12700-node corpus, expect <100 candidate concepts (most
    extracted concepts ARE defined somewhere or are common
    terms filtered by stop-list). At 1.5k tokens each, that's
    ~$0.15 on Sonnet. Acceptable for an opt-in audit.
