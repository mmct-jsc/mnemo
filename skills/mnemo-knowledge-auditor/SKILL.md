---
name: mnemo-knowledge-auditor
description: Use when the user wants to audit the mnemo knowledge graph for structural issues -- stale entries, duplicates, broken citations, contradictions, semantic orphans. Runs the deterministic auditor + groups findings by severity + proposes concrete refactor actions using existing mnemo_update_node / mnemo_delete_node primitives. v5.12.0 Phase 1 deterministic; v5.13.0 Phase 2a LLM-augmented contradictions; v5.14.0 Phase 2b LLM-augmented semantic_orphans.
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
- **contradictions** (v5.13.0): within-type pairs in the cosine
  0.5-0.85 band where at least one body contains a negation pattern
  (`do not`, `never`, `deprecated`, `removed`, `instead of`, ...).
  Default severity is `candidate` — the user reviews. With
  `MNEMO_ANALYZE_LLM_JUDGE=1` + `ANTHROPIC_API_KEY` set, candidates
  are escalated to Claude for binary confirmation: confirmed pairs
  become severity `high`, rejected pairs are dropped.
- **semantic_orphans** (v5.14.0): per-node concept extraction. Three
  regex patterns: CamelCase (`MQTTBridge`, `RetryHandler`), snake_case
  with 2+ underscores or length ≥ 12 (`son_tinh_ai`,
  `petrolimex_detection_model`), and ALL_CAPS with at least 1
  underscore (`MAX_RETRIES`, `DUPLICATE_COSINE_THRESHOLD`). For each
  extracted concept, the auditor checks every OTHER node's `name`
  and `description` (NOT body — a body mention is a reference, not a
  definition) for a case-insensitive substring match. Concepts with
  no defining node surface as candidates. Default severity is
  `candidate`. With `MNEMO_ANALYZE_LLM_JUDGE=1` +
  `ANTHROPIC_API_KEY` set, candidates are escalated to Claude:
  project-specific terms → severity `high`, common terms (Redis,
  JSON, etc.) → dropped.

## Phase 2 — Group by severity

Display findings ordered **high → medium → candidate → low**.
Within each tier, group by type. The user should see the most
urgent broken-citation + LLM-confirmed contradiction findings
first, then duplicate consolidation candidates, then unconfirmed
contradiction candidates for review, then the already-marked
stale entries last.

The `candidate` severity is new in v5.13.0 — it sits between
`high` and `medium`. A candidate contradiction is a deterministic
finding waiting for human judgement (or LLM judge confirmation if
the opt-in flag is set).

## Phase 3 — Propose actions (NEVER auto-apply)

