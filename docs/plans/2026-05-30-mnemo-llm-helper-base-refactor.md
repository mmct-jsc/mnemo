# mnemo v5.17.1 — `_LLMHelper` base refactor (analyzer LLM helpers)

> **Spec doctrine (pipeline #21):** DoD-first. This is a PURE
> internal refactor — zero public behaviour change — so the DoD is
> defined by behaviour preservation, not new capability.

## 1. One-line summary

Consolidate the four sibling LLM helper classes in
`daemon/mnemo/analyzer.py` — `LLMContradictionJudge` (v5.13.0),
`LLMSemanticOrphanJudge` (v5.14.0), `LLMRefactorProposer` (v5.15.0),
`LLMDeadCodeJudge` (v5.16.0) — onto a shared `_LLMHelper` base that
owns the create→parse→graceful-degradation routine. Each subclass
keeps its own prompt, field interpretation, and `rationale_log`
schema. Byte-identical behaviour; all four judge test files stay
green. **Patch release (5.17.1)** — no public API/behaviour change.

## 2. Why this matters

Lesson #109 (v5.15.0) chose sibling classes over a parameterized
abstraction — correct at 2-3 copies. We are now at FOUR, and the
v5.16.0 + v5.17.0 handovers both flagged the consolidation as the
next dedicated pass, explicitly "BEFORE adding a 5th judge" (the
god_object cohesion judge + future detector judges are queued).

The four classes share, verbatim:
- dataclass fields `client` / `model="claude-sonnet-4-6"` /
  `max_tokens` / `rationale_log: list = field(default_factory=list)`;
- a method that calls `client.messages.create(model, max_tokens,
  system, messages=[{role:user, content:user}])`, reads
  `response.content[0].text`, `json.loads` it, and on any
  parse/structure error logs + records a degraded entry + returns a
  safe default, and on any other exception does the same with a
  CLIENT_ERROR marker.

That's ~25 lines of identical control flow × 4 = ~100 duplicated
lines. It's the exact "duplicates" smell mnemo's own auditor would
flag. Consolidating makes the 5th judge ~15 lines instead of ~50 and
removes the risk of the four copies drifting (they already diverged:
two catch a 4-tuple of exceptions, two catch a 5-tuple incl.
`TypeError`).

## 3. Spec

### 3.1 The base

```python
@dataclass
class _LLMHelper:
    client: Any
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 512
    rationale_log: list[dict[str, Any]] = field(default_factory=list)

    def _invoke_json(
        self, *, system: str, user: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Call the model + parse its JSON reply. Returns
        (parsed_dict, None) on success, or (None, error_marker) on any
        parse/network error. NEVER raises -- the caller maps a None
        result to its own safe default + rationale_log entry."""
```

`_invoke_json` catches the SUPERSET of the four classes' exception
tuples: `(json.JSONDecodeError, KeyError, AttributeError,
IndexError, TypeError)` → `PARSE_ERROR`; any other `Exception` →
`CLIENT_ERROR`. (See §9 for why the superset is behaviour-preserving
for the two classes that previously caught only the 4-tuple.)

### 3.2 The subclasses

Each becomes a `@dataclass(...)` subclass of `_LLMHelper`. Subclasses
that need a non-default `max_tokens` redeclare just that field
(`LLMRefactorProposer` → 700, `LLMDeadCodeJudge` → 400; the two
judges keep the base default 512). Each public method
(`.judge(...)` / `.propose(...)`) keeps its exact signature, builds
its `user` message, calls `self._invoke_json(system=..., user=...)`,
then:
- on `parsed is None`: append the SAME degraded `rationale_log`
  entry it appended before (its own keys + `parsed_ok=False` +
  `rationale=<error_marker>`) and return its SAME safe default
  (`False` / `_empty_action(marker)`).
- on success: interpret the parsed dict exactly as before, append
  its SAME success `rationale_log` entry, return its SAME value.

### 3.3 What does NOT change

- Public class names, method names + signatures, return types.
- The `*_from_env()` factories (untouched).
- The `rationale_log` entry SCHEMAS (per-class keys preserved).
- The success-path return values + the error-path safe defaults.
- The MCP / HTTP / UI / SKILL surfaces — **nothing** outside
  `analyzer.py` is touched. No wire-snapshot change.

### 3.4 Tests

- `tests/unit/test_llm_helper_base.py` (NEW) — `_invoke_json`
  returns `(dict, None)` on valid JSON; `(None, "PARSE_ERROR…")` on
  invalid JSON; `(None, "CLIENT_ERROR…")` when the client raises.
- The four existing judge/proposer test files
  (`test_contradictions_judge.py`, `test_semantic_orphans_judge.py`,
  `test_refactor_actions.py`, `test_dead_code_judge.py`) are the
  regression net — they MUST pass unchanged.

## 4. Definition of Done

- [ ] Design doc (this file).
- [ ] `_LLMHelper` base with `_invoke_json` in `analyzer.py`.
- [ ] All four classes inherit `_LLMHelper` + delegate the
  network/parse routine; their prompts / interpretation /
  rationale_log schemas / return values unchanged.
- [ ] `test_llm_helper_base.py` (3+ tests) passes.
- [ ] The four existing judge test files pass UNCHANGED (not a line
  edited in them).
- [ ] Net line reduction in `analyzer.py` (~80-100 lines).
- [ ] Full daemon suite green (no regressions on 1629 + the base
  test). Ruff clean. CI 9/9.
- [ ] No change to `tests/unit/_snapshots/mcp_tool_list.json`
  (pure internal — if it changes, something leaked).
- [ ] Version bump 5.17.0 → **5.17.1** (patch). CHANGELOG entry.
- [ ] MEMORY.md promoted to v5.17.1; v5.17.0 demoted.
- [ ] Tag `v5.17.1` published on `public`.

## 5. Anti-goals

- **No behaviour change.** Return values + error safe-defaults +
  rationale_log schemas are byte-identical. The only intentional
  difference is an untested degenerate path (a `TypeError` during
  parse in the two 4-tuple classes now logs `PARSE_ERROR` instead of
  `CLIENT_ERROR` — same return value; see §9).
- **No bundled feature.** The god_object cohesion judge + any new
  detector are SEPARATE later releases. A pure refactor keeps the
  diff reviewable and lets the existing tests prove preservation.
- **No editing the four judge test files.** They are the regression
  net; editing them would defeat the purpose.
- **No surface change** (MCP/HTTP/UI/SKILL/snapshot untouched).
- **No file split.** `analyzer.py` is large but splitting it is a
  separate, riskier refactor — out of scope.

## 6. Scope

### In-scope:
- `daemon/mnemo/analyzer.py` — add `_LLMHelper`; refactor the four
  subclasses.
- `tests/unit/test_llm_helper_base.py` (new).
- `CHANGELOG.md` + version bump (4 files) 5.17.0 → 5.17.1.

### Out-of-scope:
- The detectors, the orchestrator, the `*_from_env` factories — only
  the four class BODIES change (to delegate).
- Everything outside `analyzer.py`.

## 7. Comparison

| Metric | Before (5.17.0) | After (5.17.1) |
|---|---|---|
| LLM-helper classes | 4 (each ~45-55 lines) | 4 thin + 1 base |
| Duplicated create+parse+except blocks | 4 | 1 |
| Lines in the 4 classes (approx) | ~200 | ~110 |
| Cost to add the 5th judge | ~50 lines (copy) | ~15 lines (subclass + prompt) |
| Exception-tuple drift | 2×4-tuple, 2×5-tuple | 1 superset |
| Public behaviour | — | byte-identical (return values) |
| Daemon suite | 1629/2skip | 1632+/2skip (+base test) |

## 8. Build sequence (TDD)

1. **RED** — `test_llm_helper_base.py`: `_invoke_json` success /
   parse-error / client-error. Run → red (base missing).
2. **GREEN** — add `_LLMHelper` + `_invoke_json`. Run → green.
3. **Refactor LLMContradictionJudge** to inherit + delegate. Run
   `test_contradictions_judge.py` → green.
4. **Refactor LLMSemanticOrphanJudge.** Run
   `test_semantic_orphans_judge.py` → green.
5. **Refactor LLMRefactorProposer.** Run `test_refactor_actions.py`
   → green.
6. **Refactor LLMDeadCodeJudge.** Run `test_dead_code_judge.py` →
   green.
7. Full pytest + ruff. 1632+/2skip + clean. Confirm wire snapshot
   UNCHANGED.
8. Version bump + CHANGELOG (patch) + ship.
9. Post-merge restart + reindex + handover.

## 9. Open questions resolved

- **Q: Superset exception tuple — behaviour change?**
  - A: `LLMContradictionJudge` + `LLMSemanticOrphanJudge` caught a
    4-tuple (no `TypeError`); `LLMRefactorProposer` +
    `LLMDeadCodeJudge` caught a 5-tuple (with `TypeError`). The base
    uses the 5-tuple superset. The only path this changes: a
    `TypeError` raised during parse in the two 4-tuple classes
    previously fell to `except Exception` → `CLIENT_ERROR` marker;
    now it's caught as `PARSE_ERROR`. The RETURN VALUE is identical
    (the safe default either way); only the `rationale_log` marker
    string differs, in a degenerate path NO test exercises (and
    arguably `PARSE_ERROR` is the more correct label for a parse-time
    `TypeError`). Accepted as behaviour-preserving.

- **Q: Inheritance vs a shared free function?**
  - A: Inheritance via a `@dataclass` base — because the four
    classes ALSO duplicate the four fields, not just the method.
    A base dataclass dedupes both. The subclasses are a genuine
    "is-a LLM helper" family. (A free function would dedupe only the
    method body and leave the fields copied 4×.)

- **Q: Why patch (5.17.1), not minor?**
  - A: Zero public API/behaviour change → semver patch. Conventional
    commit `refactor:`. The project already ships patches for
    non-feature work (5.5.1, 5.8.1).

- **Q: Bundle the god_object cohesion judge to "prove" the base?**
  - A: No. The handover explicitly said dedicated pass. Bundling a
    feature would make the diff ambiguous (did a behaviour delta
    come from the refactor or the feature?) and could mask a
    regression. The 5th judge lands in a separate 5.18.0; THAT
    release is where the base's payoff shows.
