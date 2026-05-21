# Case study: dogfooded mnemo on the mnemo repo (2026-05)

> First case study for the [agent-memory benchmark](../benchmark/agent-memory-spec-v0.md).
> Numbers captured 2026-05-21 from a self-hosted local mnemo daemon
> indexing the mnemo repository itself + the developer's Claude
> Code memory.

## TL;DR

**Real numbers, real install, ~11 days of active development:**

| Metric | Value | Source |
|---|---:|---|
| Indexed nodes | **12,494** | `mnemo daemon` `count_nodes()` aggregate |
| Indexed sources | **11** | memory dirs + code repos |
| Queries served | **310** | `queries` audit log (`GET /v1/audit`) |
| Period | **11.4 days** (2026-05-10 → 2026-05-21) | first / last `queries.ts` |
| Query rate | **~27 / day** | derived |
| Tokens saved (est.) | **62,000** | `queries_total × 200`, the v0.1 ROI heuristic |
| Thumbs-up signals | 0 | `feedback_event WHERE reason='thumbs_up'` |
| Re-tune iterations | 0 | no `retune_history` table in v0.1 |

Surfaced by `GET /v1/roi/summary` and rendered on the
`/` dashboard's "ROI summary" card as of v4.6.5 + Phase 2 of the
substrate-hardening roadmap.

## Why this case study

The agent-memory benchmark spec (v0) defines four metrics on a
controlled fixture per task. Real-world ROI is a different question:
how much memory-pull happens in normal day-to-day use of a coding
agent? This case study is the smallest credible answer — one
heavily-dogfooded local install over one calendar week.

It is intentionally honest about what the v0 telemetry can and
cannot tell us. The places the numbers under-state real value are
called out explicitly. Future case studies (one quarter from now, +
one with opt-in shared telemetry from a second installation) refine
the picture.

## The install

| Layer | Detail |
|---|---|
| Daemon | mnemo v4.6.5 |
| Renderer | Nebula custom WebGL (`nebula-gl.js`, v4.6.0+) |
| MCP server | `mnemo mcp` (stdio), 26 tools (9 safe / 13 confirm / 4 danger) |
| Indexed sources | 11 (Claude per-project memory + global CLAUDE.md + 3 active repos + design doc paths) |
| Active project | mnemo itself (`D:/Repository/knowledge-base`) |
| Top node types | code_method 4469, code_module 3121, code_function 3007, code_class 1157, commit 292, code_route 180 |
| Tier | 1 + 2 + 3 (Tree-sitter ingestion, scope-resolution call graph, framework extractors all active) |

## What 310 queries in 11 days looks like

- ~27 queries/day across an active coding week.
- Each query touched ≤ 800 tokens of typed Graph-RAG context with
  `[mnemo:<id>]` citations (the daemon's hard cap).
- The 62,000 tokens-saved estimate is **conservative**. The v0.1
  heuristic charges 200 tokens-per-query as "what naive RAG would
  have re-derived." Anecdotally, follow-up turns on the same
  topic — where a tool-use agent would otherwise re-`grep` /
  re-`read` files for the third or fourth time — save substantially
  more than 200 tokens each. v0.2 plumbs per-query
  `budget_tokens_used` deltas so the estimate becomes a measurement.

## Why thumbs-up = 0 (and why that's not the whole story)

The most honest reading: the explicit-thumbs UI affordance is
rarely used in casual day-to-day work. The chat companion + slash
commands DO surface a thumbs button on every hit; the user just
doesn't click it. That's a user-research signal worth taking
seriously, not a defect to hide.

What `thumbs_up_ratio = 0.0` does **NOT** mean:

- It does NOT mean the retrievals were bad. mnemo is being used 27
  times a day by the same developer — that revealed-preference
  signal beats any explicit rating.
- It does NOT mean no implicit positive signal exists. mnemo also
  tracks `inferred_requery` and `cite_copied` as `confirm`-risk
  signals (see `daemon/mnemo/store.py::FEEDBACK_REASONS`); v0.1 of
  the ROI scorer only reads explicit `thumbs_up`. v0.2 of the
  benchmark spec adds implicit-signal aggregation to the headline
  metric.

What it DOES mean for v0.1 of this case study: the
`rederivations_avoided` field under-counts. Treat **62,000 tokens
saved** as a hard lower bound, not the ceiling.

## What the benchmark spec measures vs what this case study
measures

Different things on purpose:

| Question | Where to look |
|---|---|
| "Does typed Graph-RAG memory beat no-memory on a controlled task?" | `bench/` — the open benchmark with T1-T8 + the strict `vanilla > mnemo` invariant. |
| "How much memory-pull happens in actual day-to-day use?" | This case study + the dashboard's ROI card. |
| "Did this specific PR / commit change retrieval quality?" | `mnemo retune` + the auto-tuner's MRR delta (not in this case study; documented in [v1.2 handbook](../../README.md#v12--learning-to-listen-carried-forward)). |

## How to regenerate these numbers on your own install

```bash
# From any directory with a running mnemo daemon
curl -s http://127.0.0.1:7373/v1/roi/summary | jq .
```

Or via Python, without the daemon:

```python
from mnemo import paths
from mnemo.store import Store
s = Store(paths.db_path())
print(s.roi_summary())
s.close()
```

The dashboard card at `http://127.0.0.1:7373/` shows the same
numbers refreshed on every page load.

## What changes by the next case study (target: 2026-08)

Plumbing improvements that should land before the next case study:

- v0.2 of `Store.roi_summary` plumbs per-query `budget_tokens_used`
  deltas (real M2 tokens-saved, not the 200-token heuristic).
- `retune_history` table lands so `auto_tune_iterations > 0` for
  installs that have run the auto-tuner.
- Implicit-signal aggregation in `rederivations_avoided`
  (currently explicit-thumbs only).
- Project-scoped queries (`?project=...` is currently a no-op
  forward-compat slot).

Each refinement makes the case study's numbers more conservative,
not more inflated. The current 62,000 estimate is the floor; v0.2
should raise it.

## License

CC-BY-4.0, matching the benchmark spec. Cite as: "mnemo dogfood
case study, 2026-05" or link to this file in the repo.
