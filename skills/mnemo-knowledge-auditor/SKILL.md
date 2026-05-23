---
name: mnemo-knowledge-auditor
description: Use when the user wants to audit the mnemo knowledge graph for structural issues -- stale entries, duplicates, broken citations. Runs the deterministic auditor + groups findings by severity + proposes concrete refactor actions using existing mnemo_update_node / mnemo_delete_node primitives. v5.12.0 Phase 1 of mnemo's Understanding arc; LLM-augmented detection lands in v5.13.0+.
---

# mnemo-knowledge-auditor — surface structural issues in the corpus

**Type:** rigid (audit -> group -> propose).

mnemo v1-v5 indexes + retrieves. v5.12.0+ adds an **understanding**
layer: the auditor walks the existing graph + surfaces three
structural issues that silently corrode any growing knowledge base
(internal docs, Vietnamese law, research notes, codebase, anything).

Your job: invoke the audit, group findings by severity, propose
concrete actions the user can take with existing primitives. You
NEVER auto-apply edits — Phase 1 anti-goal — the user reviews +
decides.

## Phase 1 — Run the audit

Call `mnemo_analyze` (no args) and capture the response. The shape
is:

```
{
  "ran_at": "<ISO timestamp>",
  "node_count_scanned": <int>,
  "findings": [
    {"type": "stale"|"duplicates"|"orphan_reference",
     "node_ids": [...], "description": "...", "severity": "low"|"medium"|"high"},
    ...
  ],
  "summary": {"stale": N, "duplicates": M, "orphan_references": K}
}
```

Detectors:

- **stale**: nodes whose body or description contains `SUPERSEDED`
  (case-insensitive). The user's own marker; informational.
- **duplicates**: pairs of same-type nodes with cosine similarity
  ≥ 0.95. Phase 1 covers `memory_*` and `plan_doc` / `project_doc` /
  `session_summary` types; code dedup is deferred to a domain lens
  in v5.14.0+.
- **orphan_reference**: nodes whose body cites `[mnemo:<id>]` for
  an `<id>` not in the graph. Broken citation = silent rot.

## Phase 2 — Group by severity

Display findings ordered **high → medium → low**. Within each tier,
group by type. The user should see the most urgent broken-citation
issues first, then duplicate consolidation candidates, then the
already-marked stale entries last.

## Phase 3 — Propose actions (NEVER auto-apply)

For each finding, propose ONE concrete action using existing
mnemo primitives. The user copies the proposal and runs it manually
(or accepts it via the chat companion's confirm UI).

### For `stale` findings:

- Propose: archive via `mnemo_update_node(node_id, frontmatter_patch={"archived": true})`,
  OR delete via `mnemo_delete_node(node_id)` if the entry is fully
  superseded.
- Surface the SUPERSEDING node (look for "SUPERSEDED by X" in the
  description) so the user knows which is canonical.

### For `duplicates` findings:

- Propose: merge bodies + delete one. Concretely:
  1. `mnemo_get_node(node_id_a)` + `mnemo_get_node(node_id_b)` to
     inspect both.
  2. Decide which is canonical (usually the longer / more recent).
  3. `mnemo_update_node(canonical_id, body=merged_body)`.
  4. `mnemo_delete_node(non_canonical_id)`.
- DO NOT propose deletion without showing both bodies first.

### For `orphan_reference` findings:

- Propose ONE of:
  - **Fix the citation**: `mnemo_update_node(source_id, body=...)`
    with the broken `[mnemo:gone]` replaced by a valid id or removed.
  - **Restore the target**: if the missing id should exist, prompt
    the user to recreate the node (cite the source body's context).
- Always include the surrounding body text so the user understands
  what claim the broken citation was supporting.

## Phase 4 — Emit the report

Format as a Markdown report:

```markdown
# Knowledge audit -- ran_at <ts>, scanned <N> nodes

## Summary
- High: K broken citations
- Medium: M duplicate pairs
- Low: S stale entries

## High severity (K)

### Broken citations
- [mnemo:<source_id>] -> missing [mnemo:<target>]
  **Action:** ...

## Medium severity (M)

### Duplicate pairs
- [mnemo:A] + [mnemo:B] (cosine X.XX)
  **Action:** ...

## Low severity (S)

### Stale entries
- [mnemo:X] -- marked SUPERSEDED by ...
  **Action:** ...
```

## Anti-goals (Phase 1)

- **NEVER call `mnemo_update_node` or `mnemo_delete_node` yourself.**
  The auditor surfaces; the user acts. Phase 4 (v5.15.0+) may add
  a confirm-then-apply mode behind an explicit user opt-in.
- **NEVER call `mnemo_analyze` more than once per session unless
  the user explicitly asks for a re-run.** The audit is the same
  read for the duration of a session; spamming it wastes tokens.
- **NEVER fabricate findings.** If `mnemo_analyze` returns an empty
  list, the answer is "no structural issues detected" — don't
  invent ones.
- **NEVER hide low-severity findings to "make the report shorter".**
  Show them all; the user decides what to ignore.

## Domain lenses (future v5.14.0+)

Phase 1 ships the three universal detectors above. Phase 3 adds
pluggable domain lenses:
- `lens=vietnamese-law`: detects hierarchy violations + missing
  exception cross-refs in a legal corpus.
- `lens=code`: dead-code detection across modules.
- `lens=research-notes`: un-cited claims, hypothesis drift.

Domain lenses are out of scope for this skill in v5.12.0.
