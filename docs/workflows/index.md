# Workflow skills

mnemo ships seven systematic workflows as Claude Code skills. Each lives
under [skills/](../../skills/) as a `SKILL.md`. Use them when you start a
task that fits the trigger description; Claude will surface the relevant
phases and run the right `mnemo` queries at each step.

| Skill | Type | Use when... |
|---|---|---|
| [mnemo-implement-platform](../../skills/mnemo-implement-platform/SKILL.md) | rigid | Starting a new feature/platform from scratch |
| [mnemo-debug](../../skills/mnemo-debug/SKILL.md) | rigid | Hitting a bug, test failure, or unexpected behavior |
| [mnemo-refactor](../../skills/mnemo-refactor/SKILL.md) | flexible | Restructuring code without changing behavior |
| [mnemo-add-knowledge](../../skills/mnemo-add-knowledge/SKILL.md) | flexible | Capturing a new insight as a memory node |
| [mnemo-query-knowledge](../../skills/mnemo-query-knowledge/SKILL.md) | rigid | Recalling memory on demand (not via auto-injection) |
| [mnemo-onboard-project](../../skills/mnemo-onboard-project/SKILL.md) | flexible | First scan of a new repository |
| [mnemo-review](../../skills/mnemo-review/SKILL.md) | flexible | Reviewing code (PR / diff / branch) |

## Rigid vs. flexible

**Rigid** skills (`implement-platform`, `debug`, `query-knowledge`) have
phases that must run in order, with done-criteria gating each transition.
Skipping or reordering breaks the contract. `implement-platform` even has
a mandatory user-approval gate between Design (3) and Decision (4).

**Flexible** skills (`refactor`, `add-knowledge`, `onboard-project`,
`review`) have a recommended shape but adapt to the situation. Phase
boundaries are softer.

## Common pattern across all seven

Every skill follows the same shape:

1. **Pull prior art from mnemo** at the start (`mnemo query "<topic>"`).
2. **Do the work** following the skill's phases.
3. **Capture lessons** at the end as a new memory node, so the next
   session benefits.

The point is that running any of these skills both *uses* mnemo and
*feeds* it. The store gets richer over time without explicit effort.

## When to *not* use a skill

- You're answering a quick factual question. Just answer.
- You're making a one-line change with obvious correctness. Just do it.
- The task spans multiple skills (a refactor that also fixes a bug). Pick
  the dominant one and run it; capture the secondary work as a follow-up.

## Per-skill quick reference

### implement-platform (9 phases)

`requirements -> analysis -> design -> decision -> planning -> specs ->
implementation -> verification -> documentation`

User-approval gate between Design (3) and Decision (4). Each phase writes
an artifact to `docs/plans/<date>-<topic>-<phase>.md`.

### debug (7 phases)

`reproduce -> hypothesize -> instrument -> bisect -> fix (minimum) ->
verify -> RCA`

The RCA phase writes a `memory_project` node with symptom, root cause,
blast radius, fix, and prevention. It's non-skippable.

### refactor (4 phases)

`measure baseline -> propose target shape -> atomic commits (each green)
-> verify behavior + perf`

Each commit must independently keep tests green. No bundling moves.

### add-knowledge (5 phases)

`novelty check -> categorize -> write with Why+How-to-apply -> graph-link
-> reindex`

Novelty check is the most important: if a similar entry already exists,
supersede it instead of duplicating.

### query-knowledge (rigid contract)

Documents the retrieval pipeline (intent classify -> vector + graph ->
score -> compress -> cite) and how to interpret the result fields. Use it
when you need to call retrieval directly rather than relying on the
auto-injection hook.

### onboard-project (5 phases)

`scan -> extract conventions -> build initial nodes -> link to global
patterns -> user-confirm + register sources`

Less is more: 5 high-quality starter nodes beat 50 noisy ones.

### review (5 phases)

`pull project review memory -> static review with checklist -> mental
execution -> findings -> capture novel lessons`

The skill turns code review into a learning loop: each review can both
apply prior project lessons and add new ones.
