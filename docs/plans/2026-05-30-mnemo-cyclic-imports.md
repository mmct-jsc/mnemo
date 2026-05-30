# mnemo v5.19.0 — cyclic_imports (code lens, the structure triad)

> **Spec doctrine (pipeline #21):** DoD-first. Summary / Why / Spec /
> DoD / Anti-goals / Scope / Comparison / Build sequence / Open Qs.

## 1. One-line summary

Add a third detector to the **`code`** lens: **`cyclic_imports`** —
module import cycles, found by an iterative Tarjan SCC over the
`imports` edge graph. Deterministic + precise (a cycle is
unambiguous), **no LLM judge** (no debt growth). Completes the
code-structure triad: dead code (uncalled) / god objects (oversized)
/ import cycles (tangled). `LENS_DETECTORS["code"]` goes to three.

## 2. Why this matters

Import cycles break modularity, complicate testing + tooling, and
cause import-order bugs — a classic architectural smell that's hard
to spot by hand (a 4-module cycle is invisible reading any one
file). Dedicated tools (pylint, import-linter, madge) exist solely
for this. mnemo already builds the `imports` edge graph (Tier-1, AST
-derived, complete); detecting cycles is a graph algorithm over it.

It rounds out the code lens into the three canonical structure
smells and stays deterministic — no LLM, no judge, no growth of the
LLM-helper surface.

**Demonstrability note:** the live corpus has **0 import cycles**
(probed: 1692 imports edges, 871 modules, 0 self-loops). That is the
CORRECT clean result for an acyclic graph — running the detector
live and getting `[]` IS operator-green (the expected output for a
clean import graph). The detection LOGIC is proven by synthetic
fixtures (2-cycle, 3-cycle, self-loop, two-cycles) in the unit
tests. "Live shows clean + fixtures prove it finds cycles" is a
complete verification story.

## 3. Spec

### 3.1 The detector

`detect_cyclic_imports(store) -> list[dict]`:

- Read all `imports` edges (`src=module`, `dst=module`). Build a
  directed adjacency map.
- Run an **iterative** Tarjan SCC (iterative, not recursive — a deep
  import chain must not hit Python's recursion limit in the daemon).
- A cycle is a strongly-connected component of size ≥ 2, OR a single
  node with a self-edge (a module importing itself).
- Each cycle → one finding:
  ```
  {"type": "cyclic_import",
   "node_ids": [sorted cycle member ids],
   "description": "Import cycle among N modules: A, B, C; breaks
                   modularity — consider breaking it.",
   "severity": "medium"}
  ```
  Module names resolved via `get_node` for the description (cycles
  are few, so the lookups are bounded).

### 3.2 Severity = medium

A cycle is DEFINITIVE (deterministic, precise — it either exists or
doesn't), unlike the `candidate` detectors that await
human/LLM judgement about whether the finding is even real. So it's
not `candidate`. It's a confirmed structural issue peer to
`duplicates` (= `medium`), not an urgent broken-reference (`high`).
Whether to BREAK a given cycle is still a human call, but its
EXISTENCE is certain → `medium`.

### 3.3 No LLM judge

A cycle's existence is objective; there's nothing for a judge to
confirm. (A future judge could grade "harmful vs benign mutual
recursion", but that's speculative — ship deterministic, no 6th
judge, no debt. The `_LLMHelper` base makes adding one later cheap
if a real need appears.)

### 3.4 Registration + surface

- `LENS_DETECTORS["code"] = ("dead_code", "god_object",
  "cyclic_imports")`. `analyze()` dispatch gains
  `if "cyclic_imports" in requested`.
- `mnemo_analyze` description + `types` list mention cyclic_imports;
  no new param; wire snapshot regenerated.
- `/analyze` UI + SKILL.md code-lens section note cyclic_imports.
- `KNOWN_DETECTOR_TYPES` unchanged (lens detector, not agnostic).

### 3.5 Tests

- `tests/unit/test_cyclic_imports_detector.py` — 2-module cycle
  (both ids); 3-module cycle (all three); acyclic chain → none;
  self-loop → flagged; two disjoint cycles → two findings;
  non-import edges ignored; severity medium; via `lens="code",
  types=["cyclic_imports"]`; empty store → none. Real `code_module`
  nodes + `imports` edges (FK on both endpoints — lesson #115).
- Extend `test_lens_mechanism.py` — code suite now has THREE
  detectors; `types` isolates each.
- `tests/unit/_snapshots/mcp_tool_list.json` regen.

Target: +12-14 tests; daemon suite 1649 → ~1663.

## 4. Definition of Done

- [ ] Design doc (this file).
- [ ] Iterative-Tarjan `detect_cyclic_imports(store)` — SCC ≥ 2 +
  self-loops; severity `medium`; module names in the description.
- [ ] `LENS_DETECTORS["code"]` += `cyclic_imports`; `analyze()`
  dispatches it; `KNOWN_DETECTOR_TYPES` unchanged.
- [ ] 12+ new/extended tests pass; full suite green (no regression
  on 1649).
- [ ] Ruff clean. CI 9/9.
- [ ] **Live**: `lens=code, types=["cyclic_imports"]` returns `[]`
  (correct — the corpus import graph is acyclic); `lens=code`
  returns dead_code + god_object + cyclic_imports summary keys.
- [ ] MCP signature unchanged; snapshot regen for the description.
- [ ] MEMORY.md promoted to v5.19.0; v5.18.0 demoted.
- [ ] Tag `v5.19.0` published on `public`.

## 5. Anti-goals

- **No LLM judge** (a cycle is objective; no debt growth).
- **No new lens / MCP param / agnostic detector** — cyclic_imports
  is a `code`-lens detector; `KNOWN_DETECTOR_TYPES` stays 5,
  27-tool count unchanged.
- **NEVER auto-apply** — a cycle finding is a proposal; the user
  breaks it.
- **Recursive Tarjan is banned** — iterative only, so a deep import
  chain can't crash the audit with a RecursionError.
- **No new daemon dependencies** (no networkx — hand-rolled SCC).

## 6. Scope

### In-scope:
- `daemon/mnemo/analyzer.py` — iterative SCC helper +
  `detect_cyclic_imports` + `LENS_DETECTORS` entry + `analyze()`
  dispatch.
- `daemon/mnemo/agent_tools.py` — `mnemo_analyze` description.
- `daemon/mnemo/ui/templates/analyze.html` — note.
- `skills/mnemo-knowledge-auditor/SKILL.md` — code-lens section.
- `tests/unit/test_cyclic_imports_detector.py` (new) + extend
  `test_lens_mechanism.py`.
- `tests/unit/_snapshots/mcp_tool_list.json` regen.
- `CHANGELOG.md` + version bump 5.18.0 → 5.19.0 (4 files).

### Out-of-scope:
- dead_code / god_object / the agnostic five — unchanged.
- The `imports` edge resolver — read-only here.
- orphan_modules — deferred (sparse-import-graph precision problem;
  needs its own gate design).

## 7. Comparison

| Metric | Before (5.18.0) | After (5.19.0) |
|---|---|---|
| code-lens detectors | 2 (dead_code, god_object) | 3 (+cyclic_imports) |
| import-cycle detection | none | iterative-Tarjan, exact |
| LLM judge classes | 5 | 5 (deterministic — no 6th) |
| MCP tool count | 27 | 27 |
| Daemon suite | 1649/2skip | 1663+/2skip |
| Live result on corpus | n/a | `[]` (correct — acyclic) |

## 8. Build sequence (TDD)

1. **RED** — `test_cyclic_imports_detector.py`: 2-cycle, 3-cycle,
   acyclic, self-loop, two-cycles, non-import-ignored, severity,
   empty. Run → red.
2. **GREEN** — iterative Tarjan + `detect_cyclic_imports`. Run →
   green.
3. **RED** — extend `test_lens_mechanism.py`: three-detector code
   suite, `types` isolation. Run → red.
4. **GREEN** — register in `LENS_DETECTORS["code"]` + `analyze()`
   dispatch. Run → green.
5. Surfaces (MCP desc / UI / SKILL) + regen wire snapshot.
6. Full pytest + ruff. 1663+/2skip + clean.
7. Live: cyclic_imports `[]` (clean) + 3-detector lens summary.
8. Version bump + CHANGELOG + ship.
9. Post-merge restart + reindex + handover.

## 9. Open questions resolved

- **Q: Recursive or iterative Tarjan?**
  - A: Iterative. A daemon must not crash on a deep import chain
    (Python's default recursion limit is 1000). Iterative Tarjan is
    ~40 lines but production-safe; the TDD fixtures (incl. a longer
    chain) guard correctness.

- **Q: Severity — candidate / medium / high?**
  - A: `medium`. A cycle's EXISTENCE is deterministic + certain (not
    `candidate`, which means "awaiting confirmation it's real"). It's
    a confirmed structural issue peer to `duplicates`, not an urgent
    broken reference (`high`). Whether to break it is a human call,
    but the finding is certain.

- **Q: Ship despite 0 cycles on the dogfood corpus?**
  - A: Yes. `[]` on an acyclic graph is the CORRECT result —
    operator-green verification is "the detector runs live and
    returns the expected output," which for a clean graph is empty.
    The synthetic fixtures prove it finds cycles when they exist.
    A detector that correctly reports "clean" is valuable (it
    certifies the import graph is acyclic, on demand).

- **Q: An LLM judge for cycle harm?**
  - A: No. Existence is objective. A harm-grading judge is
    speculative; ship deterministic. The `_LLMHelper` base makes
    adding one later trivial if a real need appears.

- **Q: orphan_modules in the same release?**
  - A: No. orphan_modules (modules nothing imports) has a
    sparse-import-graph precision problem + no clean precision lever
    — it needs its own gate design (entrypoint/`__init__`/`__main__`
    exclusions, maybe a judge). Out of scope; keep this release
    tight on the precise, deterministic cyclic_imports.
