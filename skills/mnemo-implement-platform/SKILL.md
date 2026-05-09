---
name: mnemo-implement-platform
description: Use when starting a new feature, platform, or system from scratch. Walks through requirements gathering -> analysis -> design -> decision -> planning -> specs -> implementation -> verification -> documentation, querying mnemo at every phase for prior art.
---

# Implement Platform

**Type:** rigid. Phases run in order. Done-criteria gate every transition.
A mandatory **user-approval gate** sits between Design (3) and Decision (4):
do not proceed past Design until the user has explicitly approved one of
the alternatives.

Every phase writes an audit artifact to
`docs/plans/<YYYY-MM-DD>-<topic>-<phase>.md` and surfaces relevant prior art
from mnemo before doing new work.

## Phase 1 - Requirements gathering

**Goal:** Understand what we're building and why.

1. Query mnemo for any related prior work:
   ```bash
   mnemo query "<topic> requirements" --k 5
   mnemo query "<topic> stakeholder" --k 5
   ```
2. Ask the user (one question at a time, multiple-choice when possible):
   - Who is this for? (stakeholders / actors)
   - What problem does it solve? Use cases (3-5).
   - Hard constraints (latency / budget / compliance / platform).
   - Success criteria (measurable).
   - Out of scope (explicit non-goals).

**Output:** `docs/plans/<date>-<topic>-requirements.md` with the answers.
**Done when:** stakeholders, use cases, constraints, success criteria, and
non-goals are all captured. The user has reviewed and confirmed.

## Phase 2 - Analysis

**Goal:** Map dependencies and conflicts before designing.

1. Query mnemo for similar prior implementations and lessons:
   ```bash
   mnemo query "<topic> existing implementation" --k 8
   mnemo query "<topic> lesson" --k 8
   ```
2. Survey the existing codebase: search for similar features, related modules,
   existing abstractions to reuse.
3. List dependencies: external services, libraries, internal modules.
4. List conflicts: things this might break, contracts to honor.

**Output:** `docs/plans/<date>-<topic>-analysis.md`
**Done when:** dependencies, conflicts, and reuse opportunities are listed,
each with the relevant `[mnemo:<id>]` citations.

## Phase 3 - Design

**Goal:** Propose 2-3 alternatives with trade-offs.

For each alternative, document:
- Architecture sketch (modules, data flow)
- Pros / cons / risks
- Estimated complexity (small / medium / large)
- Which constraints from Phase 1 it satisfies / violates

End with **your recommendation** and the reason.

**Output:** `docs/plans/<date>-<topic>-design.md`
**Done when:** at least 2 alternatives are written up, and a recommendation
is stated. **Hard stop here** until the user approves one.

## Phase 4 - Decision (user-gated)

**Goal:** Persist the chosen approach in mnemo so future sessions know what
was picked and why.

1. After the user picks an alternative, write a memory node:
   - type: `project`
   - name: `decision_<topic>`
   - description: one-line summary of the chosen approach
   - body: chosen approach + why + alternatives rejected + Phase 3 link
2. Save via `/mnemo-add` or directly into the appropriate
   `~/.claude/projects/<project-key>/memory/` directory.

**Done when:** the decision node is in mnemo and surfaces on
`mnemo query "<topic> decision"`.

## Phase 5 - Planning

**Goal:** Break the chosen approach into ordered tasks.

Use `superpowers:writing-plans` if available. Otherwise, produce an ordered
task list with: dependencies, validation checkpoints, estimated effort.

**Output:** `docs/plans/<date>-<topic>-plan.md`
**Done when:** every task has inputs, outputs, and a way to verify it.

## Phase 6 - Specs

**Goal:** Concrete I/O contracts per task.

For each task: inputs (types + invariants), outputs (types + invariants),
edge cases, test cases. This is what makes Phase 7 mechanical.

**Output:** `docs/plans/<date>-<topic>-specs.md`

## Phase 7 - Implementation

Use `superpowers:test-driven-development` if available. For each task:
1. Write a failing test from the spec.
2. Implement until green.
3. Atomic commit with a conventional prefix (`feat:` / `fix:` / etc.).
4. Move on; never batch.

**Done when:** all spec tests are green, and every commit message follows
the project's conventions (see the repo CLAUDE.md for hard rules).

## Phase 8 - Verification

1. Full test suite green.
2. Manual smoke of the golden path.
3. Check perf if Phase 1 named a perf constraint.
4. Re-read the requirements doc and tick each item.

**Output:** `docs/plans/<date>-<topic>-verification.md`

## Phase 9 - Documentation

1. Update repo `CLAUDE.md` (and per-project `CLAUDE.md` if relevant) with the
   new feature, key gotchas, and surface area.
2. Capture lessons learned as a new memory node:
   - type: `project` for facts, `feedback` for user-validated patterns
   - name: `lessons_<topic>`
3. Trigger reindex (the PostToolUse hook does this automatically when you
   write into `~/.claude/projects/*/memory/`, but you can also run
   `mnemo reindex` directly).

**Done when:** lessons are queryable via
`mnemo query "<topic> lessons"`.

## Cross-cutting safety rails

- **No co-author trailers** on any commit.
- Each phase writes its artifact before moving on.
- Each phase ends by surfacing what's new for the next phase, citing
  `[mnemo:<id>]` so the chain is auditable.
- If a phase reveals the prior phase was wrong, **go back** rather than
  papering over it.
