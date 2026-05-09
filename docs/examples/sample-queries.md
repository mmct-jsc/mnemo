# Sample queries

Real queries against a developer's `~/.claude/` memory (38 nodes, 7 sources)
captured during the smoke run. These illustrate what mnemo does in
practice.

## Layout of a hit

Every retrieved hit comes back as:

```
[mnemo:<id>] [<type>] <name>: <description>
  <body snippet, if budget had room>
```

The `[mnemo:<id>]` citation is the durable handle; you can resolve it via
`mnemo node show <id>` or open `http://127.0.0.1:7373/node/<id>`.

## Query 1: "no co-author trailer in commit messages"

```
intent: ['none']  tokens_used: 253  hits: 5

  s=0.540  [memory_feedback]   feedback-commit-style                 User wants commits without Co-Authored-By trailers
  s=0.492  [memory_feedback]   Commit author + cadence preference    User wants frequent commits during development with...
  s=0.374  [memory_project]    code-review-findings-2026-04-24       Code review snapshot 2026-04-24 - P0/P1 issues to fix
  s=0.371  [memory_project]    CSL-MT project context                Repository purpose, target event, and stakeholders for...
  s=0.368  [memory_project]    vm-sdb-multi-writer-fix               Multi-writer VMDK corruption - diagnosed and FIXED
```

Note: vector search alone surfaces the right top hit even though the
intent classifier didn't fire (it's looking for "always" / "never" /
"prefer", not "no").

## Query 2: "should I always prefer terse responses"

```
intent: ['feedback-recall']  tokens_used: 246  hits: 5

  s=0.348  [memory_feedback]   feedback-deployment-style             User prefers minimal config, autonomous execution
  s=0.347  [memory_feedback]   Asset Generation Strategy             pixelab.ai assets must be optimized close to design
  s=0.326  [memory_feedback]   feedback-commit-style                 User wants commits without Co-Authored-By trailers
  s=0.317  [memory_feedback]   Always Read Docs Before Implementing  User requires consuming all related design docs
  s=0.312  [memory_feedback]   Commit on each phase                  User wants every completed implementation phase to be
```

The `feedback-recall` intent fires (because of "always prefer"). All five
top hits are `memory_feedback` type - intent-driven type priority is
working as designed.

## Query 3: "MQTT broker authentication credentials"

```
intent: ['none']  tokens_used: 116  hits: 5

  s=0.483  [project_doc]       CLAUDE                                Global memory
  s=0.436  [memory_project]    code-review-findings-2026-04-24       Code review snapshot - P0/P1 issues incl. EMQX hardcoded
  s=0.430  [memory_project]    EMQX restored as Swarm service        Standalone EMQX container disappeared during a debug
  s=0.400  [memory_project]    v1-multitenant-saas-roadmap           Strategic direction agreed - convert AIBox from
  s=0.392  [memory_project]    Phase 18a - Snapshot via HTTPS        Edge-to-cloud HTTPS snapshot upload replacing base64
```

Surfaces both the global memory (which mentions MQTT broker quirks) and
the specific RCA where EMQX was restored. Cross-project transfer in
action.

## Query 4: "godot child timer cinematic safety"

```
intent: ['none']  tokens_used: 131  hits: 5

  s=0.444  [project_doc]       CLAUDE                                Global memory
  s=0.362  [memory_project]    Alert correlation key gotchas         Operators commonly conflate SLA with dedup
  s=0.360  [memory_project]    Phase 18b - Per-event MP4 clip        End-to-end edge -> cloud video clip pipeline
  s=0.318  [memory_project]    production-infrastructure             3-VM production cluster - IPs, services
  s=0.317  [memory_project]    Ken Project Overview                  Narrative information-puzzle game with LLM
```

The global `CLAUDE.md` chunks dominate because it has multiple Godot
lessons. Note: **all five hits are distinct nodes** - the per-node
deduplication in retrieval prevents the same node from appearing
multiple times via its different chunks.

## Query 5: "where do we keep deployment files"

```
intent: ['none']  tokens_used: 273  hits: 5

  s=0.429  [memory_project]    deployment-files-no-san               Key deployment files for the no-SAN swarm deployment
  s=0.408  [memory_project]    Phase 18a - Snapshot via HTTPS        Edge-to-cloud HTTPS snapshot upload replacing base64
  s=0.405  [memory_project]    Phase 18b - Per-event MP4 clip        End-to-end edge -> cloud video clip pipeline
  s=0.358  [memory_feedback]   feedback-deployment-style             User prefers minimal config, autonomous execution
  s=0.341  [memory_project]    v1-multitenant-saas-roadmap           Strategic direction agreed - convert AIBox from
```

Top hit is the literal `deployment-files-no-san` node - exact match. The
4th hit (a `feedback` node about deployment style) is also relevant; it
surfaced via type priority + cosine similarity.

## What this shows

- **Vector search** carries most of the weight on lexical matches.
- **Intent classification** kicks in for shape-based queries ("always",
  "where is", "design") and bias toward the right node types.
- **Per-node deduplication** prevents long-bodied nodes from dominating.
- **Token budget** keeps results tight - 116-273 tokens here, never near
  the 800 cap.
- **Citations** are always present so future tools can resolve back.

## Re-running

```bash
mnemo reindex                       # ensure index is fresh
mnemo query "<your question>"       # human-readable
mnemo query "<your question>" --json --k 10 --budget 1500   # raw
```

For interactive exploration, open the UI at `http://127.0.0.1:7373/` (after
`mnemo daemon start`).
