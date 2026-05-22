---
description: Turn a vague coding prompt into a paste-ready prompt block with typed Graph-RAG context, citations, and anti-pattern warnings
argument-hint: <vague coding prompt to architect>
---

Architect a polished, context-rich prompt for the following ask:

<user-prompt>
$ARGUMENTS
</user-prompt>

Follow the **`mnemo:prompt-architect`** skill (full definition at
`skills/mnemo-prompt-architect/SKILL.md`). The skill drives a
four-phase flow:

**Phase 1 — score confidence.** Run:

```bash
mnemo query "$ARGUMENTS" --json --budget 2000 --k 12 --exclude-local-only
```

Note the `top_hit_score` (best hit's score), `hit_density` (count of
hits scoring > 0.4), and structural features present in the prompt
(file path / line number / function name / test name / env name).
HIGH confidence iff `top_hit_score >= 0.55` AND
(`hit_density >= 3` OR structural features `>= 1`). Otherwise ask
ONE clarifying question and stop. Never exceed two clarifying turns.

**Phase 2 — expand retrieval (HIGH confidence only).** Re-query with
a wider budget (`--budget 4000 --k 20 --exclude-local-only`) and
walk provenance with `mnemo node show <id>` on the top 2-3 hits.
If the prompt names a specific function, run
`mnemo query "calls of <function>" --json --k 8 --exclude-local-only`.

**Phase 3 — emit the sectioned block.** Output EXACTLY this shape
inside one markdown code fence:

```markdown
## Problem
[reframed; one or two sentences]

## Context
[2-4 short paragraphs, each citing a memory node with [mnemo:<id>]
tags. Lead with the strongest hit. Surface assumptions explicitly
when confidence is medium ("Assuming dev environment based on recent
commits [mnemo:abc]").]

## Files
- `path/to/file.py:line_start-line_end` (one-line role)
- `path/to/other.py:line_start-line_end` (one-line role)

## Acceptance criteria
- One sentence per checkable outcome.
- Pull from existing test names or specs in the retrieved context.

## Anti-patterns
- "Do NOT do X" with [mnemo:<id>] explaining why.
- Skip this section entirely if no anti-pattern is in the retrieved
  context. Do not invent warnings.

## Prompt
[Final paste-ready prompt the user copies into their IDE. Reference
the files + criteria above by name, but write the prompt itself in
self-contained natural language so the host LLM can act on the
prompt alone when the user picks "prompt only" copy.]
```

**Phase 4 — citation discipline.** Use the literal tag form
`[mnemo:<node_id>]`. One strong citation per claim; three citations
in one sentence reads as defensive. Never cite a node you didn't
actually retrieve.

## Provider-neutrality

This slash command is a Claude Code surface, but the underlying
`mnemo:prompt-architect` workflow is shipped to every MCP host:

- **Cursor / Claude Desktop / Continue / Windsurf / Zed / Gemini CLI
  / OpenAI Agents SDK** — invoke via `mnemo_run_skill("mnemo-prompt-architect")`
  (see `docs/integrations/` for the 5-minute mount of each host).
- **mnemo dock** (any page) — click the architect pill before
  pressing send; chat.js sets `use_skill='mnemo-prompt-architect'`
  on the POST body so the daemon pre-loads the skill before the
  model sees the user text.
- **`/chat` page** — same architect pill, same skill.

All four surfaces converge on `skills/mnemo-prompt-architect/SKILL.md`.

## `exclude_local_only=true` is non-negotiable

The architected prompt is paste-bound to a foreign LLM. Nodes
flagged `local_only` (under `docs/_private/`, with frontmatter
`local_only: true`, or body starting with `[LOCAL ONLY]`) must
never reach the output. The `--exclude-local-only` flag on
`mnemo query` (added v5.8.0) mirrors the MCP / dock contract.

## Failure modes

- **Zero hits retrieved** — say so plainly in Phase 1; ask the
  user to add detail. Do not fabricate context.
- **Retrieval dominated by `local_only_excluded` hits** — the
  prompt-architect cannot do its job on confidential-only context.
  Tell the user the architected prompt is unavailable for this
  prompt and recommend moving the work to mnemo's `/chat` (v3
  companion path that CAN consult local_only nodes).
- **User asked for a memory write, not a prompt** — redirect to
  the `mnemo:doc` skill via a one-line note; do not architect.
