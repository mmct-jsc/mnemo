# mnemo v5.16.0 — Understanding Phase 3: Domain Lenses (code lens + dead_code)

> **Spec doctrine (pipeline #21, installed v5.12.0):** This doc
> follows the DoD-first template. Sections in order: Summary / Why /
> Spec / Definition of Done / Anti-goals / Scope / Comparison / Build
> sequence / Open questions resolved.

## 1. One-line summary

Establish the **pluggable domain-lens mechanism** for the knowledge
auditor and ship the first lens — **`code`** — with one detector,
**`dead_code`** (private, uncalled functions/methods). A `lens=`
parameter selects a domain-specific detector suite that replaces the
domain-agnostic five; `dead_code` uses the existing `calls` edge
graph + an opt-in LLM judge for precision. The 27-tool MCP surface
stays count-stable (`mnemo_analyze` gains one optional
backward-compatible param).

## 2. Why this matters

Phase 1 + 2 (v5.12.0 → v5.15.0) shipped five **domain-agnostic**
detectors (stale / duplicates / orphan_references / contradictions /
semantic_orphans) + the refactor_actions enrichment. They work on
any corpus — memory, law, research notes, code.

But the user's vision is explicit that mnemo should understand a
**codebase** and "suggest if it needs refactoring because it's a
mess". Domain-agnostic detectors can't see code-specific structural
rot: a function nobody calls, a module import cycle, a route with no
handler. Phase 3 adds **domain lenses** — pluggable suites of
domain-specific detectors.

mnemo's own corpus is ~75% code nodes (9 289 callables, 3 645
modules, 5 258 resolved `calls` edges as of this design). The code
lens is therefore **immediately dogfoodable**: we run it against
mnemo's own source and find (or rule out) real dead code.

