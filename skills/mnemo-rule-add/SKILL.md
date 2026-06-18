---
name: mnemo-rule-add
description: Use when the user wants to add a governance RULE to mnemo -- a coding constraint, workflow gate, spec, mandatory-verification, or code-review requirement that mnemo should surface prescriptively and (optionally) enforce. Distinct from mnemo-add-knowledge: a rule is binding ("MUST/MUST_NOT"), context-scoped, and can block a tool call; knowledge is advisory context. Walks: novelty check -> modality -> scope/triggers -> enforcement -> write -> reindex.
---

# Add a governance rule

A **rule** is a prescriptive, enforceable constraint stored as a `type: rule`
memory file. Unlike a `feedback` note (advisory), a rule is **binding**: it
surfaces in the `## Active rules` block at the right moment (by file / task /
tool), and -- if `enforcement: block` -- can deny a tool call or block session
end until a mandatory step is *provably* satisfied (evidence-based).

**Type:** flexible (collapse trivially for an obvious rule).

## Phase 1 - Novelty check
Query for an existing rule covering the same constraint:
```bash
mnemo query "<one-line of the constraint>" --k 5
```
If a `rule` node already covers it, update that file instead of duplicating.

## Phase 2 - Decide the rule fields
Establish, with the user, each field (defaults shown):

- **modality** (`SHOULD`): `MUST` | `MUST_NOT` | `SHOULD`. MUST/MUST_NOT are
  *mandatory* -- they always surface and bypass the injection budget.
- **enforcement** (`inform`): `inform` | `warn` | `require-ack` | `block`.
  Orthogonal to modality. Start at `warn`; only set `block` once the triggers
  are proven precise (a false block erodes trust). `block` requires a
  satisfied `requires_step` (evidence) before the matching tool call.
- **applies_to** (the context triggers; omit for a universal rule, pair with
  `base: true`): `glob` (file paths), `intent` (task tags like `refactor`,
  `design`, `feedback-recall`), `tool` + `tool_arg_match` (e.g. Bash +
  `"git commit"`). A rule applies if ANY declared trigger matches.
- **verify** (for mandatory-verification rules): `command` + `expect_exit`.
  mnemo captures the command's *real* exit code as evidence -- the agent
  cannot assert it ran.
- **requires_step** (for gate rules): `review` | `verify` | `ack` -- the step
  that must be evidenced before a `block` rule allows the tool call.

## Phase 3 - Write the rule file
One rule per file under a memory dir (e.g. `rules/<id>.md`). Scaffold:

```yaml
---
name: <kebab-name>
type: rule
base: true                       # if it applies to every project
description: <one-line MUST/MUST_NOT statement>
rule:
  id: rule.<area>.<thing>
  modality: MUST_NOT
  enforcement: warn
  applies_to:
    glob: ["**/*.py"]
    tool: ["Bash"]
    tool_arg_match: "git commit"
  verify: { command: "uv run ruff check .", expect_exit: 0 }
  requires_step: verify
  links: { refines: rule.<parent> }
---
<The rule text, the rationale, and the escape hatch (how to override).>
```

Keep the body short and prescriptive: state the rule, why it exists, and the
documented bypass (`MNEMO_GOVERNANCE_BYPASS=1` disables blocking globally).

## Phase 4 - Reindex
```bash
mnemo reindex
```
Then confirm it parsed: `mnemo query "<rule topic>"` should surface it under
`[rule]`, and `mnemo_get_node <id>` shows the `rule:` block.

## Cross-cutting
- A bad/unknown modality or enforcement value **fails open** (parsed as
  advisory `SHOULD`/`inform`), never breaking retrieval -- but get the values
  right so the rule actually binds.
- Prefer `warn` over `block` until the rule's triggers are battle-tested.
- Rules are the ONE thing mnemo injects prescriptively; keep the corpus small
  and high-signal (a flood of MUSTs is noise).
