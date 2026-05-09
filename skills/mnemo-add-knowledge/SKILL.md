---
name: mnemo-add-knowledge
description: Use when capturing a new insight, decision, or lesson into mnemo memory. Walks through novelty check -> categorize -> write -> link -> reindex so the entry surfaces correctly for future sessions.
---

# Add Knowledge

**Type:** flexible (5 phases, can collapse trivially when the entry is
clearly novel and standalone).

The point of this skill: every memory entry is reviewed for **novelty**
before being added, classified into the right **type**, written with a
**Why:** and **How to apply:** structure so future-you can act on it, and
linked to related nodes so retrieval surfaces it from adjacent queries.

## Phase 1 - Novelty check

Before writing, query mnemo for similar entries:

```bash
mnemo query "<one-line description of the insight>" --k 5
```

Read the top hits.

- If a hit is the **same insight, with the same scope** (score > ~0.7 and
  type matches): **don't add a duplicate**. Either:
  - Update the existing entry, or
  - If the existing one is wrong/outdated, write a new entry and add a
    `supersedes` edge from new -> old.
- If a hit is **adjacent** (related but distinct): note its `[mnemo:<id>]`
  for the link phase.
- If nothing close came back: this is genuinely new; proceed.

## Phase 2 - Categorize

Pick exactly one type:

| Type | When to use |
|---|---|
| `user` | User profile, preferences, role, communication style |
| `feedback` | Corrections or validated approaches the user gave (rules of engagement) |
| `project` | Project facts, decisions, infrastructure, RCAs, lessons specific to a project |
| `reference` | Pointers to external systems (Linear board, dashboard URL, runbook location) |

If you can't decide, the rule of thumb is:
- "How should we do X?" -> `feedback`
- "What is true about X?" -> `project`
- "Where is X?" -> `reference`
- "Who is X?" -> `user`

## Phase 3 - Write the entry

Use the standard frontmatter format:

```markdown
---
name: <slug-style-name>
description: <one-line, action-oriented>
type: <user|feedback|project|reference>
---

<Lead with the rule, fact, or decision in one sentence.>

**Why:** <the motivation - usually a past incident or a strong preference.
This is what lets future Claude judge edge cases instead of blindly
following the rule.>

**How to apply:** <when this kicks in, what to actually do>
```

For `project` and `feedback` types, **always** include the `Why:` and
`How to apply:` lines. They are non-negotiable - a memory without them
becomes uninterpretable in 6 months.

Write to:
- Project memory: `~/.claude/projects/<project-key>/memory/<type>_<name>.md`
- Global: append a section to `~/.claude/CLAUDE.md` (one big file, not a new file)

Don't dump full memory bodies into the user's chat - the entry's `body`
will be retrievable later.

## Phase 4 - Link to related nodes

If Phase 1 found adjacent nodes, declare the relationships in the new
entry's frontmatter:

```yaml
appliesTo:
  - <other_node_name_or_id>
supersedes:
  - <old_node_id>
derivedFrom:
  - <source_node>
```

mnemo's graph layer reads these on next reindex and adds the edges.
Co-occurrence edges are learned automatically; you don't need to declare them.

## Phase 5 - Reindex

The PostToolUse hook will reindex automatically when you write into a
known memory directory. If you wrote elsewhere, run:

```bash
mnemo reindex
```

Confirm the entry surfaces:

```bash
mnemo query "<a phrase from the body>" --k 3
```

The new entry should appear, ideally as the top hit.

## After: update the project's MEMORY.md index

If the project keeps a `MEMORY.md` index, append a one-line entry:

```markdown
- [<name>](<filename>.md) - <one-line description>
```

Keep `MEMORY.md` short - one line per memory file, no body content.