For each finding, propose ONE concrete action using existing
mnemo primitives. The user copies the proposal and runs it manually
(or accepts it via the chat companion's confirm UI).

### Auto-proposed actions (v5.15.0)

If you call `mnemo_analyze(propose_actions=true)` AND the daemon has
`MNEMO_ANALYZE_PROPOSE_ACTIONS=1` + `ANTHROPIC_API_KEY` set, each
high/medium finding arrives with an `action` field already populated
by the daemon's LLM proposer:

```
"action": {
  "kind": "merge" | "supersede" | "delete" | "create_definition"
        | "add_reconciliation_note" | "fix_citation" | "none",
  "primitive": "mnemo_update_node" | "mnemo_delete_node"
        | "mnemo_create_node" | null,
  "target_node_id": "<id>" | null,
  "args_hint": { ...suggested kwargs... },
  "rationale": "<why>"
}
```

When the `action` field is present, USE IT as the basis for your
proposal rather than re-deriving from scratch — but still SHOW the
user the action + rationale and let them decide. The proposer is
severity-gated (high/medium only) and hard-capped per audit;
`summary._refactor_actions_skipped` reports how many eligible
findings were left unenriched by the cap. If `action.kind` is
`"none"` (the proposer declined or hit an error), fall back to the
per-type manual proposal templates below.

When `propose_actions` is off (the default), every finding has
`action: null` and you derive proposals manually as below.

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

### For `semantic_orphans` findings:

- The node references a concept (CamelCase / snake_case / ALL_CAPS)
  that no other node in the corpus defines (no substring match in
  any other node's `name` or `description`).
- Propose ONE of:
  - **Create a definition node**: prompt the user to run
    `mnemo_create_node` with `type="memory_reference"` (or the
    domain-appropriate type), `name=<concept>`, and a description
    + body explaining what the concept is. Cite the source node's
    body excerpt that motivated the new definition.
  - **Add the definition to an existing node**: if a sibling node
    is the natural place for the definition, propose
    `mnemo_update_node(sibling_id, description=description +
    " Defines <concept>: <one-line definition>")`.
  - **Remove the reference**: if the concept is incidental and
    doesn't warrant a definition, the user can edit the source's
    body to remove or contextualize the reference.
- The `concept` field on each finding identifies the orphaned term.
- For `candidate` severity (deterministic-only), explicitly flag
  that the user should verify the concept needs a definition (it
  may be a common term that doesn't); for `high` severity
  (LLM-confirmed), include the judge's rationale if available.

### For `contradictions` findings:

- The two nodes carry opposing prescriptions on the same topic
  (Vietnamese-law rules with mutually-exclusive exceptions;
  internal docs with "use Redis" vs "do not add Redis"; etc.).
- Propose ONE of:
  - **Mark one superseded**: `mnemo_update_node(older_id,
    description=description + " SUPERSEDED by newer_id")` if one
    is clearly the more recent / authoritative.
  - **Add a reconciliation note**: edit the older or canonical
    node's body to explicitly cite the contradiction +
    explain the scope distinction (when both are valid in
    different contexts).
  - **Delete the deprecated one**: `mnemo_delete_node(older_id)`
    if the older entry is fully superseded.
- Always include BOTH bodies in the report so the user sees the
  conflict before deciding.
- For `candidate` severity (unconfirmed), explicitly flag that
  the user should verify the contradiction is real before
  acting; for `high` severity (LLM-confirmed), include the
  judge's rationale if available.

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

## Domain lenses (v5.16.0+)

The five detectors above are domain-AGNOSTIC. A **domain lens**
(`mnemo_analyze(lens=...)`) runs a suite of domain-SPECIFIC
detectors INSTEAD of the agnostic ones (a lens replaces, not adds —
running agnostic detectors on a code corpus floods).

### `lens="code"` (shipped v5.16.0)

- **dead_code**: PRIVATE (`_`-prefixed, non-dunder) `code_function`
  / `code_method` nodes with ZERO inbound `calls` edges, excluding
  test entry points. Default severity `candidate`. With
  `MNEMO_ANALYZE_LLM_JUDGE=1` + `ANTHROPIC_API_KEY`, each candidate
  is graded: genuinely-dead → `high`; reached dynamically
  (dispatch table / getattr / decorator / framework hook) →
  dropped.
- **Workflow**: call `mnemo_analyze(lens="code")`. For each
  `dead_code` finding, propose `mnemo_delete_node(node_id)` (or, in
  the source, deleting the function) for confirmed-dead `high`
  findings; for `candidate` findings, explicitly flag that the user
  should verify it isn't reached dynamically before deleting. NEVER
  delete automatically.
- Only PRIVATE symbols are flagged — public dead code needs
  cross-file/external/dynamic call resolution mnemo doesn't have, so
  flagging public symbols would flood with false positives.

### Future lenses (later releases)

- `lens=vietnamese-law`: hierarchy violations + missing exception
  cross-refs in a legal corpus.
- `lens=research-notes`: un-cited claims, hypothesis drift.
- Additional `code` detectors (`cyclic_imports`, `orphan_modules`)
  land when a corpus exercises them.

To discover valid lenses programmatically, the analyzer exports
`KNOWN_LENSES`; an unknown lens runs no detectors (returns empty).
