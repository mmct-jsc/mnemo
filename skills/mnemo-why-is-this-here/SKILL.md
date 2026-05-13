---
name: mnemo-why-is-this-here
description: Use when the user asks "why is this function/class here?" or "what motivated this code?". The decision-provenance walker. Walks code_function -> commit -> memory_feedback / plan_doc to surface the chain of decisions that produced the code, with one-line synthesis citing each.
---

# Why is this here

**Type:** rigid. The headline capability no other code-intelligence
tool has: code that remembers why it exists.

## Phase 1 - Identify the target

```bash
mnemo query "<function/class name>" --k 5 --project <project_key>
```

Find the `code_function` / `code_method` / `code_class` node the
user is asking about. Note its node id.

## Phase 2 - Walk references_function reverse

Pull the 5 most-recent commits that touched the function:

```bash
curl "http://127.0.0.1:7373/v1/edges?dst_id=<node_id>&relation=references_function"
```

Each edge points to a `commit` node. Order by the commit's
timestamp (in the commit node's frontmatter) descending.

> Note: commit ingestion is the v2.0 phase 9 step. If your store
> doesn't have commit nodes yet, fall back to git log directly.
> The `mnemo:why-is-this-here` chain still works once phase 9
> reindexes the repo's commits.

## Phase 3 - Walk motivated_by

For each commit, walk `motivated_by`:

```bash
curl "http://127.0.0.1:7373/v1/edges?src_id=<commit_id>&relation=motivated_by"
```

Each target is a `memory_feedback` / `plan_doc` / `memory_project`
node. Read its `description` + `body`.

## Phase 4 - Render

The format the design specifies:

```
auth.py::login (lines 42-58)

Most-recent material change: commit a1b2c3d (Alice, 2026-04-12)
  > "fix: short-circuit login on stale token (see retro 2026-04-10)"
  Motivated by: feedback_mqtt_auth_flake [mnemo:abc123]
    "Tokens flake under MQTT broker reprovision; short-circuit
    before broker check."

Earlier touches:
  - 11ac... (2026-03-30) implementation
  - 8842... (2026-02-14) renamed from `do_login`
    plan: 2026-02-10-auth-refactor.md [mnemo:def456]
```

## Phase 5 - Synthesis

One-paragraph synthesis at the bottom. Cite each motivating
node. The synthesis should explain "the function exists in its
current form because <X>; <Y> motivated the most recent change."

## Phase 6 - Suggest follow-ups

- "See the full retro?" -> open the cited memory_feedback node.
- "Show all commits touching this function?" -> expand phase 2
  beyond the 5-most-recent cap.
- "Show the blast radius if I change this?" -> chain into
  `mnemo:impact-analysis`.

## When provenance is empty

If a function has no `references_function` edges (phase 9 hasn't
ingested commits yet, or the commit history is shallow), fall
back to:

1. `git log -L` directly on the file:line range.
2. Pull `memory_feedback` / `plan_doc` nodes by query and
   manually correlate by timestamp.

Mark the output "(synthesized -- commit graph not yet ingested)"
so the user knows the chain is approximate.