`dead_code` is the documented first code-lens capability (the
SKILL.md future-lenses note already names "dead-code detection
across modules").

## 3. Spec

### 3.1 The lens mechanism

- `analyze(..., lens: str | None = None)`.
- A registry: `LENS_DETECTORS: dict[str, tuple[str, ...]]`, e.g.
  `{"code": ("dead_code",)}`. `KNOWN_LENSES = tuple(LENS_DETECTORS)`.
- **A lens REPLACES the agnostic suite** (it does not add to it):
  - `lens is None` (default): the five agnostic detectors run,
    filtered by `types` — unchanged behaviour.
  - `lens="code"`: only the code-lens detectors run, filtered by
    `types` within the lens suite.
  - Rationale: a lens is a *focused domain audit*. Running the
    agnostic detectors on a code corpus floods (semantic_orphans
    alone emitted 24 776 candidates on this corpus); mixing suites
    would bury the code findings. Mutual exclusivity keeps each
    audit legible. A future release may add suite composition if a
    real need appears (YAGNI now).
- **Unknown lens is permissive** (matches the existing `types`
  contract): an unrecognized lens runs no detectors and returns an
  empty findings list. `KNOWN_LENSES` is exported so callers +
  the tool description can advertise valid values.

### 3.2 The `dead_code` detector

For each `code_function` / `code_method` node, a candidate is one
that is:

1. **Private**: `name` starts with `_` but is not a dunder
   (`__x__`). Private symbols are only reachable within their own
   module, where the Tier-2 call resolver is high-confidence (0.95
   within-file per the store comment). A private symbol with zero
   resolved inbound calls is a strong dead signal — far stronger
   than a public symbol, which may be called cross-file / externally
   / dynamically where resolution is sparse.
2. **Uncalled**: has **zero inbound `calls` edges** (no edge with
   `relation="calls"` and `dst_id == node.id`).
3. **Not a test entry point**: `name` does not start with `test_`
   and `source_path` does not contain a `tests/` (or `\tests\`)
   segment.

Candidates ship with severity `candidate`. Why only private: on the
live corpus, *any* uncalled callable = 6 367 candidates (68 % — the
call graph is best-effort, so most "uncalled" are really
resolution misses). Restricting to **private** uncalled = **135**
candidates — tractable + far higher precision (within-file
resolution catches real private uses).

### 3.3 Opt-in LLM judge

The remaining deterministic false positives are private functions
reached by patterns the static graph misses — dispatch tables
(`_extract_python` selected by language key), `getattr`, decorators,
registration callbacks. The opt-in judge filters them.

When `MNEMO_ANALYZE_LLM_JUDGE=1` + `ANTHROPIC_API_KEY` are set +
`anthropic` is importable, each candidate's function body + name +
path go to Claude (`LLMDeadCodeJudge`), which returns
`{"is_dead": true|false, "rationale": "..."}`. Confirmed dead →
severity `high`; not-dead (dynamically dispatched / entry point /
public-API-by-convention) → dropped. Parse/network errors degrade
to keeping the deterministic `candidate`. Reuses the shared
`MNEMO_ANALYZE_LLM_JUDGE` flag + `MNEMO_ANALYZE_JUDGE_MODEL`
override (default `claude-sonnet-4-6`).

### 3.4 HTTP / MCP / Schema / UI / Skill

- `AnalyzeIn` gains `lens: str | None = None`.
- `server.py` `/v1/analyze` passes `lens` through.
- `mnemo_analyze` MCP tool gains an optional `lens` param +
  description (count stays 27; optional + backward-compatible).
- `/analyze` UI: a short note that a `code` lens exists + renders
  `dead_code` findings (the `concept`/`action` columns already
  added in v5.14/v5.15 carry the function identifier in the
  description). No layout change beyond what already exists.
- `mnemo-knowledge-auditor` SKILL.md gains a "Domain lenses"
  section: when to pass `lens="code"`, the `dead_code` semantics,
  and the proposed-action mapping (`delete` for confirmed dead, or
  add a `# noqa: used-via-dispatch` style note if it's a false
  positive worth annotating).

### 3.5 Tests

- `tests/unit/test_dead_code_detector.py` — private-uncalled
  candidate gate; dunder / test_ / public / tests-path exclusions;
  inbound-`calls` lookup (a called private fn is NOT a candidate);
  severity `candidate` default; orchestrator via `lens="code"`.
- `tests/unit/test_dead_code_judge.py` — env gate, mocked client
  (is_dead true/false), graceful degradation, rationale_log.
- `tests/unit/test_lens_mechanism.py` — `KNOWN_LENSES` surface;
  `analyze(lens="code")` runs only the code suite; `lens=None`
  runs the agnostic five (unchanged); unknown lens → empty;
  `types` filters within a lens.
- Extended: `test_analyze_endpoint.py`, `test_mnemo_analyze_mcp_tool.py`,
  `test_analyze_ui_page.py`, `test_knowledge_auditor_skill.py`.
- `tests/unit/_snapshots/mcp_tool_list.json` — regen.

Target: +18-22 tests; daemon suite 1584 → ~1605.

## 4. Definition of Done

- [ ] Design doc (this file) with Spec / DoD / Anti-goals / Scope /
  Comparison (pipeline #21).
- [ ] `LENS_DETECTORS` registry + `KNOWN_LENSES` in `analyzer.py`.
- [ ] `detect_dead_code(store, *, judge=None)` — private + uncalled
  + not-test candidate gate; severity `candidate`, `high` when the
  judge confirms.
- [ ] `LLMDeadCodeJudge` + `dead_code_judge_from_env()` reusing the
  shared env contract; graceful on every error path.
- [ ] `analyze(lens=None)` wiring: lens replaces the agnostic suite;
  `types` filters within; unknown lens → empty findings.
- [ ] `AnalyzeIn.lens`; `/v1/analyze` passes it; `mnemo_analyze`
  optional `lens` param; wire snapshot regenerated; count stays 27.
- [ ] `/analyze` UI + SKILL.md updated.
- [ ] 18+ new tests pass; full daemon suite green (no regressions on
  the 1584).
- [ ] Ruff clean. CI 9/9 green.
- [ ] **Interactive dogfood**: `POST /v1/analyze {"lens":"code"}`
  against the live corpus returns `dead_code` candidates (~135
  private-uncalled) in well-formed shape; `lens=None` default
  returns the agnostic findings unchanged; unknown lens → empty.
- [ ] MEMORY.md promoted to v5.16.0 canonical; v5.15.0 demoted.
- [ ] Tag `v5.16.0` published on `public`.

## 5. Anti-goals (preserved + new)

- **NEVER auto-apply.** A `dead_code` finding (even LLM-confirmed)
  is a proposal; the user deletes. No mutating call path.
- **No raw-uncalled flood.** The detector is restricted to PRIVATE
  uncalled callables by design — shipping the 6 367-candidate raw
  signal would be a low-precision embarrassment on the flagship
  dogfood. Precision over recall for the first code detector.
- **Lens replaces, not adds.** No agnostic-on-code flood.
- **No new MCP tool.** Count stays 27; one optional
  backward-compatible param (`lens`) added to `mnemo_analyze`.
- **No new detector in `KNOWN_DETECTOR_TYPES`.** `dead_code` lives
  in `LENS_DETECTORS["code"]`, not the agnostic registry. The
  agnostic five are unchanged byte-for-byte.
- **No second code detector this release.** `cyclic_imports` and
  `orphan_modules` are deferred — the live corpus has 0 import
  cycles (clean architecture), so `cyclic_imports` is correct but
  undemonstrable; ship it when there's a corpus that exercises it.
- **No other lenses.** `vietnamese-law` / `research-notes` are
  later releases. The mechanism this release makes them cheap.
- **No new daemon dependencies.** `anthropic` already a dep.

## 6. Scope — files touched + boundary

### In-scope (must change):

- `daemon/mnemo/analyzer.py` — ADD `LENS_DETECTORS`, `KNOWN_LENSES`,
  `detect_dead_code`, `LLMDeadCodeJudge`, `dead_code_judge_from_env`,
  dead-code constants; wire `lens` into `analyze()`.
- `daemon/mnemo/api_schemas.py` — `AnalyzeIn.lens`.
- `daemon/mnemo/server.py` — pass `lens` through the route.
- `daemon/mnemo/agent_tools.py` — `mnemo_analyze` `lens` param +
  description.
- `daemon/mnemo/ui/templates/analyze.html` — lens note.
- `skills/mnemo-knowledge-auditor/SKILL.md` — Domain lenses section.
- 3 new + 4 extended test files.
- `tests/unit/_snapshots/mcp_tool_list.json` — regen.
- `CHANGELOG.md` — `[5.16.0]`.
- version bump 5.15.0 → 5.16.0 (4 files).

### Out-of-scope (do NOT touch this release):

- The 5 agnostic detectors + refactor_actions — unchanged
  byte-for-byte.
- The edge store / call-graph resolver — read-only here.
- Retrieval / reindex / chat — untouched.
- Any mutating-primitive path — read-only.

## 7. Comparison

### 7.1 Pre-v5.16.0 baseline

The auditor sees a code corpus as generic text. Asked "is there dead
code?", it has no answer — the five agnostic detectors don't model
call graphs. A user hunting dead code reads every module by hand or
wires up a separate tool (vulture, ts-prune) outside mnemo.

### 7.2 Post-v5.16.0 target

`mnemo analyze --lens code` (or `mnemo_analyze(lens="code")`)
returns private uncalled callables in seconds, using the call graph
mnemo already built. With the LLM judge on, the list narrows to
genuinely-dead functions. The same auditor surface now does
code-structural analysis — no second tool.

### 7.3 Specific measurable claims

| Metric | Baseline (v5.15.0) | Target (v5.16.0) |
|---|---|---|
| Dead-code detection | none (no code lens) | private-uncalled in < 5s on 9 289 callables |
| Raw-uncalled candidates | n/a | 6 367 (NOT shipped — too noisy) |
| Private-uncalled candidates (shipped gate) | n/a | ~135 (tractable + reviewable) |
| Lenses available | 0 | 1 (`code`) |
| MCP tool count | 27 | 27 |
| `mnemo_analyze` signature | (types, project_key, propose_actions) | + optional `lens` |
| Daemon test suite | 1584/2skip | 1605+/2skip |
| LLM cost (judged dead_code) | n/a | ≤ $0.25 (135 × ~1.5k tok on Sonnet) |

### 7.4 Failure-mode comparison

Pre: code rot (functions that lost their last caller in a refactor)
accumulates invisibly. Post: the code lens surfaces private
orphans on demand; the user prunes them. The mechanism also makes
every future code/law/research detector a small addition to a
registry rather than a new architecture.

## 8. Build sequence (TDD)

1. **RED — dead_code candidate gate.** `test_dead_code_detector.py`:
   private-uncalled candidate, dunder/test/public/tests-path
   exclusions, a called private fn is not a candidate. Run → red.
2. **GREEN — `detect_dead_code`** (deterministic path) in
   `analyzer.py`. Run → green.
3. **RED — lens mechanism.** `test_lens_mechanism.py`: KNOWN_LENSES,
   `analyze(lens="code")` runs only dead_code, `lens=None` runs
   agnostic five, unknown lens → empty, types within a lens. Run →
   red.
4. **GREEN — `LENS_DETECTORS` + `analyze(lens=)` wiring**. Run →
   green.
5. **RED — LLM judge.** `test_dead_code_judge.py`: env gate, mocked
   client, graceful, rationale_log. Run → red.
6. **GREEN — `LLMDeadCodeJudge` + `dead_code_judge_from_env`** +
   wire into `detect_dead_code` + orchestrator. Run → green.
7. **RED — endpoint + MCP + UI + skill.** Extend the four. Run →
   red.
8. **GREEN — schema `lens` + route + tool param + UI note + SKILL**.
   Run → green.
9. **MCP wire snapshot regen.**
10. **Full pytest** + ruff. Confirm 1605+/2skip + clean.
11. **Live dogfood** — `lens=code` on the running daemon.
12. **Version bump + CHANGELOG** + ship.
13. **Post-merge daemon restart + reindex + handover.**

## 9. Open questions resolved

- **Q: dead_code or cyclic_imports as the first code detector?**
  - A: dead_code (private-refined). The live corpus has **0 import
    cycles** — `cyclic_imports` would be correct but show nothing,
    a weak first demo. dead_code yields 135 reviewable candidates +
    exercises the full candidate→judge pattern. cyclic_imports is
    deferred to a corpus that exercises it.

- **Q: Why private-only? Isn't that under-inclusive?**
  - A: Precision over recall for the first detector. Raw uncalled =
    6 367 (68 %, mostly resolution misses on the best-effort call
    graph). Private uncalled = 135 (within-file resolution is
    high-confidence, so a private orphan is a real signal). Public
    dead code needs cross-file/external/dynamic resolution we don't
    have; flagging it would flood. A later release can widen with a
    stronger call graph.

- **Q: Lens replaces or augments the agnostic suite?**
  - A: Replaces. Running agnostic detectors on a code corpus floods
    (24 776 semantic_orphans). A lens is a focused audit; mixing
    suites buries the signal. Mutual exclusivity is the legible
    default; composition is YAGNI until asked for.

- **Q: Unknown lens — raise or ignore?**
  - A: Ignore (empty findings), matching the existing permissive
    `types` contract. `KNOWN_LENSES` is exported so callers can
    validate + the tool advertises valid values.

- **Q: Fourth LLM helper class — refactor to a base?**
  - A: Not this release. Four siblings (Contradiction/Orphan judges,
    Refactor proposer, DeadCode judge) now share the same shape
    (client/model/max_tokens/rationale_log + graceful degradation).
    Lesson #109 favored siblings over a parameterized abstraction;
    that still holds. A tiny `_LLMHelperBase` mixin is a tempting
    *internal* refactor but risks the byte-stable surface — note it
    for a dedicated refactor pass, don't bundle it into a feature
    release.

- **Q: Cost-cap the judge?**
  - A: Implicit via the 135-candidate private gate. At ~1.5k tok
    each on Sonnet that's ~$0.25 — acceptable for an opt-in audit.
    If a corpus produces thousands of private orphans, the same
    `DEFAULT_MAX_REFACTOR_ACTIONS`-style cap pattern can be added;
    not needed at 135.
