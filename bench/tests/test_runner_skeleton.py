"""Task 3.2 acceptance tests: runner skeleton + Metrics + TaskResult.

Spec invariants the skeleton must satisfy (per
``docs/benchmark/agent-memory-spec-v0.md``):

1. ``run_task`` returns a :class:`TaskResult` carrying the task id,
   the agent's final output, and a populated :class:`Metrics`.
2. ``Metrics`` exposes all four spec metrics (M1 re-derivation, M2
   tokens-to-answer split into in/out, M3 citation precision, M4
   answer correctness) with safe defaults.
3. ``run_task`` invokes the agent once per prompt in the sequence
   and surfaces the FINAL answer (intermediate turns are discarded
   here; per-turn scoring wraps the agent in Task 3.3).
4. The agent contract is just ``Callable[[str], str]`` so the test
   stub stays trivial and external implementers don't have to
   import a special base class.
"""

from __future__ import annotations

from agent_memory_bench import Memory, Metrics, Retrieval, TaskResult, run_task


def test_runner_returns_result_with_required_fields() -> None:
    """Plan-spec test from Task 3.2 -- the minimum shape every
    skeleton implementation must satisfy."""
    result = run_task(task_id="echo-1", agent=lambda p: "ok", memory=None)
    assert isinstance(result, TaskResult)
    assert result.task_id == "echo-1"
    assert result.metrics.tokens_in >= 0
    assert result.metrics.tokens_out >= 0
    assert 0.0 <= result.metrics.rederivation_rate <= 1.0


def test_metrics_exposes_all_four_spec_metrics() -> None:
    """M1-M4 from the spec. If a future change removes one, this
    fails loudly rather than silently producing a partial report."""
    m = Metrics()
    # M1
    assert hasattr(m, "rederivation_rate")
    assert 0.0 <= m.rederivation_rate <= 1.0
    # M2 (split in/out so cost-per-task can be computed against any
    # provider's pricing sheet)
    assert hasattr(m, "tokens_in")
    assert hasattr(m, "tokens_out")
    assert m.tokens_in >= 0
    assert m.tokens_out >= 0
    # M3
    assert hasattr(m, "citation_precision")
    assert 0.0 <= m.citation_precision <= 1.0
    # M4
    assert hasattr(m, "answer_correctness")
    assert 0.0 <= m.answer_correctness <= 1.0


def test_task_result_carries_the_agents_final_output() -> None:
    """The rubric judge grades this string in M4 (Task 3.3+). If
    run_task doesn't preserve it, the entire scoring layer is
    flying blind."""
    result = run_task(task_id="echo-2", agent=lambda p: "hello world", memory=None)
    assert result.output == "hello world"


def test_run_task_invokes_agent_once_per_prompt() -> None:
    """Skeleton multi-prompt behaviour: each prompt gets a turn; the
    final answer wins. Per-turn scoring lands in Task 3.3."""
    calls: list[str] = []

    def recording_agent(prompt: str) -> str:
        calls.append(prompt)
        return f"answer-to-{prompt}"

    result = run_task(
        task_id="multi",
        agent=recording_agent,
        memory=None,
        prompts=["q1", "q2", "q3"],
    )
    assert calls == ["q1", "q2", "q3"]
    assert result.output == "answer-to-q3"


def test_default_prompt_when_none_provided() -> None:
    """Smoke-test convenience: ``run_task(..., prompts=None)`` sends a
    single 'echo' prompt so simple skeleton tests (like the plan
    stub above) don't need to construct a sequence."""
    calls: list[str] = []
    run_task(
        task_id="smoke",
        agent=lambda p: calls.append(p) or "ok",
        memory=None,
    )
    assert calls == ["echo"]


def test_memory_protocol_can_be_implemented_minimally() -> None:
    """External implementers satisfy the Memory protocol without
    importing a special base class. Verify a trivial inline
    implementation type-checks at runtime."""

    class InlineMemory:
        def query(self, prompt: str, max_tokens: int = 800) -> Retrieval:
            return Retrieval(text="", hit_ids=[], tokens_used=0)

        def feedback(self, hit_id: str, direction) -> None:  # noqa: ARG002
            return None

    mem: Memory = InlineMemory()  # type-check via assignment
    r = mem.query("anything")
    assert isinstance(r, Retrieval)
    assert r.tokens_used == 0
