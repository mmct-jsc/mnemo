# mnemo v5.17.0 — Understanding Phase 3b: god_object (code lens)

> **Spec doctrine (pipeline #21, installed v5.12.0):** DoD-first
> template. Summary / Why / Spec / DoD / Anti-goals / Scope /
> Comparison / Build sequence / Open questions resolved.

## 1. One-line summary

Add a second detector to the **`code`** lens: **`god_object`** —
classes with too many methods and modules with too many top-level
definitions, surfaced by exact edge counts (not the sparse call
graph). Deterministic, count-based, no LLM judge. Registered as
`LENS_DETECTORS["code"] = ("dead_code", "god_object")`. The 27-tool
MCP surface stays byte-stable (only `mnemo_analyze`'s description
changes).

## 2. Why this matters

v5.16.0 shipped the code lens with `dead_code`. `dead_code` (and the
deferred `cyclic_imports` / `orphan_modules`) lean on the
best-effort `calls` / `imports` graph, which is sparse — so they're
high-recall / candidate-precision and lean on an LLM judge.

`god_object` is different: it counts `method_of` and `defines`
edges, which are **Tier-1 structural edges** built directly from the
AST — they're complete, not best-effort. A class's method count is
exact. So `god_object` is **precise without an LLM judge**, and it
directly answers the user's vision verbatim: "suggest if it needs
refactoring because it's a mess". A 92-method service class IS the
canonical mess.

It's also immediately dogfoodable: mnemo's own `Store` (80 methods)
and `api_schemas.py` (58 top-level definitions) are real
candidates — the auditor flags its own sprawl.

## 3. Spec

### 3.1 god class

A `code_class` node `C` is a candidate when the number of
`method_of` edges with `dst_id == C.id` exceeds
`GOD_CLASS_METHOD_THRESHOLD` (**25**). (`method_of`: src=method,
dst=class — so inbound `method_of` count = the class's method
count.)

Threshold rationale (from a live-corpus probe of 902 classes):
mean 5.4, p90 11, max 92. `> 25` flags 18 classes — the top ~2%,
well clear of p90. Genuine outliers (`AlertsService` 92,
`ReportsService` 82, `Store` 80, …), not the body of the
distribution.

### 3.2 god module

A `code_module` node `M` is a candidate when the number of
`defines` edges with `src_id == M.id` exceeds
`GOD_MODULE_DEFINES_THRESHOLD` (**30**), EXCLUDING test files
(`_is_test_symbol` on name/path — test modules naturally define many
test functions and are not "god modules").

Threshold rationale (1795 modules): mean 3.2, p90 8, max 75.
`> 30` flags 12 raw / ~8 after test exclusion — real large modules
(`api_schemas.py` 58, `capture_backends.py` 51, `code.py` 45,
`agent_tools.py` 35, …).

### 3.3 finding shape

```
{"type": "god_object", "node_ids": [id], "severity": "candidate",
 "symbol": "<class/module name>",
 "description": "Class 'X' (path) defines N methods (> 25); consider
                 splitting responsibilities." }
```

Severity `candidate` — a high count is a real smell but not a
certain defect (a cohesive facade may be acceptable; the user
judges). `symbol` reuses the field added in v5.16.0 (HTTP/MCP
parity); the count lives in the description.

### 3.4 No LLM judge (this release)

`god_object` ships deterministic-only. A future opt-in judge could
distinguish "cohesive facade" from "grab-bag", but that would add a
5th sibling LLM-helper class — and the v5.16.0 handover flagged that
the 4 existing siblings should be consolidated into a
`_LLMHelperBase` (a dedicated refactor pass) BEFORE a 5th is added.
So: ship `god_object` count-only now; the refactor + an optional
cohesion judge come later, in that order. Keeping it deterministic
also keeps the detector precise + free.

### 3.5 Surface

- `LENS_DETECTORS["code"]` gains `"god_object"`. `KNOWN_LENSES`
  unchanged (still just `code`). `KNOWN_DETECTOR_TYPES` unchanged
  (god_object is a lens detector, not agnostic).
- `analyze()` dispatch gains `if "god_object" in requested`.
- `mnemo_analyze` description mentions god_object in the code lens;
  param signature unchanged; wire snapshot regenerated.
- `/analyze` UI + `mnemo-knowledge-auditor` SKILL.md mention
  god_object + the propose-action mapping (split the class/module).

### 3.6 Tests

- `tests/unit/test_god_object_detector.py` — god class > / <=
  threshold; god module > threshold; test-module excluded;
  non-code ignored; severity candidate; symbol + count in
  description; both code_class + code_module scanned; via
  `lens="code"`.
- Extend `test_lens_mechanism.py` — the code suite now has TWO
  detectors; `types=["god_object"]` filters to it; `types=["dead_code"]`
  excludes it.
- `tests/unit/_snapshots/mcp_tool_list.json` — regen.

Target: +12-15 tests; daemon suite 1615 → ~1630.

## 4. Definition of Done

- [ ] Design doc (this file).
- [ ] `GOD_CLASS_METHOD_THRESHOLD = 25`,
  `GOD_MODULE_DEFINES_THRESHOLD = 30` constants.
- [ ] `detect_god_object(store)` — exact `method_of` per-class +
  `defines` per-module counts; test-module exclusion; severity
  `candidate`; symbol + count.
- [ ] `LENS_DETECTORS["code"] = ("dead_code", "god_object")`;
  `analyze()` dispatches it under `lens="code"`.
- [ ] `KNOWN_DETECTOR_TYPES` unchanged (5); agnostic detectors +
  dead_code unchanged byte-for-byte.
- [ ] 12+ new tests pass; full suite green (no regressions on 1615).
- [ ] Ruff clean. CI 9/9 green.
- [ ] **Live dogfood**: `lens=code, types=["god_object"]` surfaces
  mnemo's own `Store` (80) + `api_schemas.py` (58) etc.; `dead_code`
  still works in the same lens; agnostic default unchanged.
- [ ] MEMORY.md promoted to v5.17.0; v5.16.0 demoted.
- [ ] Tag `v5.17.0` published on `public`.

## 5. Anti-goals (preserved + new)

- **NEVER auto-apply.** A god_object finding is a proposal; the user
  refactors. No mutating path.
- **No LLM judge this release** (keeps it precise + free; avoids a
  5th sibling class before the consolidation refactor).
- **No threshold magic.** Fixed, documented thresholds (25 / 30)
  derived from a corpus probe — not a runtime statistical fit that
  varies per audit.
- **No new lens, no new MCP tool** (count stays 27), no new agnostic
  detector (`KNOWN_DETECTOR_TYPES` stays 5).
- **Test modules excluded** from god_module (they legitimately
  define many test functions).
- **No new daemon dependencies.**

## 6. Scope

### In-scope:
- `daemon/mnemo/analyzer.py` — constants + `detect_god_object` +
  `LENS_DETECTORS` entry + `analyze()` dispatch.
- `daemon/mnemo/agent_tools.py` — `mnemo_analyze` description.
- `daemon/mnemo/ui/templates/analyze.html` — mention god_object.
- `skills/mnemo-knowledge-auditor/SKILL.md` — code-lens section.
- `tests/unit/test_god_object_detector.py` (new) + extend
  `test_lens_mechanism.py`.
- `tests/unit/_snapshots/mcp_tool_list.json` — regen.
- `CHANGELOG.md` + version bump 5.16.0 → 5.17.0 (4 files).

### Out-of-scope:
- `dead_code` + the 5 agnostic detectors + refactor_actions —
  unchanged byte-for-byte.
- `cyclic_imports` / `orphan_modules` — deferred (the corpus has 0
  cycles; orphan_modules has the sparse-graph problem).
- The `_LLMHelperBase` refactor — separate dedicated pass.
- Any cohesion LLM judge for god_object — deferred until after the
  refactor.

## 7. Comparison

### 7.1 Pre-v5.17.0
The code lens finds dead (private uncalled) functions but is blind
to oversized classes/modules. A user hunting refactoring targets
("what's bloated?") gets no help from mnemo.

### 7.2 Post-v5.17.0
`mnemo_analyze(lens="code", types=["god_object"])` returns the
corpus's largest classes + modules by exact structural count, in
milliseconds, ranked-worthy for a split.

### 7.3 Measurable claims

| Metric | Baseline (v5.16.0) | Target (v5.17.0) |
|---|---|---|
| Oversized-class detection | none | exact, < 2s on 902 classes |
| god class candidates (corpus, >25) | n/a | 18 |
| god module candidates (corpus, >30, ex-test) | n/a | ~8 |
| Precision | n/a | exact counts (no sparse-graph FP) |
| code-lens detectors | 1 (dead_code) | 2 (+god_object) |
| New LLM judge classes | n/a | 0 (deterministic) |
| MCP tool count | 27 | 27 |
| Daemon suite | 1615/2skip | 1630+/2skip |

### 7.4 Failure-mode comparison
Pre: classes accrete methods release over release; nobody notices
until the file is unmaintainable. Post: the auditor names the top
sprawl on demand — `Store` (80), `AlertsService` (92) — so the split
happens deliberately.

## 8. Build sequence (TDD)

1. **RED** — `test_god_object_detector.py`: god class > threshold
   flagged; <= not; god module flagged; test-module excluded;
   non-code ignored; severity/symbol/count. Run → red.
2. **GREEN** — `detect_god_object` + constants. Run → green.
3. **RED** — extend `test_lens_mechanism.py`: code suite has two
   detectors; `types` filters each. Run → red.
4. **GREEN** — register in `LENS_DETECTORS["code"]` +
   `analyze()` dispatch. Run → green.
5. Surfaces (MCP desc / UI / SKILL) + regen wire snapshot.
6. Full pytest + ruff. 1630+/2skip + clean.
7. Live dogfood `lens=code, types=["god_object"]`.
8. Version bump + CHANGELOG + ship.
9. Post-merge restart + reindex + handover.

## 9. Open questions resolved

- **Q: LLM judge for cohesion?**
  - A: Not this release. It would add a 5th sibling LLM-helper class
    before the flagged `_LLMHelperBase` consolidation. Order:
    refactor first, then judge-bearing detectors. god_object is
    precise enough deterministic (exact counts) to ship judge-free.

- **Q: Fixed thresholds vs statistical (mean+2σ) outliers?**
  - A: Fixed (25 / 30), documented, probe-derived. A per-audit
    statistical fit makes the same class flagged-or-not depending on
    what else is in the corpus — unpredictable + non-reproducible.
    A fixed threshold is a stable contract; tune it in a later
    release if real corpora disagree.

- **Q: Include test modules in god_module?**
  - A: No. A test file with 60 `test_*` functions is not a god
    module — that's normal. Reuse `_is_test_symbol` (name/path) to
    exclude, same as `dead_code`. (god_CLASS doesn't need the
    exclusion — test files rarely define 25-method classes.)

- **Q: Severity — candidate, medium, or high?**
  - A: `candidate`. A high method count is a real smell but the
    user judges whether it's a cohesive facade or a grab-bag. Matches
    the lens-detector convention (dead_code is also `candidate`).

- **Q: `>` or `>=` threshold?**
  - A: Strict `>` (so threshold 25 means ≥26). Matches the probe
    counts (`> 25` → 18 classes).
