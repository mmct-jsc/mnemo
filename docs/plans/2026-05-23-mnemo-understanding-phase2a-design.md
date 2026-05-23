# mnemo v5.13.0 — Understanding Phase 2a: Contradictions Detector

> **Spec doctrine (pipeline #21, installed v5.12.0):** This doc
> follows the DoD-first template. Sections in order: Summary / Why /
> Spec / Definition of Done / Anti-goals / Scope / Comparison / Build
> sequence / Open questions resolved.

## 1. One-line summary

Add an opt-in **contradictions** detector to the knowledge auditor:
deterministic candidate-pair selection by cosine + lexical negation
differential, optionally escalated to an LLM judge (Claude) for
confirmation. The 27-tool MCP surface stays byte-stable; the
existing 3 detectors (stale / duplicates / orphan_references) stay
unchanged.

## 2. Why this matters

v5.12.0 shipped Phase 1 (deterministic detectors): the auditor
surfaces stale entries, duplicates, and orphan references — all
without any LLM call. Phase 2 of the Understanding arc adds
LLM-augmented detection where the deterministic shape isn't enough.

**Contradictions** is the highest-value Phase 2 capability:

- Vietnamese-law use case (canonical non-code example from the
  user's vision): laws + decrees + exceptions with mutually
  exclusive consequences. A purely lexical / cosine detector
  can't tell "X is required if Y" apart from "X is forbidden if
  Y" — the embeddings are similar (same domain), the
  surface-form negation might be subtle. An LLM judge can.
- Internal docs: "use Redis" in one design doc vs "do not add
  Redis (removed 2026-03)" in a feedback memory. The two
  appear as similar-topic nodes (high embedding similarity) but
  with opposite prescriptions.
- Codebase: two API endpoints documented as the canonical
  surface (drift over releases).

Semantic-orphans + refactor-action generation are also Phase 2
work in the vision doc but split into v5.14.0+ (Phase 2b) to
keep each release bounded.

## 3. Spec

### 3.1 Deterministic candidate-pair selection

For each pair `(A, B)` of same-type nodes:

- Cosine similarity in `[0.5, 0.85]` (similar enough to be about
  the same topic; different enough to potentially conflict).
- Lexical "negation differential": one node's body contains a
  negation pattern that the other doesn't, OR vice versa.
  Patterns matched (case-insensitive):
  `do not`, `don't`, `never`, `no longer`, `deprecated`,
  `removed`, `instead of`, `forbidden`, `disallowed`,
  `must not`, `should not`, `avoid`.

A pair that passes both gates becomes a **candidate**. The
default detection mode emits candidates directly with severity
`candidate` — the user reviews. This is the no-LLM path.

### 3.2 Opt-in LLM judge confirmation

When `MNEMO_ANALYZE_LLM_JUDGE=1` is set AND `ANTHROPIC_API_KEY`
is present AND the `anthropic` package is importable, the
analyzer escalates each candidate pair to a Claude judge
(default model `claude-sonnet-4-6`, override via
`MNEMO_ANALYZE_JUDGE_MODEL`).

The judge prompt:

```
You are a strict contradiction grader for a knowledge corpus.
Given two related text snippets, decide whether they are
mutually contradictory (one explicitly negates or forbids what
the other prescribes). Respond ONLY with JSON of the shape:

  {"contradiction": true|false, "rationale": "<one short paragraph>"}
```

Pairs the judge confirms become severity `high`; pairs the judge
rejects are silently dropped. Network/parse failures degrade
gracefully to the deterministic candidate (severity `candidate`)
+ log a warning.

### 3.3 HTTP / MCP / Skill / UI

Additive changes only:

- `KNOWN_DETECTOR_TYPES` gains `"contradictions"` so callers can
  filter `{"types": ["contradictions"]}`.
- `mnemo_analyze` tool description mentions the new detector +
  the opt-in flag.
- `mnemo-knowledge-auditor` SKILL.md gains a "Contradictions"
  section + the env-flag setup.
- `/analyze` UI surfaces a new badge for `candidate` (medium)
  vs `high` (LLM-confirmed) severity.

### 3.4 Tests

- `tests/unit/test_contradictions_detector.py` — unit tests for
  candidate-pair selection (cosine bands, negation patterns).
- `tests/unit/test_contradictions_judge.py` — opt-in LLM judge
  path (mock Anthropic client; verify graceful failure).
- `tests/integration/test_analyze_endpoint.py` — extended to
  cover `types=["contradictions"]` filter and the new severity
  values.
- `tests/unit/test_mnemo_analyze_mcp_tool.py` — extended to
  cover the contradictions code path.
- `tests/unit/test_mcp_tool_surface_contract.py` — no change
  (tool name unchanged; only description updated).
- `tests/unit/_snapshots/mcp_tool_list.json` — regen for the
  updated description.

Target: +8-10 new tests; daemon suite goes 1510 → ~1520.

## 4. Definition of Done

- [ ] Design doc (this file) exists with Spec / DoD / Anti-goals
  / Scope / Comparison sections (pipeline #21 check).
- [ ] `detect_contradictions(store, embedder, judge=None)`
  exists in `daemon/mnemo/analyzer.py` + emits candidate findings.
- [ ] Opt-in LLM judge via `judge_from_env()` reading
  `MNEMO_ANALYZE_LLM_JUDGE` + `ANTHROPIC_API_KEY` returns either
  an `LLMContradictionJudge` instance OR `None` (graceful fallback).
- [ ] `analyze()` orchestrator wires the judge through when
  `contradictions` is in the requested types.
- [ ] `KNOWN_DETECTOR_TYPES` gains `"contradictions"`.
- [ ] 8+ new test files all pass.
- [ ] Full daemon suite passes (no regressions on the 1510).
- [ ] Ruff check + format clean.
- [ ] CI 9/9 green on PR.
- [ ] `POST /v1/analyze {"types": ["contradictions"]}` returns
  a well-formed response on the live daemon.
- [ ] `mnemo_analyze` MCP tool returns the same shape.
- [ ] `/analyze` UI renders the new severity badges.
- [ ] MEMORY.md promoted to v5.13.0 canonical; v5.12.0 handover
  demoted to SUPERSEDED.
- [ ] **Interactive test**: live audit against the actual corpus
  with `types=["contradictions"]` returns at least one candidate
  pair (likely the v5.10.0 / v5.10.0-superseded pair, or a
  similar drift).
- [ ] Tag `v5.13.0` published on `public`.

## 5. Anti-goals (preserved + new)

- **No required LLM.** The default path is deterministic
  candidate selection only; the LLM judge is opt-in. Mirrors the
  bench's LLM judge pattern (v5.11.0).
- **No silent edits.** Still in force from Phase 1. The auditor
  surfaces; the user acts.
- **No MCP surface count change.** Still 27 tools.
  `mnemo_analyze` gains a new detector branch internally; the
  tool name/signature is unchanged.
- **No semantic_orphans detector this release.** Phase 2b
  (v5.14.0). Concept extraction is a substantially different
  LLM pattern (per-node, not per-pair).
- **No refactor_action generation this release.** Phase 2c
  (v5.14.0+ or v5.15.0). For each finding, generating a
  proposed action is a different ergonomic + cost profile.
- **No domain lenses (vietnamese-law / code / research-notes).**
  Phase 3, v5.14.0+. The contradictions detector here is
  domain-agnostic.
- **No proactive scheduling.** Phase 4, v5.15.0+.
- **No new daemon dependencies.** `anthropic` is already a
  runtime dep for the chat companion (`v3` phase); the LLM judge
  reuses it.

## 6. Scope — files touched + boundary

### In-scope (must change):

- `daemon/mnemo/analyzer.py` — ADD `detect_contradictions`,
  `LLMContradictionJudge` class, `judge_from_env()` helper;
  add `"contradictions"` to `KNOWN_DETECTOR_TYPES`; update
  `analyze()` to wire the judge.
- `daemon/mnemo/api_schemas.py` — `AnalyzeIn.types` Literal
  values (if validated) need `"contradictions"`; otherwise
  unchanged (the model is permissive).
- `daemon/mnemo/agent_tools.py` — update `mnemo_analyze`
  description to mention the new detector + opt-in flag.
- `daemon/mnemo/ui/templates/analyze.html` — render the
  `candidate` severity badge; no other UI changes.
- `skills/mnemo-knowledge-auditor/SKILL.md` — add a
  Contradictions section describing the workflow + env flag.
- 4 new test files (per §3.4).
- `tests/unit/_snapshots/mcp_tool_list.json` — regen.
- `CHANGELOG.md` — `[5.13.0]` entry.
- `daemon/pyproject.toml`, `daemon/mnemo/__init__.py`,
  `daemon/uv.lock`, `.claude-plugin/plugin.json` — 5.12.0 → 5.13.0.

### Out-of-scope (do NOT touch this release):

- The 3 existing Phase 1 detectors (stale, duplicates,
  orphan_references) — unchanged.
- The `/analyze` UI layout — only the severity badge styling
  may add a new color for `candidate`.
- The retrieval / reindex / chat paths — untouched.
- The bench package — v5.11.0's LLM judge stays in its own
  module; the analyzer judge is a SIBLING implementation (not
  imported from bench).

## 7. Comparison

### 7.1 Pre-v5.13.0 baseline

Today (v5.12.0), if a user wants to find contradictions in their
mnemo corpus, the auditor doesn't help. They must:

1. List the nodes (paginated).
2. Manually skim for pairs of similar-topic-different-prescription
   nodes.
3. Decide if each pair is actually contradictory.

On a 12700-node corpus this is intractable.

### 7.2 Post-v5.13.0 target

`mnemo analyze` returns candidate contradiction pairs in **under
60 seconds** on the same corpus. With the opt-in LLM judge
enabled, confirmed contradictions are tagged `high` severity and
ordered first.

### 7.3 Specific measurable claims

| Metric | Baseline (v5.12.0) | Target (v5.13.0) |
|---|---|---|
| Contradiction detection time on 12700 nodes | intractable (manual) | < 60s (deterministic) / < 180s (LLM judge) |
| Candidate-pair precision (deterministic) | n/a | ≥ 50% are real contradictions (rest are domain-overlap false positives) |
| LLM-confirmed precision | n/a | ≥ 90% are real contradictions (per judge prompt) |
| Daemon MCP surface | 27 tools | 27 tools (no change) |
| Daemon test suite | 1510/2skip | 1520+/2skip |
| LLM cost (judged path) | n/a | ≤ $0.05 per audit on 12700 nodes (Sonnet at ~10 candidate pairs × 1k tokens each) |

### 7.4 Failure-mode comparison

Pre-v5.13.0: contradictions accumulate silently; the corpus
gradually rots into "two competing versions of the truth" with
no surfacing mechanism. Post-v5.13.0: contradictions surface on
demand; the user can act on the highest-severity drift before
it propagates.

## 8. Build sequence (TDD)

1. **RED — candidate selection.** Write
   `tests/unit/test_contradictions_detector.py` with failing
   tests for cosine band + negation differential. Run → red.
2. **GREEN — `detect_contradictions` deterministic path** in
   `analyzer.py`. Run → green.
3. **RED — LLM judge module.** Write
   `tests/unit/test_contradictions_judge.py` with mocked
   Anthropic client. Run → red.
4. **GREEN — `LLMContradictionJudge` + `judge_from_env`** in
   `analyzer.py`. Run → green.
5. **RED — orchestrator wiring.** Extend
   `test_analyze_detectors.py` with `types=["contradictions"]`
   + summary entry. Run → red.
6. **GREEN — wire judge through `analyze()`** in `analyzer.py`.
   Run → green.
7. **RED — endpoint + MCP.** Extend
   `test_analyze_endpoint.py` + `test_mnemo_analyze_mcp_tool.py`.
   Run → red.
8. **GREEN — endpoint passes types through** (already does;
   probably no code change) + update `mnemo_analyze`
   description.
9. **RED — UI.** Extend `test_analyze_ui_page.py` to assert the
   `candidate` severity badge appears.
10. **GREEN — UI template update.** Run → green.
11. **Skill update.** Add Contradictions section to SKILL.md;
    extend `test_knowledge_auditor_skill.py` if needed.
12. **MCP wire snapshot regen.**
13. **Full pytest** + ruff. Confirm 1520+/2skip + clean.
14. **Live test (deterministic).** `curl POST /v1/analyze
    {"types": ["contradictions"]}` against the running daemon.
15. **Live test (LLM judge, if user has API key).** Same call
    with `MNEMO_ANALYZE_LLM_JUDGE=1` set.
16. **Version bump + CHANGELOG** + ship.
17. **Post-merge daemon restart + reindex + handover doc**.

## 9. Open questions resolved

- **Q: Cosine bounds 0.5-0.85 or 0.4-0.90?**
  - A: 0.5-0.85. Below 0.5 the topics are too different (no
    contradiction possible; the two snippets aren't talking
    about the same thing). Above 0.85 it's near-duplicate
    territory (handled by the existing `duplicates` detector,
    which catches reformulations of the same prescription, not
    contradictions). The 0.5-0.85 band is the "same topic,
    different prescription" sweet spot.
- **Q: Use the bench's LLM judge module?**
  - A: No — sibling implementation in `analyzer.py`. The bench
    judge has a rubric-grading shape (multi-criterion); the
    contradiction judge is a binary classifier with rationale.
    Reusing would couple the daemon to the bench package.
- **Q: What if a node's body is huge (e.g., a whole
  reference doc)?**
  - A: Cap body excerpts at 2000 chars per snippet sent to the
    LLM. The judge has the cosine score + descriptions; full
    body isn't needed for a yes/no decision.
- **Q: What if both nodes in a candidate pair contain
  negation patterns?**
  - A: Still a candidate (a pair where both contain "do not X"
    but with different X's is real — they may be in tension
    over scope). Differential = XOR(has_negation_A,
    has_negation_B); we treat ANY differential as a candidate.
  - **Correction (closed after re-read):** Actually the
    canonical signal is "one node prescribes; the other forbids"
    — so the pattern set should match ANY negation, and we
    require at least one of the pair to contain a negation
    pattern (more permissive than XOR). Tighten in v5.14.0+ if
    false-positive rate is bad.
- **Q: Cost-cap the LLM judge?**
  - A: Implicit via the deterministic candidate gate. On a
    12700-node corpus, expect <20 candidate pairs (most pairs
    don't pass the cosine + lexical filters). At 1k tokens
    each, that's ~$0.03 on Sonnet. Acceptable for an opt-in
    audit.
