---
name: mnemo-query-knowledge
description: Use when you need to recall information from mnemo memory on demand (not via the auto-injection hook). Drives the intent classify -> hybrid retrieve -> compress -> cite flow exposed by the daemon.
---

# Query Knowledge

**Type:** rigid. The daemon already implements the algorithm; this skill
documents the contract so callers (you, Claude) interpret results
correctly.

## When to use

- Auto-injection is off and you need targeted recall.
- You want results outside the default `--budget 800 --k 5` shape.
- You want to control intent classification or project scoping explicitly.

## The pipeline

```
prompt --> intent classify --> vector top-k chunks
                            --> graph proximity from candidates
                            --> 5-term scoring (vector + graph + recency + type + project)
                            --> per-node dedup (best chunk wins)
                            --> top-k
                            --> compress to budget tokens
                            --> emit citations [mnemo:<id>]
```

## The CLI

Default invocation (what the auto-inject hook uses):

```bash
mnemo query "<prompt>" --json --budget 800 --k 5
```

Options:
- `--budget <N>` - max tokens in the result (default 800)
- `--k <N>` - max hits returned (default 5; 20 if calling from code)
- `--project <key>` - boost nodes under this project key
- `--json` - emit JSON instead of human text

## How to read the output

Each hit comes back as:

```json
{
  "node_id": "<id>",
  "type": "memory_feedback",
  "name": "<name>",
  "description": "<one-line>",
  "body": "<full body, included only if budget had room>",
  "score": 0.642,
  "chunk_idx": 0,
  "citation": "[mnemo:<id>]"
}
```

When presenting to the user:

1. **Always** include the `citation` so they can resolve back via
   `mnemo node show <id>` or open `http://127.0.0.1:7373/node/<id>`.
2. Lead with the highest-scoring hits.
3. Show the body only if the user asked for detail; otherwise the
   description is enough.

## How to interpret intent_tags

The `intent_tags` field tells you which patterns matched:

| Tag | What it means | Type weights it boosts |
|---|---|---|
| `debug` | error / fail / crash / stack-trace language | memory_project, memory_feedback |
| `feedback-recall` | "always", "never", "prefer", "rule" | memory_feedback, memory_user |
| `project-context` | "this repo", "in our setup" | memory_project, project_doc |
| `design` | "design", "architecture", "approach" | plan_doc, memory_project |
| `reference` | "where is", "what is", "find" | memory_reference, memory_project |
| `none` | nothing matched | balanced default |

If the prompt clearly fits a tag but `intent_tags` came back as `none`,
file an issue - the regex is conservative by design but should catch
common phrasings.

## Cross-cutting

- The audit log writes a row per query (`mnemo audit` from the CLI, or
  `/audit-page` in the UI). Use it to spot bad retrieval and tune.
- `mnemo query` takes ~50-100ms after the model is loaded. The first
  query in a session pays the model-load tax (~2s).
- The hybrid scoring weights are at module-level in `mnemo/retrieve.py`
  (ALPHA, BETA, GAMMA, DELTA, EPSILON). Override only if you have data
  from the audit log saying the defaults are wrong for your usage.
