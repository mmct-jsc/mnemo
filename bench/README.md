# agent-memory-bench

A reproducible benchmark for **typed Graph-RAG agent memory** — the
missing layer every modern AI coding agent reinvents in private.

- **Spec** (CC-BY-4.0): [`docs/benchmark/agent-memory-spec-v0.md`](../docs/benchmark/agent-memory-spec-v0.md) in the parent repo. Defines 8 tasks (T1 → T8), 4 metrics (M1 re-derivation rate, M2 tokens-to-answer, M3 citation precision, M4 answer correctness), and 2 reference baselines.
- **Harness** (MIT): this package.
- **First case study**: [`docs/case-studies/2026-05-mnemo-self-host.md`](../docs/case-studies/2026-05-mnemo-self-host.md) — real numbers from the dogfooded install (310 queries / 11.4 days / 62K tokens-saved estimate).
- **Reference implementation**: [mnemo](https://github.com/mmct-jsc/mnemo) (`/v1/query` for retrieval; `/v1/feedback/thumbs` for the feedback loop). Any agent that satisfies the documented `Memory` Protocol (see `agent_memory_bench/runner.py`) can be benchmarked.

## Quick start

```bash
cd bench
uv sync --extra dev
uv run pytest -v
```

Expected: 12 passing tests (+ 1 skipped on the live-daemon mnemo
agent unless `MNEMO_DAEMON_URL` is set).

## Public surface (v0.1.0)

```python
from agent_memory_bench import Memory, Metrics, Retrieval, TaskResult, run_task
from agent_memory_bench.agents.vanilla import make_vanilla_agent
from agent_memory_bench.agents.mnemo import (
    make_mnemo_mock_agent,        # deterministic CI baseline
    make_mnemo_http_agent,        # live-daemon adapter, gated on MNEMO_DAEMON_URL
)
from agent_memory_bench.tasks import answer_follow_up
```

- `Memory` — Protocol external implementers satisfy. No base class.
- `Retrieval` — what `Memory.query()` returns.
- `Metrics` — the four spec metrics with safe zero defaults.
- `TaskResult` — per-task report.
- `run_task` — agent-shape-agnostic invocation.
- `answer_follow_up.run(agent_factory)` — T1 end-to-end with
  scoring. Strict invariant locked: `vanilla > mnemo` on
  rederivation rate.

## Tasks shipped (v0)

| ID | Task | Status |
|---|---|---|
| T1 | Answer follow-up referencing turn-1 material | shipped (`tasks/answer_follow_up.py`) |
| T2 | Code-symbol chain (5 turns) | spec only |
| T3 | Recover after session resume | spec only |
| T4 | Reject stale / superseded memory | spec only |
| T5 | Honor permission boundary | spec only |
| T6 | Apply in-session feedback | spec only |
| T7 | Cross-project isolation | spec only |
| T8 | Budget compliance | spec only |

T2-T8 land in v0.1 + v0.2 of the harness. Their fixtures
(`bench/fixtures/<task_id>/`) follow the same shape as
`answer_follow_up`.

## Writing a new agent

External implementers register an agent factory of shape
`Callable[[Memory], Callable[[str], str]]`. The agent closes over
the `Memory` handle for queries; the runner threads prompts through
it. Look at `agent_memory_bench/agents/mnemo.py` for the simplest
real adapter (HTTP against `POST /v1/query`) and
`agent_memory_bench/agents/vanilla.py` for the no-memory baseline
shape.

## License

- This harness: MIT.
- The spec: CC-BY-4.0.
- mnemo (the reference implementation): MIT.

## Roadmap

See the spec's roadmap section. Pull-request based contributions
welcome — open an issue first describing the task / metric /
implementation you'd like to add.
