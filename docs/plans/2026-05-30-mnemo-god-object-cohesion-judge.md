# mnemo v5.18.0 — god_object cohesion judge (the _LLMHelper payoff)

> **Spec doctrine (pipeline #21):** DoD-first. Summary / Why / Spec /
> DoD / Anti-goals / Scope / Comparison / Build sequence / Open Qs.

## 1. One-line summary

Add an opt-in LLM **cohesion judge** to the `god_object` detector:
it sees a flagged class/module's member names and decides whether
the size reflects a **cohesive facade** (one clear responsibility →
drop) or a **grab-bag** that should be split (→ severity `high`).
Built as `LLMCohesionJudge(_LLMHelper)` — ~15 lines, the first
payoff of the v5.17.1 base refactor. Reuses the shared
`MNEMO_ANALYZE_LLM_JUDGE` opt-in; the deterministic path stays
byte-stable.

## 2. Why this matters

`god_object` (v5.17.0) is precise about COUNT but blind to
COHESION. On the live corpus it flags 26 candidates, but several are
legitimate facades: a `Store` with 80 storage methods, a NestJS
`*Service` that genuinely owns one domain. Flagging those as
problems is noise. The real refactoring targets are the grab-bags —
a class whose methods span unrelated responsibilities
(`parse_pdf` + `send_email` + `calculate_tax`).

A count threshold can't tell these apart; member NAMES can. This is
exactly the deterministic-candidate → LLM-judge pattern that
v5.13.0–v5.16.0 established four times. It's also the concrete
demonstration of the v5.17.1 `_LLMHelper` refactor: the 5th judge is
a tiny subclass.

## 3. Spec

### 3.1 The judge

```python
@dataclass
class LLMCohesionJudge(_LLMHelper):
    max_tokens: int = 400

    def judge(self, *, kind: str, name: str, members: list[str]) -> bool:
        # kind: "class" | "module"; members: method / definition names.
        # Returns True if it SHOULD be split (grab-bag); False if
        # cohesive (a facade with one responsibility) -- including on
        # every error path.
```

Prompt: given the `kind`, `name`, and member-name list, decide
whether the unit is a cohesive single-responsibility facade or a
grab-bag of unrelated responsibilities that should be split.
Respond `{"should_split": true|false, "rationale": "<one paragraph>"}`.
Member list capped (`_COHESION_MEMBERS_CAP = 80`) so a 92-method
class doesn't blow the prompt.

`god_object_judge_from_env()` mirrors the other factories: returns a
judge only when `MNEMO_ANALYZE_LLM_JUDGE` + `ANTHROPIC_API_KEY` +
`anthropic` are present (shared opt-in flag + `MNEMO_ANALYZE_JUDGE_MODEL`).

### 3.2 Detector integration

`detect_god_object(store, *, judge=None)`:

- Deterministic candidate gate UNCHANGED (god class > 25 `method_of`;
  god module > 30 `defines`, ex-test).
- When `judge` is provided, for each candidate collect its member
  names (a class's methods via `method_of` src nodes; a module's
  definitions via `defines` dst nodes), then call
  `judge.judge(kind=, name=, members=)`:
  - `should_split=True` → severity `high` (genuine god object).
  - `should_split=False` (cohesive) OR any error → candidate
    DROPPED (matches the established judge-authoritative-when-enabled
    semantics: contradictions/orphans/dead_code all drop on
    rejection/error).
- Without a judge: candidates ship `candidate` (UNCHANGED).

Member-name collection is targeted + only when the judge is enabled:
the count pass additionally groups member ids per owner; for each
candidate the member ids are resolved to names via `store.get_node`
(bounded — only the ~26 candidates' members).

### 3.3 Orchestrator

`analyze(..., god_object_judge=None)`: caller-provided > env-derived
(`god_object_judge_from_env`) > None. When `lens="code"` runs
`god_object`, the resolved judge is passed to `detect_god_object`.
Mirrors the `dead_code_judge` wiring exactly.

### 3.4 Surface

- `mnemo_analyze` description notes god_object can now be LLM-judged
  via the shared opt-in flag. **No new param** (env-gated, like
  dead_code). Wire snapshot regenerated for the description.
- `mnemo-knowledge-auditor` SKILL.md god_object section gains the
  cohesion-judge note (cohesive facade vs grab-bag; high vs
  candidate).
- No HTTP schema change (reuses the env opt-in; findings keep their
  shape, only severity may be `high` when judged).

### 3.5 Tests

- `tests/unit/test_cohesion_judge.py` — env gate; mocked client
  `should_split` true/false; graceful (parse/client error → False);
  rationale_log; member list passed through.
- Extend `tests/unit/test_god_object_detector.py` —
  `detect_god_object(judge=MagicMock(should_split=True))` → high;
  `should_split=False` → dropped; default (no judge) → candidate
  unchanged.
- `tests/unit/_snapshots/mcp_tool_list.json` — regen (description).

Target: +10-12 tests; daemon suite 1635 → ~1647.

## 4. Definition of Done

- [ ] Design doc (this file).
- [ ] `LLMCohesionJudge(_LLMHelper)` with `judge(kind, name, members)`
  + `_COHESION_MEMBERS_CAP`; graceful → False on every error path.
- [ ] `god_object_judge_from_env()` reuses the shared opt-in
  contract.
- [ ] `detect_god_object(store, judge=None)` — member-name collection
  + escalation; `should_split` → high, cohesive/error → dropped;
  no-judge path byte-identical (candidate).
- [ ] `analyze(god_object_judge=None)` wiring (caller > env > None).
- [ ] 10+ new/extended tests pass; full suite green (no regression
  on 1635).
- [ ] Ruff clean. CI 9/9.
- [ ] **Live**: `lens=code, types=["god_object"]` with no env flag
  returns the SAME 26 candidates (byte-stable, judge off). The
  judged path is mock-tested (no API key in the autonomous session).
- [ ] MCP signature unchanged (no new param); wire snapshot regen
  for the description only.
- [ ] MEMORY.md promoted to v5.18.0; v5.17.1 demoted.
- [ ] Tag `v5.18.0` published on `public`.

## 5. Anti-goals

- **No new MCP param** — the judge is env-gated (`MNEMO_ANALYZE_LLM_JUDGE`),
  exactly like dead_code's judge. 27-tool surface name/signature
  unchanged.
- **Deterministic path unchanged** — without the opt-in flag,
  god_object behaves byte-identically to v5.17.0 (26 candidates).
- **No new env flag** — reuses the shared `MNEMO_ANALYZE_LLM_JUDGE`
  (+ `MNEMO_ANALYZE_JUDGE_MODEL`).
- **No threshold change** — the count gate (25 / 30) is unchanged;
  the judge only RE-GRADES the candidates it produces.
- **NEVER auto-apply** — a high god_object finding is still a
  proposal; the user splits.
- **No new daemon dependencies.**

## 6. Scope

### In-scope:
- `daemon/mnemo/analyzer.py` — `LLMCohesionJudge`,
  `god_object_judge_from_env`, `detect_god_object(judge=)`,
  `analyze(god_object_judge=)`, `_COHESION_*` constants + prompt.
- `daemon/mnemo/agent_tools.py` — `mnemo_analyze` description.
- `skills/mnemo-knowledge-auditor/SKILL.md` — god_object note.
- `tests/unit/test_cohesion_judge.py` (new) + extend
  `test_god_object_detector.py`.
- `tests/unit/_snapshots/mcp_tool_list.json` — regen.
- `CHANGELOG.md` + version bump 5.17.1 → 5.18.0 (4 files).

### Out-of-scope:
- The other 4 judges + the detectors' deterministic gates —
  unchanged.
- dead_code / the agnostic five — untouched.
- HTTP/UI layout — only the SKILL + tool description change.

## 7. Comparison

| Metric | Before (5.17.1) | After (5.18.0) |
|---|---|---|
| god_object precision | count-only (facades + grab-bags both `candidate`) | LLM splits cohesive (dropped) from grab-bag (`high`) |
| LLM judge classes | 4 | 5 (LLMCohesionJudge) |
| Cost to add the 5th judge | (refactor done) | ~15 lines — the `_LLMHelper` payoff realized |
| New env flag / MCP param | — | none (reuses shared opt-in) |
| Deterministic path | 26 candidates | 26 candidates (byte-stable) |
| Daemon suite | 1635/2skip | 1647+/2skip |

## 8. Build sequence (TDD)

1. **RED** — `test_cohesion_judge.py`: env gate, mocked
   should_split true/false, graceful, rationale_log. Run → red.
2. **GREEN** — `LLMCohesionJudge(_LLMHelper)` +
   `god_object_judge_from_env`. Run → green.
3. **RED** — extend `test_god_object_detector.py`: judge=MagicMock
   should_split=True → high; False → dropped; no-judge → candidate.
   Run → red.
4. **GREEN** — `detect_god_object(judge=)` member collection +
   escalation; `analyze(god_object_judge=)` wiring. Run → green.
5. Surfaces (MCP desc / SKILL) + regen wire snapshot.
6. Full pytest + ruff. 1647+/2skip + clean.
7. Live: deterministic god_object byte-stable (judge off).
8. Version bump + CHANGELOG + ship.
9. Post-merge restart + reindex + handover.

## 9. Open questions resolved

- **Q: Cohesive/error → drop or keep-as-candidate?**
  - A: Drop, matching the established judge-authoritative-when-enabled
    semantics (contradictions/orphans/dead_code all drop on
    rejection AND on error → safe default False). Consistency over a
    bespoke keep-on-error rule. Documented.

- **Q: What does the judge see — counts or member names?**
  - A: Member names (capped at 80). A count can't reveal cohesion;
    the method/definition names cluster (or don't) around a
    responsibility. `get_node` per member, only for the ~26
    candidates, only when the judge is enabled — bounded cost.

- **Q: New MCP param for the judge?**
  - A: No. Env-gated via the shared `MNEMO_ANALYZE_LLM_JUDGE`, like
    dead_code. The detector resolves the judge from env inside
    `analyze`. Signature + tool count unchanged.

- **Q: Why `should_split` semantics (vs `is_cohesive`)?**
  - A: Align the "confirmed → high" direction with the other
    judges: the judge CONFIRMS the problem (should_split=True →
    escalate). `is_cohesive` would invert the direction and risk a
    confusing double-negative in the graceful default.

- **Q: Member cap 80?**
  - A: The largest god class on the corpus has 92 methods; 80 keeps
    the prompt bounded while preserving enough names to judge
    cohesion. (A class with >80 methods is obviously a candidate
    regardless; the truncated sample still reveals grab-bag-ness.)
