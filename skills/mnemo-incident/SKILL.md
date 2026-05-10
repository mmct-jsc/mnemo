---
name: mnemo-incident
description: Use when handling a production fire / outage. Drives the stabilize -> investigate -> RCA -> post-mortem flow with aggressive prior-incident memory pull. Distinct from mnemo:debug because incident cadence is different (severity tracking, hard time-pressure on stabilization, formal post-mortem doc).
---

# Incident

**Type:** rigid. Phases run in order. The post-mortem phase is
non-skippable. Stabilization comes BEFORE investigation -- get the
fire out, then figure out why it started.

## Phase 1 - Severity

**Goal:** Frame the incident, start the timer.

Ask:
- Severity (1: full outage / data loss; 2: degraded for many users;
  3: degraded for a few; 4: nuisance)
- Started when (best guess UTC)
- Surface symptoms (what users / dashboards see)
- Affected systems

Stub the post-mortem doc immediately:
```
docs/incidents/<YYYY-MM-DD>-<short-name>.md
```
with the severity, start time, and a "Timeline" section we'll append
to in real time.

## Phase 2 - Pull priors

**Goal:** Did we see this before?

```bash
mnemo query "<symptom keyword>" --k 8
mnemo query "<affected system> incident" --k 5
mnemo query "<error code or stack signature>" --k 5
```

For each top hit that looks related, surface the citation. Tag any
node where the *fix* from a past incident might apply directly.
Confirm with the user before re-applying historical fixes.

If a past RCA matches the current symptom, the response is usually:
re-apply the documented mitigation, then go to phase 3 anyway because
seeing the same incident twice is ITSELF a problem worth documenting.

## Phase 3 - Stabilize

**Goal:** Stop the bleeding. Investigation comes later.

Standard escalation order:
1. Roll back the last deploy if it's recent and plausible.
2. Toggle a kill-switch / feature flag if one exists.
3. Shed traffic (rate-limit, drain, redirect) if the system is
   overloaded.
4. Restart the failing component if it's a known transient.
5. Page the on-call for the affected dependency if external.

Every action goes into the timeline section of the post-mortem doc
with an absolute timestamp and who did it. Even if it didn't help.
Especially if it didn't help.

**Don't move to phase 4 until users / dashboards show the symptoms
have stopped or are trending green.**

## Phase 4 - Investigate

**Goal:** What actually went wrong.

List 3-5 hypotheses, each with the evidence that would confirm or
refute it. Cite mnemo nodes when a hypothesis is "we saw this before
and the cause was X". Prefer cheap evidence (logs, metrics, recent
commit list) before expensive (replay, reproducer build, code dive).

Bisect by:
- `git bisect` if a deploy window is clearly implicated.
- Time-bisect on metrics (when did the error rate first spike?).
- Component-bisect (which subsystem started failing first?).

## Phase 5 - RCA

**Goal:** State the root cause precisely.

A good RCA has:
- **What changed**: the specific commit, config, deploy, or external
  event that introduced the failure.
- **Why it broke**: the causal chain from change to user-visible
  symptom.
- **Why we missed it**: what test, monitor, or review *should* have
  caught this and didn't.

If the answer to "why we missed it" is "we don't have anything for
that," the prevention from phase 6 must close that gap.

## Phase 6 - Post-mortem doc

Fill `docs/incidents/<date>-<name>.md`:

1. **Summary** (2-3 sentences for someone scanning the index)
2. **Severity + impact** (users affected, duration, blast radius)
3. **Timeline** (absolute UTC timestamps, including stabilization
   actions that didn't help)
4. **Root cause** (the precise causal chain)
5. **Resolution** (the fix that ended the impact, with commit hash)
6. **Prevention** (concrete: a regression test, a new monitor, a
   doc, a process change)
7. **Action items** (each with an owner and a target date)

The doc is shareable as-is. Anyone reading should be able to
reconstruct what happened, what we did, and what we changed so it
doesn't happen again.

## Phase 7 - Memory promotion

**Goal:** Surface the *durable lesson* in mnemo.

Promote the post-mortem to a `memory_feedback` node:
- name: `feedback_<short-incident-slug>` (or `incident_<...>`)
- description: 1-line "<symptom>: <root cause>"
- body: the prevention + the category-of-bug (so future
  similar-symptom queries surface this)
- BASE if the lesson genuinely applies across projects (rare)

Trigger via `/mnemo-add` or write directly into the project's
memory dir. The PostToolUse hook reindexes.

**Done when:** `mnemo query "<symptom>"` returns the new feedback
node in the top 3 hits.

## Cross-cutting

- Stabilization beats investigation. Don't optimize for understanding
  while users are still affected.
- Every action gets a timestamp in the timeline, not just the fix.
- The post-mortem is for the *next* on-call. Write it for someone
  who has none of your context.
- Capture the lesson even if the fix was a one-line config change.
  The *category* of failure is what compounds.
