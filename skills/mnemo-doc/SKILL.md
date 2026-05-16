---
name: mnemo-doc
description: Use when the user asks Mnem (the v3 chat companion) to "draft a memory", "write this up", "save what we learned", or otherwise turn a finding into a durable mnemo memory node. The agent researches with the read tools, then emits the entry inside a ```mnemo-draft fenced block so the chat UI can render a one-click "Save as memory" button.
---

# mnemo:doc - draft a memory the user can one-click save

**Type:** flexible (research -> draft -> hand off to the UI).

The v3 chat UI watches the assistant's streamed text for a fenced
block tagged ` ```mnemo-draft `. It renders a **Save as memory**
button next to each such block; clicking it parses the frontmatter +
body, `POST`s to `/v1/nodes`, and triggers a memory-dir reindex so the
entry surfaces in future retrieval. Your job is only to produce a
well-formed draft fence -- never call a write tool yourself for this.

## Phase 1 - Research first

Use the safe read tools before drafting so the entry is novel and
accurate:

- `mnemo_query` for similar existing memories (avoid duplicates).
- `mnemo_get_node` / `mnemo_traverse` to ground specifics + find the
  nodes this entry should reference.

If a near-duplicate already exists, say so and propose updating it via
the (permission-gated) `mnemo_update_node` tool instead of drafting a
new one.

## Phase 2 - Emit the draft fence

Emit EXACTLY one fenced block per memory, opened with three backticks
and the language tag `mnemo-draft`. Inside: YAML frontmatter, then a
blank line, then the markdown body.

````
```mnemo-draft
---
name: feedback-mqtt-auth-flake
type: feedback
projectKey: D--Repository-edge-device
---
# MQTT auth flakes under broker reprovision

**Why:** ...

**How to apply:** ...
```
````

### Frontmatter contract

- `name` (required) - kebab-case, unique, matches the memory-entry
  convention (`feedback-...`, `reference-...`, `session-...`).
- `type` (required) - one of `user`, `feedback`, `project`,
  `reference`, `session_summary`, ... (the mnemo memory types).
- `projectKey` (optional) - omit for BASE/global knowledge; set it to
  scope the entry to one project.

### Body contract

Follow the house structure so future sessions can act on it:
a one-line title (`# ...`), then **Why:** (the durable reason) and
**How to apply:** (the concrete action). Cite related nodes inline as
`[mnemo:<id>]`.

## Phase 3 - Hand off

After the fence, add one sentence telling the user the **Save as
memory** button will persist it and reindex. Do not call
`mnemo_create_node` yourself for a doc-helper draft -- the UI button
(permission-gated on first use per project) is the save path so the
user stays in control.
