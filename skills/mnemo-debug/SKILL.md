---
name: mnemo-debug
description: Use when encountering a bug, test failure, or unexpected behavior. Drives a systematic reproduce -> hypothesize -> instrument -> bisect -> fix -> verify -> RCA flow, querying mnemo for prior incidents and capturing the root cause back to memory.
---

# Debug

**Type:** rigid. Phases run in order. The **RCA phase is non-skippable**:
every debug session ends with a `memory_project` node describing the root
cause, blast radius, and prevention.

If `superpowers:systematic-debugging` is available, prefer that for the core
loop and use this skill as a thin wrapper that adds the mnemo
prior-art-recall and RCA-capture phases.

## Phase 1 - Reproduce

**Goal:** Make the bug deterministic before chasing it.

1. Query mnemo for prior incidents matching the symptom:
   ```bash
   mnemo query "<error message or symptom>" --k 8
   mnemo query "<failing component> bug" --k 5
   ```
   If a hit looks like a known issue, surface the citation and confirm with
   the user before re-investigating.
2. Get a minimum reproducer: same input, same env, every time.
3. Note the env: branch, commit, OS, runtime version.

**Done when:** the bug fires on demand from a known input.

## Phase 2 - Hypothesize

**Goal:** Rank suspects.

List 3-5 plausible causes, ranked by likelihood. For each, name the
evidence that would confirm or refute it. Bias toward **recent changes**
(`git log --since="1 week ago" -- <file>`) and **simple causes** (typos,
config drift) before exotic ones (race conditions, memory corruption).

## Phase 3 - Instrument

**Goal:** Gather evidence cheaply.

Add temporary logs/asserts/breakpoints to confirm or refute the top
hypothesis. Prefer adding instrumentation to *bisect* (binary search) over
sprinkling logs everywhere.

**Don't move on until you have direct evidence**, not "looks plausible."

## Phase 4 - Bisect

**Goal:** Narrow to the offending change or input.

1. `git bisect` if the bug crossed commits.
2. Binary-search through the input space if the bug is input-dependent.
3. Capture the exact commit + line + condition that reproduces.

## Phase 5 - Fix

**Goal:** Minimum change. No scope creep.

1. Apply the smallest fix that addresses the root cause.
2. Resist the urge to also refactor / clean up "while I'm here." Track
   tangents as new tasks; do not bundle.
3. Add a regression test from Phase 1's reproducer. Verify red-green:
   - Run new test on broken code -> RED.
   - Apply fix -> GREEN.
   - **If the test was already green on broken code, the test is wrong**;
     fix the test before claiming done.

## Phase 6 - Verify

1. Run the regression test.
2. Run the full suite covering the affected area.
3. Manual smoke of the golden path that originally triggered the report.
4. Confirm no adjacent functionality regressed (run nearby tests too).

## Phase 7 - RCA to mnemo (mandatory)

Write a `memory_project` node:
- name: `rca_<short-slug>`
- description: one-line: "<symptom>: <root cause>"
- body must include:
  - **Symptom**: how it manifested
  - **Root cause**: what was actually wrong
  - **Blast radius**: what else this affects / could affect
  - **Fix**: commit hash + one-line summary
  - **Prevention**: test added, config check, or doc update that stops it
    from happening again
  - **Date** in absolute form (YYYY-MM-DD)

Trigger via `/mnemo-add` or write directly into the project's memory dir.
The PostToolUse hook will reindex.

**Done when:** `mnemo query "<symptom>"` returns the new RCA node as a top
hit.

## Cross-cutting safety rails

- Don't use destructive operations (`git reset --hard`, `git clean -f`,
  force-push) without explicit user approval.
- Don't silence failing tests to make CI green; fix the underlying issue.
- Capture the lesson even if the fix was trivial - the *category of bug*
  is the durable lesson.
