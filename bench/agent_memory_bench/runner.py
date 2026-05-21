"""Benchmark runner: agent-shape-agnostic invocation + metric scaffolding.

Task 3.2 of the enterprise execution plan / Phase 2. Implements the
minimum surface the spec (``docs/benchmark/agent-memory-spec-v0.md``)
requires:

- :class:`Memory` protocol -- the contract external implementers
  satisfy. mnemo's ``/v1/query`` is the reference implementation;
  any other Graph-RAG memory can register an adapter agent.
- :class:`Retrieval` -- what ``Memory.query`` returns.
- :class:`Metrics` -- the four spec metrics (M1-M4). Population is
  Task 3.3's job (the per-task scoring code); this skeleton just
  defines the shape with safe zero defaults.
- :class:`TaskResult` -- the per-task report aggregating output +
  metrics.
- :func:`run_task` -- invokes an agent once per prompt in the
  sequence; collects the final output + zero-initialised metrics.

The runner deliberately knows nothing about specific tasks or
fixtures. Task 3.3 adds the first real task end-to-end on top of
this skeleton without modifying the runner's signature.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol


class Memory(Protocol):
    """The Memory contract per the v0 spec.

    Agents that want memory take an optional ``Memory`` handle in
    their factory; the harness threads it in. Vanilla baseline
    agents ignore the handle (run_task accepts ``memory=None``).
    """

    def query(self, prompt: str, max_tokens: int = 800) -> Retrieval: ...

    def feedback(self, hit_id: str, direction: Literal["up", "down"]) -> None: ...


@dataclass
class Retrieval:
    """What ``Memory.query`` returns. Shape mirrors mnemo's
    ``/v1/query`` response so the reference adapter is trivial."""

    text: str
    """The budgeted, formatted context block ready to drop into a prompt."""

    hit_ids: list[str]
    """Per-hit identifiers in the order returned. Citation-precision
    scoring compares these against the task's ``expected.json``."""

    tokens_used: int
    """Actual token count of ``text``. Token-budget compliance
    (M2 / task T8) compares against the request's ``max_tokens``."""


@dataclass
class Metrics:
    """The four spec metrics (M1-M4). Zero-default so the skeleton's
    ``TaskResult`` always has a well-formed metrics object even before
    Task 3.3's scoring code lands.

    All four are per-task; aggregation across tasks happens at report
    time (Task 3.7), not here.
    """

    rederivation_rate: float = 0.0
    """M1. Fraction of follow-up turns that re-issue a semantically
    equivalent query when memory should have made it unnecessary.
    Lower is better (0.0 = perfect memory; 1.0 = re-derives every turn)."""

    tokens_in: int = 0
    """M2 (input side). Sum of model-input tokens across all turns of
    the task to reach a correct answer. Lower is better."""

    tokens_out: int = 0
    """M2 (output side). Sum of model-output tokens. Kept separate so
    cost-per-task can be computed against any provider's pricing
    sheet without re-running the harness."""

    citation_precision: float = 0.0
    """M3. Of the hits the agent's final answer cites, fraction that
    appear in the task's expected-relevant set. Higher is better."""

    answer_correctness: float = 0.0
    """M4. LLM-judge rubric score in [0.0, 1.0]. Higher is better."""


@dataclass
class TaskResult:
    """The per-task report. ``output`` is the agent's final answer
    (the judge grades this for M4); ``metrics`` is the populated
    :class:`Metrics`."""

    task_id: str
    output: str
    metrics: Metrics = field(default_factory=Metrics)


def run_task(
    *,
    task_id: str,
    agent: Callable[[str], str],
    memory: Memory | None,
    prompts: list[str] | None = None,
) -> TaskResult:
    """Invoke ``agent`` against the prompt sequence; return a
    :class:`TaskResult` with the final answer + zero-init metrics.

    Skeleton (Task 3.2) behaviour:

    - If ``prompts`` is omitted, the harness sends a single ``"echo"``
      prompt so simple smoke tests can use ``lambda p: "ok"``.
    - If ``prompts`` is provided, ``agent`` is invoked once per prompt
      in order. The final answer is recorded; intermediate answers
      are discarded (the per-turn scoring code in Task 3.3 wraps the
      agent if it needs the full sequence).
    - ``memory`` is accepted on the signature but not threaded into
      ``agent`` automatically; agents that need memory close over
      their own handle. This keeps the runner's contract minimal
      and matches the spec's "agent-shape-agnostic" goal.

    Future (Task 3.3+) will populate ``Metrics`` from per-task
    scoring code dispatched off ``task_id``. The signature stays
    stable so external implementers can wire against this surface
    today.
    """
    sequence = prompts if prompts else ["echo"]
    output = ""
    for prompt in sequence:
        output = agent(prompt)
    return TaskResult(
        task_id=task_id,
        output=output,
        metrics=Metrics(),
    )
