---
name: mnemo-plan
description: Use when starting a non-trivial change. Drives a plan-first workflow that pulls past mnemo context, brainstorms one question at a time, proposes 2-3 approaches with trade-offs, captures user decisions, and produces a phased design doc artifact before any code is written.
---

# Plan

**Type:** rigid. Phases run in order. No code is written in this skill.
The output is a `docs/plans/<date>-<topic>-design.md` artifact the user
reviews before invoking `mnemo:implement-platform`.

If `superpowers:brainstorming` is available, prefer that for the
brainstorming phase and use this skill as a thin wrapper that adds the
mnemo context-pull and design-doc-emit phases.

## Phase 1 - Pull context

**Goal:** Surface every relevant prior decision, design doc, and
feedback note before asking the user any question.

1. Query mnemo for the request:
   ```bash
   mnemo query "$ARGUMENTS" --k 8 --budget 1000
   ```
2. If the user's request mentions a system, also query:
   ```bash
   mnemo query "<system> design" --k 5
   mnemo query "<system> feedback" --k 5
   ```
3. Show the top hits with `[mnemo:<id>]` citations. Confirm with the
   user which (if any) the new design should derive from or supersede.

**Done when:** the user has acknowledged the prior art (or confirmed
there is none) and you've noted which past nodes are relevant.

## Phase 2 - Brainstorm

**Goal:** Refine the request into well-defined requirements.

Ask **one question at a time**. Prefer multiple choice when possible.
Cover: purpose, success criteria, constraints, non-goals, who else is
affected, what's explicitly out of scope.

If `superpowers:brainstorming` is available, defer to it -- this skill
adds the citation requirement (every borrowed pattern names a
`[mnemo:<id>]`).

## Phase 3 - Approaches

**Goal:** Surface alternatives, recommend one.

Propose 2-3 architectural approaches with trade-offs. Lead with the
recommendation and explain *why* (citing prior mnemo nodes when an
approach mirrors something the user has done before, or differs from
something that didn't work).

The user picks one. If they push back on all options, return to
phase 2 -- the requirements aren't tight enough yet.

## Phase 4 - Decisions

**Goal:** Lock the architecture decisions one at a time.

Each architecture decision is its own multiple-choice question with a
recommendation. After every "approved", capture it. Common decision
points:

- Storage layout, schema migrations, backward compat
- API surface (new endpoints, breaking changes)
- Distribution / packaging
- Test strategy (unit / integration / e2e split)
- Observability (logs, metrics, audit trail)
- Failure modes and error handling

## Phase 5 - Write the design doc

Render the validated design to:
```
docs/plans/<YYYY-MM-DD>-<short-topic-slug>-design.md
```

Sections, in this order:

1. **Goal, non-goals, scope summary**
2. **Architecture** (text + ASCII diagram if useful)
3. **Public surface** (HTTP / SDK / CLI changes)
4. **Internal changes** (per-component breakdown)
5. **Migration / backward compatibility**
6. **Test strategy** (what to add, what to keep green)
7. **Phased roadmap** (one commit per phase, conventional prefix)
8. **Risk register** (what can go wrong + mitigation)
9. **Open questions deferred** (what's NOT in this design's scope)

Cite every borrowed pattern as `[mnemo:<id>]`.

## Phase 6 - Done criteria

The design doc passes review when:

- The phased roadmap has 5-15 phases (smaller = under-decomposed,
  larger = scope creep).
- Every phase fits in 1-3 commits.
- Every architecture decision is explicit ("we picked X because Y").
- Risks are listed, not just hoped-against.
- Non-goals are written down so future scope creep is detectable.

Stop here. The user runs `/mnemo-implement-platform` (or the
`superpowers:executing-plans` skill) next, with the doc as input.

## Cross-cutting

- No code in this skill. Tempting in phase 4 -- resist.
- Always cite prior mnemo nodes; do not silently re-derive.
- If the user gets impatient and asks for code, gently push back: a
  written plan saves more time than it spends. If they insist, exit
  this skill cleanly and let them invoke implement-platform.
