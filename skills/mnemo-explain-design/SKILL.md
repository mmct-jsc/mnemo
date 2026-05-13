---
name: mnemo-explain-design
description: Use when the user asks "why is the codebase structured this way?" / "what's the design philosophy of `<project>`?" / "summarize the architecture". Pulls plan_doc nodes + the project's top-level modules and synthesizes a design narrative.
---

# Explain design

**Type:** flexible. Adapt the synthesis depth to the user's question.

## Step 1 - Pull plan_doc nodes scoped to the project

```bash
mnemo query "<project_key> design" --k 8 --project <project_key>
```

Filter to nodes whose type is `plan_doc`. These are the design
docs the team has captured -- they're the highest-signal context
for understanding intent.

## Step 2 - Pull the top modules

```bash
mnemo query "<project_key> module overview" --k 10 --project <project_key>
```

The /code/<project> page ranks modules by node count; pick the
top 5-8 most-populated modules as the project's structural spine.

## Step 3 - Pull memory_project notes

```bash
mnemo query "<project_key> architecture" --k 8 --project <project_key>
```

`memory_project` nodes often capture the team's running notes on
how things fit together. Surface relevant ones.

## Step 4 - Synthesize

Write a paragraph (~150-300 words) covering:

1. **Goal.** One sentence on what the project is. Lift from the
   most-cited plan_doc.
2. **Shape.** 3-5 bullets on the structural spine (one per top
   module).
3. **Decisions.** 2-4 bullets on key trade-offs / patterns
   pulled from plan_doc + memory_project.
4. **Lessons learned.** Optional. Pull memory_feedback nodes if
   the user asks "what went wrong" / "what would you do
   differently".

Every claim cites a node via `[mnemo:<node_id>]`. If you're
making a claim with no citation, label it "inference" so the
user can tell it apart from documented design.

## Step 5 - Suggest follow-ups

- "Show me the call graph of `<X>`" -> chain into `mnemo:trace-call`.
- "How does the API map to the frontend?" -> chain into
  `mnemo:trace-route` or open the sitemap.
- "Why is `<function>` here specifically?" -> chain into
  `mnemo:why-is-this-here`.
