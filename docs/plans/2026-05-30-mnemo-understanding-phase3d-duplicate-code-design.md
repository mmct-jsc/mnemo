# mnemo Understanding — Phase 3d: `duplicate_code` code-lens detector (v5.20.0)

> DoD-first design doc (pipeline #21). Specs + what-to-do / what-NOT-to-do
> are the definition of done. Written 2026-05-30.

## 0. Context + the pivot away from `orphan_modules`

The v5.19.0 handover named `orphan_modules` as the next code-lens
detector. Per the standing **probe-before-gate** rule (lesson #112), I
probed the live corpus first. **The probe killed it.** This doc records
that negative result (so no future session re-attempts it blindly) and
pivots to a detector the graph CAN support: `duplicate_code`.

### 0.1 `orphan_modules` probe result — REJECTED

Live corpus (3645 `code_module` nodes, 1692 `imports` edges):

| metric | value |
|---|---|
| modules with ZERO inbound `imports` edges (raw orphans) | **3446 (94.5%)** |
| distinct modules that are EVER an import target | **199 (5.5%)** |
| residual after dunder/entrypoint/test/script gate | **3029 (83% of ALL modules)** |
| sampled residuals with zero inbound edges of ANY relation | 200/200 |

The residual is dominated by (a) non-code files miscategorized as
`code_module` (`.md`, `.json`, `.yml`, configs, `.tsx`) and (b)
**`analyzer.py` itself** — a module provably imported by `server.py`,
`agent_tools.py`, and the whole test suite — reading as an "orphan".

**Root cause:** the import resolver records only ~12% of real imports
(it does not resolve package-style `from mnemo.analyzer import ...` to
the module node). The signal is unusable and an LLM judge cannot rescue
it — the edges are *missing*, not merely *unjudged* (garbage in).

### 0.2 The architectural lesson (why cyclic_imports shipped but orphan_modules can't)

**A detector built on an INCOMPLETE graph relation is viable only when a
missing edge causes a false NEGATIVE (safe under-report), not a false
POSITIVE (flood).**

- `cyclic_imports` reads the same sparse `imports` graph, but a missing
  edge can only *hide* a cycle → false negative → safe. It correctly
  returned `[]` (operator-green clean).
- `orphan_modules` is the inverse: every unrecorded import *manufactures*
  a false orphan → false positive → 83% flood.
- `dead_code` dodged the same asymmetry by restricting to PRIVATE
  symbols, where the relevant graph slice (within-module `calls`) IS
  complete. There is no analogous high-confidence restriction for module
  imports on this corpus.

`orphan_modules` is **deferred until the import resolver resolves
absolute/package imports** (a parsers/Tier-2 change, out of analyzer
scope). Captured as lesson #119.

## 1. Goal

A `duplicate_code` detector under the `code` lens: surface pairs of
`code_function` / `code_method` nodes whose bodies are near-identical
(copy-paste duplication) — the most actionable refactor smell. It uses
EMBEDDINGS (complete + reliable), NOT the import graph, so it sidesteps
the orphan_modules blocker entirely.

This is the explicitly-deferred sibling of `detect_duplicates`, whose
`DUPLICATE_TYPE_BUCKETS` comment says: *"Code nodes ... are intentionally
skipped -- ... the dedup story for code is different (refactoring
suggestions, not body merges)."* Phase 3d delivers that code dedup as a
lens detector with the refactoring framing.

## 2. Probe-validated gate (live corpus)

9301 `code_function` + `code_method` nodes; body sizes p50=12, p90=47
lines. Candidate pairs (600-node sample):

| cosine >= | >=1 line | >=3 | >=5 | >=8 |
|---|---|---|---|---|
| 0.93 | 37 | 31 | 25 | 17 |
| 0.95 | 26 | 22 | 19 | 15 |
| **0.97** | 16 | 14 | **12** | 10 |
| 0.98 | 8 | 7 | 7 | 6 |

Example hits (all GENUINE): `CitationCard` (cos 0.998, 30 lines) copy-
pasted across two QA components; `EmptyState`/`LoadingBlock`/`ErrorBox`
React components duplicated across 3-4 pages; `_intent` test helpers
across files. Signal-to-noise is excellent at 0.97.

**Chosen gate (deterministic, no judge):**
- node type in (`code_function`, `code_method`);
- NOT a test symbol (reuse `_is_test_symbol` — consistency with
  dead_code / god_object; test duplication is often intentional);
- body has **>= 5 non-empty lines** (`_MIN_DUPLICATE_CODE_LINES`) —
  suppresses trivial one-liners;
- within the combined code-type bucket, cosine **>= 0.97**
  (`DUPLICATE_CODE_COSINE_THRESHOLD`, higher than prose's 0.95 because
  code has more structural near-similarity);
- de-duplicated pairs (sorted-tuple seen-set), like `detect_duplicates`.

Severity **`medium`** (a refactor suggestion, peer to `duplicates`;
certain that the bodies match, but whether to extract is the user's
call). NO LLM judge — the threshold is precise (matches the
cyclic_imports "objective signal → no judge" precedent; avoids judge
debt). A future opt-in judge ("genuine extract target vs coincidental
similarity?") is cheap on `_LLMHelper` if ever wanted.

## 3. What to do

1. `analyzer.py`:
   - constants `DUPLICATE_CODE_COSINE_THRESHOLD = 0.97`,
     `DUPLICATE_CODE_L2_THRESHOLD = sqrt(2*(1-0.97))`,
     `_MIN_DUPLICATE_CODE_LINES = 5`, `_DUPLICATE_CODE_NODE_TYPES =
     ("code_function", "code_method")`;
   - helper `_nonempty_line_count(body) -> int`;
   - `detect_duplicate_code(store, *, embedder) -> list[finding]`:
     iterate both code types, gate (private/test/min-lines), embed body,
     `vec_search(k=12, type_filter=_DUPLICATE_CODE_NODE_TYPES)`, keep
     hits with L2 <= threshold + other-endpoint passing the same
     min-lines/test gate, emit one finding per unique pair;
   - register: `LENS_DETECTORS["code"] = ("dead_code", "god_object",
     "cyclic_imports", "duplicate_code")`;
   - orchestrator branch: `if "duplicate_code" in requested:
     findings.extend(detect_duplicate_code(store, embedder=embedder))`.
2. Per-finding shape: `{type: "duplicate_code", node_ids: [a, b],
   description: "Two code symbols 'X' / 'Y' share cosine 0.NN ...",
   severity: "medium", symbol: "<a name> / <b name>"}`. The
   per-finding `type` == detector name == summary key (no plural remap;
   follows dead_code / god_object, avoids the cyclic_import(s) wrinkle).
3. Surfaces: `mnemo_analyze` description (+ lens/types param text); the
   `/analyze` UI code-lens paragraph; `mnemo-knowledge-auditor`
   SKILL.md code-lens section + a "extract a shared helper" proposed-
   action note. Regenerate the MCP wire snapshot (description text only).
4. Version bump 5.19.0 -> 5.20.0 in the 4 canonical files + CHANGELOG.

## 4. What NOT to do (anti-goals)

- **Do NOT ship `orphan_modules`** (see §0). Defer to a resolver fix.
- **No LLM judge / no new env flag / no new MCP param** — deterministic
  detector, like cyclic_imports. The 27-tool count + `mnemo_analyze`
  signature are unchanged.
- **No change to the agnostic `detect_duplicates`** or
  `DUPLICATE_TYPE_BUCKETS` — code dedup is a SEPARATE lens detector, not
  a re-inclusion of code into the prose duplicates path. `KNOWN_DETECTOR_TYPES`
  stays 5.
- **Do NOT include test symbols** (intentional dup is common in tests).
- **Do NOT auto-apply** — a finding is a proposal; the user merges.
- **No new daemon dependency.**
- **No within-prose cross-type merge** — function<->method dups are
  fine (both are code), but never match a code body against a memory
  node.

## 5. Definition of Done

- [ ] `detect_duplicate_code` returns `[]` on an empty store and when
      `embedder is None` (clean fallback, matching detect_duplicates).
- [ ] Two functions with identical bodies (>= 5 lines) → exactly ONE
      finding (pair de-duped, not two).
- [ ] A near-identical pair below 5 lines is NOT flagged (min-lines gate).
- [ ] A test-symbol pair (name `test_*` or `/tests/` path) is NOT flagged.
- [ ] Two clearly-different functions are NOT flagged.
- [ ] A function<->method identical pair IS flagged (combined bucket).
- [ ] Registered in `LENS_DETECTORS["code"]`; reachable via `lens=code`
      and filterable via `types=["duplicate_code"]`; absent from the
      default agnostic suite (`lens=None` never runs it).
- [ ] severity `medium`; finding carries `symbol`.
- [ ] Full daemon suite green (+ new tests); ruff clean.
- [ ] MCP wire snapshot regenerated; 27-tool count unchanged.
- [ ] **Live dogfood**: `lens=code, types=["duplicate_code"]` on the
      real corpus returns a manageable set of GENUINE duplicates
      (e.g. the `CitationCard` / `EmptyState` pairs from the probe),
      and the count is reported (no silent cap). Operator-green =
      the live findings are real copy-paste, not noise.

## 6. Test plan (TDD, `tests/unit/test_duplicate_code_detector.py`)

A `FakeEmbedder` returning deterministic vectors keyed by body content
(identical body → identical vector → cosine 1.0; different → orthogonal).
Build a Store with real nodes (FK on both edge endpoints — lesson #115,
though this detector uses no edges, nodes still must exist for vec rows).

1. empty store → `[]`
2. `embedder=None` → `[]`
3. two identical >=5-line functions → exactly 1 finding, severity medium,
   both ids in `node_ids`, `symbol` set
4. identical but 3-line bodies → `[]` (min-lines gate)
5. test-named / tests-path pair → `[]` (test exclusion)
6. two unrelated functions (orthogonal vectors) → `[]`
7. identical function + method → 1 finding (combined bucket)
8. three mutually-identical functions → 3 pairs, each de-duped once
9. via-lens: `analyze(store, embedder=fake, lens="code",
   types=["duplicate_code"])` surfaces it; `analyze(..., lens=None)`
   (agnostic default) does NOT.
10. extend `test_lens_mechanism.py`: code suite now 4 detectors;
    `types` isolation still works.
11. (revision) cross-project pair NOT flagged (within-project scope);
    no-embeddings store -> `[]` (clean fallback, replaces the
    embedder=None test since the detector no longer takes an embedder).

## 7. Revision after live verification (perf + scope fix, lesson #120)

The first implementation re-embedded every eligible node
(`embedder.embed_text(body)` per node). Live dogfood: **632 findings in
987 s (~16 min)** — the findings were correct (CitationCard, EmptyState,
LoadingBlock, ErrorBox, hour_of, create_tables, downloadFile, ...), but
~16 min would TIME OUT the `/v1/analyze` HTTP call and the
`mnemo_analyze` MCP call → the user-facing surface fails → NOT
operator-green. It took TWO iterations (each verified live) to land:

1. **Read STORED embeddings; don't re-embed (987 s → 682 s).** The
   corpus is already indexed, yet `embed_text` issues one
   `model.encode([body])` per node (~106 ms × 9k+ nodes ≈ ~300 s). Read
   chunk-0 embeddings via `store.get_chunk_embeddings` (batched 400
   pairs/query to stay under SQLite's 999-variable limit) instead. But
   the live run was STILL 682 s — the dominant cost was the **6000
   per-node `vec_search` KNN queries** (each a full scan of the 15k-chunk
   vec table = O(N · corpus)), not the embedding. So a single fix wasn't
   enough.
2. **All-pairs cosine via numpy, per project (682 s → 2.5 s).** Replace
   the per-node KNN loop with an in-memory BLAS matmul: group eligible
   ids by `project_key`, stack each group's stored (normalized)
   embeddings into a matrix, and compute the upper-triangle cosine in
   row-blocks (`mat[r0:r1] @ mat.T`), emitting pairs ≥ 0.97. Grouping
   both scopes the result within-project (drops cross-repo coincidences
   like `Field` across two unrelated repos — 632 → 615 actionable) AND
   shrinks each matmul to `n_project²`. No live embedder; the signature
   drops the `embedder` param. numpy ships with the embed stack (lazy
   import; graceful `[]` if absent). Final live: **615 findings in
   2.5 s** (the matmul finds ALL within-project pairs — no `vec_search`
   k-cap — so dense clusters surface fully, slightly more than the
   capped KNN's 595).

**Lesson #120 — read the index, don't rebuild it:** re-embedding an
already-indexed corpus inside a detector is O(N) `model.encode` calls =
minutes; read the stored embeddings (`get_chunk_embeddings`).

**Lesson #121 — right algorithm beats micro-tuning, and verify RUNTIME
not just output:** a per-node `vec_search` KNN loop is O(N · corpus) =
minutes at 6k×15k; for all-pairs similarity within a bounded set, an
in-memory blocked matmul (numpy BLAS) is the right shape (seconds). A
correct-but-11-min detector times out the MCP/HTTP surface, so it is NOT
operator-green even though every finding is right — the live-verify
caught it twice; a unit test never would.
