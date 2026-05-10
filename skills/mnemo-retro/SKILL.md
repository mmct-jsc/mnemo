---
name: mnemo-retro
description: Use when finishing a working session and you need to extract durable lessons into mnemo memory. Sweeps the recent audit log, file edits, and design docs; surfaces 0-N candidate memory entries; lets the user accept, edit, or reject each; writes accepted entries as memory files and reindexes.
---

# Retro

**Type:** flexible. Phases are a sequence, not a strict gate. Skip
phases that don't apply (e.g. no recent commits = phase 1's git
sweep is a no-op).

The point of this skill is to convert tacit knowledge from a
just-finished session into mnemo's typed memory format **before** the
context is forgotten. Run it at end-of-session, end-of-feature, or
post-incident (after the `mnemo:incident` skill's RCA phase).

## Phase 1 - Sweep

**Goal:** Reconstruct what happened in the last working window.

1. Pull the recent audit log:
   ```bash
   mnemo audit --limit 50
   ```
   Look at the last 1-N hours of queries -- they show what the user
   was thinking about and which nodes mnemo found relevant.
2. Pull recent file edits:
   ```bash
   git log --since="6 hours ago" --stat
   git diff --stat HEAD~5..HEAD  # or whatever range looks right
   ```
3. Identify any new `docs/plans/*` artifacts (designs that landed) or
   `docs/incidents/*` (incidents that closed).

Frame the window as "since when" with the user. They may want a
specific commit range or "this morning's session".

## Phase 2 - Propose candidates

**Goal:** Surface 0-N memory entry candidates.

For each pattern below, propose a candidate entry. Each candidate has:

- **type**: `memory_user` | `memory_feedback` | `memory_project` | `memory_reference`
- **name**: snake-case slug (e.g. `starlette_middleware_order`)
- **description**: 1-line summary that retrieval can rank
- **body**: the actual durable content (with examples / pitfalls)
- **base** (optional): true if the lesson applies across all projects
- **confidence**: high / medium / low

Patterns to look for:

| Pattern | Type to propose |
|---|---|
| "I keep forgetting that X" | memory_user (preference / pattern) |
| Bug we fixed + the *category* it belongs to | memory_feedback (gotcha) |
| Architecture decision that's now stable | memory_project |
| External API quirk we worked around | memory_reference |
| Hard-rule the user enforced more than once | memory_user with base: true |

Don't shotgun. **Quality over quantity.** A retro that proposes 8
weak entries is worse than one that proposes 2 strong ones.

## Phase 3 - Triage

For each candidate, ask the user:

- **Accept** (write as-is)
- **Edit** (revise name / description / body / type / base flag)
- **Reject** (note the reason; we won't propose this again)

When a candidate is rejected, log the reason in the audit so the same
pattern doesn't get re-proposed tomorrow. (Future: a "rejected"
memory.json the retro skill consults next session. For now, just say
so to the user.)

## Phase 4 - Write + reindex

For each accepted candidate:

1. Pick the right memory dir:
   - Project-specific: `~/.claude/projects/<project_key>/memory/`
   - User-global / BASE: `~/.claude/projects/<project_key>/memory/`
     with frontmatter `base: true` (BASE flag bypasses isolation)
2. Write the file with frontmatter:
   ```markdown
   ---
   name: <name>
   description: <description>
   type: <type>
   base: <true|false>
   ---
   <body>
   ```
3. Run `mnemo reindex` (or just let the watcher pick it up).

Confirm with `mnemo query "<key phrase from the new entry>"` and check
the new node lands in the top hits.

## Done criteria

- Each accepted candidate is on disk and indexed.
- The user has explicitly chosen accept / edit / reject for every
  proposed candidate.
- A rejection list is captured (in the chat, at minimum) so the next
  retro doesn't re-propose the same things.

## Cross-cutting

- Default to PROJECT scope, not BASE. BASE is for the rare lesson
  that genuinely applies to every codebase the user touches.
- Skill should read more like a triage queue than an interrogation.
  If a candidate is obviously good ("yes, accept as-is"), don't
  belabor it.
- Skip the "what did we accomplish today" summary -- that's a slack
  message, not memory. Memory is for **lessons that change future
  behavior**.
