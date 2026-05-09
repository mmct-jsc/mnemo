---
name: mnemo-review
description: Use when reviewing code (a PR, a diff, a feature branch). Pulls project-specific review checklists from mnemo memory, runs the review, and captures any new lessons back into memory so the next review benefits.
---

# Code Review with Project Memory

**Type:** flexible. The skill adds two things on top of generic review:
project-aware checklists pulled from mnemo, and a learning loop that
captures novel issues so the next review starts smarter.

## Phase 1 - Pull project review memory

Before opening the diff, query mnemo for project-specific review patterns:

```bash
mnemo query "<repo or component> code review" --k 8
mnemo query "<language> common issues here" --k 5
mnemo query "<repo> security checklist" --k 5
```

Build a quick checklist from the hits. Examples this surfaces:
- "We always check `requestChecksumCalculation` on S3 clients" (from a
  past MinIO compat RCA)
- "Pre-rebuild: verify .env + model_weights/*.pt exist" (from a deploy RCA)
- "Hard rule: no Co-Authored-By trailers on commits" (feedback)

Each hit is a `[mnemo:<id>]` you'll cite when applying it in the review.

## Phase 2 - Static review

Walk the diff with the project checklist plus the standard rubric:

| Rubric line | Look for |
|---|---|
| Correctness | Off-by-one, error paths, async ordering, type punning |
| Security | Injection, SSRF, IDOR, secrets in logs, auth bypass |
| Concurrency | Race conditions, lock ordering, fork/exec safety |
| Backward compat | API/wire-format/migration impact |
| Tests | Coverage of new behavior + at least one regression test |
| Conventions | Naming, file layout, commit message format |
| Performance | N+1, sync I/O on hot path, allocation in tight loop |

For each finding, classify by severity:
- **P0 ship-blocker** - correctness, security, data-loss risk
- **P1 must fix before merge** - regression risk, missing tests
- **P2 nice to have** - style, naming, light refactor

## Phase 3 - Run the diff in your head

For non-trivial changes, mentally execute the diff with realistic input:

- What does the user see if X is null / empty / huge?
- What happens if two callers race?
- What happens on retry / replay?
- What happens at module boundaries (this fn called from N call sites -
  did all of them get updated?)

## Phase 4 - Surface findings to the author

Group findings by severity. For each:

1. The finding (what's wrong, where).
2. The reason (why it matters - cite mnemo when applicable).
3. The suggested fix (or "discuss" for genuinely-ambiguous calls).

Use `[mnemo:<id>]` citations when a finding maps to a known project
pattern - it tells the author "this is a repeat issue, not a one-off
preference."

## Phase 5 - Capture novel lessons

Before closing the review, ask: was anything in this diff a category of
issue you hadn't seen before in this project? If yes, it should become a
new memory node.

Use `mnemo-add-knowledge`:
- type: `feedback` if it's a rule the team will apply going forward
- type: `project` if it's a fact about this codebase

Examples of capture-worthy lessons:
- "Adding NOT NULL columns to `events` table requires a two-step migration
  because of replication lag"
- "All routes under `/api/v1/reports` must go to `event-service`, not
  `analytics-service`"
- "S3Client instances need `requestChecksumCalculation: 'WHEN_REQUIRED'`
  to talk to our MinIO build"

Don't capture trivia ("typo in comment", "missing test for trivial getter").
Only durable lessons - the kind you'd want a brand-new reviewer to know.

## Cross-cutting

- Be technically rigorous. Don't soften findings to be polite. Don't
  inflate findings to look thorough.
- If a finding seems wrong on second look, mark it as such openly rather
  than burying it.
- Pair this skill with `superpowers:requesting-code-review` (if available)
  for the agentic-review variant, or `superpowers:receiving-code-review`
  if you're on the receiving end.
