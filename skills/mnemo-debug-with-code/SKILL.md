---
name: mnemo-debug-with-code
description: Use when "X is broken, why?" and the user has indexed code_repo for the project under investigation. Mirrors mnemo:debug's phases but folds in the call graph + decision provenance at the investigate phase so hypotheses cite specific functions and commits.
---

# Debug with code

**Type:** rigid. Mirrors `mnemo:debug`'s 7-phase loop with code-graph
integration at phase 3.

If `mnemo:debug` is available, prefer that as the parent flow and
use this skill as a thin overlay that adds the code-graph + decision-
provenance steps. The phases below are the overlay.

## Phase 1 - Frame the symptom

Reproduce the bug. Capture the error message + stack trace + steps.
This is bug-tracking discipline, not mnemo-specific.

## Phase 2 - Pull priors

```bash
mnemo query "<symptom>" --k 8 --project <project_key>
```

Look for prior `feedback_*` / `memory_project` hits that match.
If one is a near-duplicate, flag it and ask the user whether to
treat it as a regression.

## Phase 3 - Trace the failing code path

This is the code-graph step that distinguishes this skill from
`mnemo:debug`.

1. Identify the function the stack trace bottoms out in. Look it
   up via `mnemo:trace-call` or directly:
   ```bash
   mnemo query "<function from stack trace>" --k 3 --project <project_key>
   ```
2. Walk reverse `calls` edges: who calls this function? Use the
   /code/<project>/function/<node_id> page or the
   `?dst_id=<id>&relation=calls` HTTP query. The frontier of
   callers is the hypothesis space.
3. For each likely-suspect caller, walk `references_function` to
   see which commits touched it most recently:
   ```bash
   curl "http://127.0.0.1:7373/v1/edges?dst_id=<node_id>&relation=references_function"
   ```
4. For commits with `motivated_by` edges to memory_feedback or
   plan_doc nodes, read them. The recent decision that landed
   the suspect code is often the source of the bug.

## Phase 4 - Hypothesis list

Each hypothesis names:
- A specific function (cited).
- A specific commit (cited if provenance is available).
- A causal mechanism in one sentence.

## Phase 5 - Investigate

Standard: reads + logs + targeted tests. The earlier phases
narrowed the search; this phase confirms.

## Phase 6 - Fix

Apply the fix. Update tests.

## Phase 7 - RCA capture

**Non-skippable.** Write a `memory_feedback` node:

```bash
mnemo source add ... # if needed
# Or directly:
curl -X POST http://127.0.0.1:7373/v1/nodes -d '{
  "type": "memory_feedback",
  "name": "feedback_<short_slug>",
  "description": "<one-line root cause>",
  "body": "<full RCA + prevention + linked commits/functions>",
  "project_key": "<project_key>"
}'
```

Reference the affected `code_function` nodes by id in the body
so the next person hitting a similar symptom can follow the
chain back.

## Output to user

A compact write-up: symptom, hypothesis chain (each cited), the
fix, and the RCA node id.
