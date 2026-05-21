# agent-memory-bench

> **Status: skeleton** (Phase 2 / Task 3.2 of the mnemo execution
> plan). The benchmark spec is published; the first end-to-end
> task lands in Task 3.3.

A reproducible benchmark for **typed Graph-RAG agent memory** — the
missing layer every modern AI coding agent reinvents in private.

- **Spec** (CC-BY-4.0): [`docs/benchmark/agent-memory-spec-v0.md`](../docs/benchmark/agent-memory-spec-v0.md)
  in the parent repo.
- **Harness** (MIT, this package): scaffold lives here; the first
  real task end-to-end ships in Task 3.3 of the enterprise
  execution plan.
- **Reference implementation**: [mnemo](https://github.com/mmct-jsc/mnemo)
  (`/v1/query` for retrieval; `/v1/feedback/thumbs` for the feedback
  loop). Any agent that satisfies the documented `Memory` Protocol
  (see `agent_memory_bench/runner.py`) can be benchmarked.

## Quick start

```bash
cd bench
uv sync --extra dev
uv run pytest -v
```

## Public surface (v0.1.0 skeleton)

```python
from agent_memory_bench import Memory, Metrics, Retrieval, TaskResult, run_task
```

- `Memory` — the Protocol external implementers satisfy.
- `Retrieval` — what `Memory.query` returns.
- `Metrics` — the four spec metrics (re-derivation rate, tokens-in /
  tokens-out, citation precision, answer correctness).
- `TaskResult` — per-task report aggregating output + metrics.
- `run_task` — agent-shape-agnostic invocation.

External implementers do not need to import a base class — the
agent contract is just `Callable[[str], str]`. Agents that want
memory close over their own handle; the harness threads a
`Memory | None` into `run_task` for completeness.

## License

- This harness: MIT.
- The spec (`docs/benchmark/agent-memory-spec-v0.md`): CC-BY-4.0.
- mnemo (the reference implementation): MIT.

## Roadmap to v1.0

See the spec's roadmap section. Pull-request based contributions
welcome — open an issue first describing the task / metric /
implementation you'd like to add.
