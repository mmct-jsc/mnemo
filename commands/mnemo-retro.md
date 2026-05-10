---
description: End-of-session retro -- extracts durable lessons from recent activity into mnemo memory
argument-hint: [optional time window or commit range]
---

Invoke the `mnemo:retro` skill to triage recent activity into mnemo
memory entries.

The skill will:
1. Sweep the recent audit log + git log + new design / incident docs
   to reconstruct what just happened.
2. Propose 0-N candidate memory entries (each with a type, name,
   description, draft body, and confidence level).
3. Walk you through accept / edit / reject for each candidate.
4. Write accepted entries as memory files under the active project
   (or BASE if the lesson is universal) and reindex.

Run this at end-of-session, after a feature ships, or after an
incident's RCA -- whenever durable knowledge has been generated and
is at risk of being forgotten.

Defaults to the last few hours of activity. Pass an explicit window
in `$ARGUMENTS` (e.g. "since this morning", "since 14:00 UTC", or a
commit range) to override.
