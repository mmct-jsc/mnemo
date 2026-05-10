---
description: Plan a feature before any code -- pulls mnemo context, brainstorms, decisions, writes design doc
argument-hint: <topic or feature description>
---

Invoke the `mnemo:plan` skill to drive a plan-first workflow on this
topic.

The skill will:
1. Query mnemo for prior designs / feedback / decisions related to
   `$ARGUMENTS` and surface them with citations.
2. Brainstorm with you one question at a time to refine requirements.
3. Propose 2-3 architectural approaches and recommend one.
4. Capture each architecture decision as you approve it.
5. Write the validated design to
   `docs/plans/<YYYY-MM-DD>-<topic>-design.md` with a phased roadmap.

No code is written in this skill. The output is a reviewable design
doc you take into `mnemo:implement-platform` (or
`superpowers:executing-plans`) next.
