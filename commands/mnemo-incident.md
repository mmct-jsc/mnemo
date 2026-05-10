---
description: Production incident response -- stabilize, investigate, RCA, post-mortem, promote to mnemo
argument-hint: <severity and one-line symptom>
---

Invoke the `mnemo:incident` skill to drive the production-incident
workflow.

The skill will:
1. Stub a post-mortem doc at `docs/incidents/<date>-<name>.md` with
   severity + start time + an empty timeline.
2. Pull every related past incident from mnemo with citations so
   you don't re-discover known mitigations.
3. Drive stabilization (rollback, kill-switch, traffic shed, restart,
   page external) BEFORE investigation, with every action logged to
   the timeline.
4. Once symptoms are stopped, drive hypothesis ranking, evidence
   collection, and RCA.
5. Fill out the post-mortem doc (summary, impact, timeline, root
   cause, resolution, prevention, action items).
6. Promote the durable lesson to a mnemo `memory_feedback` node so
   the next time the same symptom shows up, the fix is one query
   away.

`$ARGUMENTS` is the initial framing -- e.g. "sev2: signup API 500s
spiking since 14:32 UTC".
