---
name: mnemo-explore-codebase
description: Use when you need to orient in an unfamiliar codebase indexed via `mnemo source add --kind code_repo`. Pulls the project's most-connected functions, route surface, and any memory_feedback notes to give a shape-of-the-codebase summary in under a minute.
---

# Explore codebase

**Type:** flexible. Adapt to the size of the repo and the depth the
caller asked for.

When the user says "tell me about this codebase" / "give me a tour
of `<project>`" / "where do I start in this repo", run the
following flow.

## Step 1 - Resolve the project

```bash
# Active project tells us where to look first.
mnemo query "active project" --k 0    # via the daemon directly:
curl http://127.0.0.1:7373/v1/projects/active
```

If the active project doesn't match the repo the user asked
about, ask them: which project_key did they index it under? Then:

```bash
curl http://127.0.0.1:7373/v1/projects/known
```

returns the list -- match by substring on the path.

## Step 2 - Pull the project overview

```bash
mnemo query "<project_key> overview" --k 8 --project <project_key>
```

The /code UI mirrors this: <http://127.0.0.1:7373/code/<project_key>>
shows per-type counts (modules / functions / classes / methods /
routes / components) and the 25 most-connected functions ranked by
call-graph degree. Surface this as the starting shape.

## Step 3 - Surface the route map

If the project is a service, the routes are the user's mental
model. Pull them:

```bash
mnemo query "routes" --project <project_key> --k 15
```

Or open the cross-stack sitemap:
<http://127.0.0.1:7373/code/<project_key>/sitemap>

## Step 4 - Surface lessons learned

If the project has `memory_feedback` nodes scoped to it, those are
the most useful one-shot context for a newcomer:

```bash
mnemo query "lessons" --project <project_key> --k 10
```

Look for nodes whose `name` starts with `feedback_`. Cite each as
`[mnemo:<node_id>]`.

## Step 5 - Optional: drill into one function

If the user has a follow-up like "what does X do?", open the
function detail page:

<http://127.0.0.1:7373/code/<project_key>/function/<node_id>>

It shows the body, callers, callees, and (when present) the
commits that touched it. That's the natural place to chain into
`mnemo:trace-call` for a deeper walk.

## Output format

Return a short orientation report (~200 words) covering:

- One sentence on shape (lang, top files, count).
- The 3-5 most-connected functions, each with a one-line gloss.
- The route surface if applicable.
- Any lessons-learned citations.
- A "follow-ups" line pointing at the natural next skill
  (`mnemo:trace-call`, `mnemo:why-is-this-here`, etc.).

Cite everything with `[mnemo:<node_id>]`.
