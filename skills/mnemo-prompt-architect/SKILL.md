---
name: mnemo-prompt-architect
description: Use when the user types a short or vague coding prompt into Mnem and wants a polished, context-rich prompt block they can paste into any IDE AI agent (Cursor, Claude Code, Continue, Copilot). Analyzes the prompt against the typed Graph-RAG memory + code graph, surfaces the missing context, and emits a sectioned markdown output with citations + file refs + anti-pattern warnings.
---

# mnemo:prompt-architect - turn a vague prompt into a paste-ready one

**Type:** flexible (analyze -> assemble -> emit).

The v5 dock surface invokes this skill on every architect-mode
message. Your job: take the user's raw input and emit ONE sectioned
markdown block the user copies and pastes into a foreign LLM. The
wedge is the typed Graph-RAG context Cursor / Continue / Copilot
cannot see -- so cite specific memory nodes, name specific files,
and warn against anti-patterns the user might otherwise re-derive.

## Phase 1 - Score the prompt's confidence

Decide single-turn vs. multi-turn BEFORE any heavy retrieval. The
confidence signal is mostly retrieval-derived:

- Run `mnemo_query(prompt, limit=12, max_tokens=2000,
  exclude_local_only=True)`. Note `top_hit_score` (best score in
  the result set) and `hit_density` (count of hits scoring > 0.4).
- Note **structural features** present in the prompt: a file path,
  a line number, a function name, a test name, an env name.

Score (informal):

- `top_hit_score >= 0.55` AND (`hit_density >= 3` OR `structural
  features >= 1`) -> HIGH confidence -> single-turn.
- Otherwise -> LOW confidence -> ask ONE clarifying question and
  stop. Pick from: which file / which test / which environment /
  which error message / which subsystem. Re-score after the answer.

Never exceed two clarifying turns; if still unclear, emit a draft
output anyway with the gaps called out in the Assumptions block at
the top of the Context section.

## Phase 2 - Pull deep context (only after HIGH confidence)

Now expand retrieval to assemble the architected prompt:

- `mnemo_query(prompt, limit=20, max_tokens=4000,
  exclude_local_only=True)` -- the analysis budget can be larger
  than the OUTPUT budget; the architected prompt prunes back to
  what the host actually needs.
- `mnemo_traverse(node_id, max_hops=2)` on the top 2-3 hits to walk
  provenance (decisions / supersession / linked code).
- `mnemo_search_by_type("code_function", name_glob=...)` if the
  prompt mentions a specific function name.
- `mnemo_get_code_lines(source_path, start, end)` to lift the
  exact lines the architected prompt will reference.

ALWAYS pass `exclude_local_only=True` on the retrieval calls in
this skill. The architected prompt is paste-bound to a foreign
LLM; nodes flagged ``local_only`` (docs/_private/, frontmatter
``local_only: true``, body starting with ``[LOCAL ONLY]``) must
never reach the output. The retrieval result's
``local_only_excluded`` count tells the dock how many were
filtered for the pre-emit warning.

## Phase 3 - Emit the sectioned block

Emit EXACTLY this shape, with three backticks opening + closing a
single markdown code-fence. The dock's copy buttons split the
block into "prompt only" vs "with context" by reading the section
headers:

````markdown
## Problem
[reframed problem statement; one or two sentences max]

## Context
[2-4 short paragraphs, each citing a memory node with `[mnemo:<id>]`
tags. Lead with the strongest hit. Surface assumptions explicitly
("Assuming dev environment based on recent commits [mnemo:abc]")
if confidence is medium.]

## Files
- `path/to/file.py:line_start-line_end` (one-line role)
- `path/to/other.py:line_start-line_end` (one-line role)

## Acceptance criteria
- One sentence per criterion. State checkable outcomes, not actions.
- Pull from existing test names or specs in the retrieved context.

## Anti-patterns
- "Do NOT do X" with a citation: [mnemo:<id>] explains why.
- Skip this section entirely when no anti-pattern is in the retrieved
  context. Don't invent warnings.

## Prompt
[The final paste-ready prompt the user copies. Reference the files
+ criteria above by name, but write the prompt itself in
self-contained natural language so the host LLM can act on the
prompt alone when the user picks "prompt only" copy.]
````

## Phase 4 - Hand-off to the dock

The dock parses your sectioned output, renders it with two copy
buttons (`Copy prompt only` strips everything except `## Prompt`;
`Copy with context` takes the whole block), and surfaces the
pre-emit warning if `mnemo_query` returned a non-zero
`local_only_excluded` count.

Do NOT emit a `mnemo-draft` fence here -- that is `mnemo:doc`'s
contract; this skill never creates memory nodes. The user is the
sole writer of the output to disk (via paste into their IDE).

## Citation discipline

- Always use the literal tag form `[mnemo:<node_id>]` -- the dock
  resolves the click; the foreign LLM treats it as an opaque
  provenance marker.
- Cite at most one tag per claim. Three citations in one sentence
  reads as defensive; one strong citation reads as authoritative.
- Never cite a node you did not actually retrieve in Phase 1/2.

## Failure modes

- The retrieval returned zero hits -- say so plainly in Phase 1
  and ask the user to add detail. Do NOT fabricate context.
- The retrieval is dominated by `local_only_excluded` hits -- the
  prompt-architect cannot do its job on confidential-only context.
  Tell the user the architected prompt is unavailable for this
  prompt and recommend rephrasing or moving the work to /chat (the
  v3 companion path that CAN consult local_only nodes).
- The user asked for a memory write, not a prompt -- redirect to
  the `mnemo:doc` skill via a one-line note; do not architect.

## Reference

Local repo: `mnemo query` shows the same retrieval surface the skill
uses; the daemon binds to `127.0.0.1:7373` so the dock invokes this
skill via the standard `/v1/chat/.../message` SSE channel.
