---
name: mnemo-refactor
description: Use when restructuring existing code without changing its behavior. Drives a measure -> propose -> atomic-commits -> verify flow, with each commit independently green and behavior-preserving.
---

# Refactor

**Type:** flexible. The four phases below are the recommended shape; adapt
as the change demands, but never skip "measure" and never bundle multiple
behavior-preserving steps into one commit.

## Phase 1 - Measure baseline

Before changing anything:

1. Query mnemo for prior refactors of this area:
   ```bash
   mnemo query "<module> refactor" --k 5
   mnemo query "<module> design" --k 5
   ```
2. Capture the current behavior:
   - Run the existing tests; they're your ground truth.
   - If coverage is thin, **add characterization tests first** (these freeze
     current behavior, even if it's ugly, so the refactor can be verified).
3. Capture perf baseline if relevant (compile time, request latency, memory).

**Done when:** there's a green test suite that fully exercises the area you
intend to change, and you've recorded the perf numbers you care about.

## Phase 2 - Propose target shape

1. State the smell: what's wrong with the current shape (duplication,
   leaky abstraction, dead branches, etc.).
2. State the target shape concisely.
3. Sketch the **path** between them as an ordered list of small,
   behavior-preserving moves. Each move should be a single commit.

If you can't see a chain of small moves, **stop**: either the target shape
is wrong, or this is actually a feature change (use
`mnemo-implement-platform` instead).

## Phase 3 - Atomic, behavior-preserving commits

For each move:

1. Make the move.
2. Run the full suite (or at least the area-specific tests). It MUST stay
   green.
3. Commit with a `refactor:` prefix and a one-line summary of the move.
4. **Do not** combine moves. Each commit is its own atomic step.

If a move requires a temporary scaffold (like a thin wrapper that both old
and new callers can use during a rename), that's fine - the scaffold lives
across multiple commits and is removed at the end.

## Phase 4 - Verify behavior + perf

1. Full suite green.
2. Perf metrics from Phase 1 unchanged (within noise) or improved.
3. Manual smoke of the most important callers.
4. Diff review: every change is mechanical or clearly behavior-preserving.
   If you see a change that changed semantics, that's a bug introduced by
   the refactor - revert and try again.

## Cross-cutting safety rails

- **Never** use a refactor to fix bugs silently. If you find a bug,
  capture it, finish the refactor, then fix the bug as a separate change.
- **Never** drop tests "because the code changed shape." If a test no
  longer compiles, port it; if a test no longer makes sense, *that's
  evidence the refactor changed behavior*.
- Don't try to refactor and add features in the same change. If the user
  asked for both, do them as two separate sessions.

## After: capture the pattern

If the refactor exposed a generally-useful pattern (or a recurring smell),
write a `memory_project` node so the next refactor in this area starts from
prior art.
