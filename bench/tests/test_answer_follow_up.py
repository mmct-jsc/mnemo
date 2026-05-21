"""Task 3.3 acceptance tests: T1 answer-follow-up end-to-end.

Asserts the strict invariant from ``expected.json``:

    vanilla.rederivation_rate > mnemo.rederivation_rate

If a future change breaks this, the entire substrate framing
("typed Graph-RAG memory beats no memory") fails. The test catches
it before the breaking change ships.
"""

from __future__ import annotations

import os

import pytest

from agent_memory_bench.agents.mnemo import (
    get_daemon_url,
    make_mnemo_http_agent,
    make_mnemo_mock_agent,
)
from agent_memory_bench.agents.vanilla import make_vanilla_agent
from agent_memory_bench.tasks import answer_follow_up


def test_fixture_loads_with_expected_shape() -> None:
    fx = answer_follow_up.load_fixture()
    assert len(fx.corpus) == 3, f"T1 corpus must have 3 memory_feedback nodes; got {len(fx.corpus)}"
    assert fx.prompts == [
        "How do we handle MQTT broker auth?",
        "What's the testing approach for that?",
    ]
    assert fx.relevant_node_ids == {"mqtt-auth-1", "mqtt-auth-2", "mqtt-auth-3"}
    assert "paho" in fx.required_keywords_final_turn


def test_vanilla_baseline_runs_and_re_derives_every_turn() -> None:
    """Vanilla re-derives on every turn by construction. With 2
    prompts (1 follow-up), the score must be 1.0."""
    result = answer_follow_up.run(make_vanilla_agent)
    assert result.task_id == "answer_follow_up"
    assert result.metrics.rederivation_rate == pytest.approx(1.0), (
        f"vanilla must re-derive on every follow-up turn; got "
        f"rederivation_rate={result.metrics.rederivation_rate}"
    )


def test_mnemo_mock_caches_turn_1_retrieval_and_reuses_on_follow_up() -> None:
    """Mnemo mock queries on turn 1, reuses cache on turn 2 (which
    contains the deictic 'that'). One query / one follow-up turn ->
    rederivation_rate = 0.0."""
    result = answer_follow_up.run(make_mnemo_mock_agent)
    assert result.task_id == "answer_follow_up"
    assert result.metrics.rederivation_rate == pytest.approx(0.0), (
        f"mnemo mock must NOT re-derive on the deictic follow-up; got "
        f"rederivation_rate={result.metrics.rederivation_rate}"
    )


def test_strict_invariant_mnemo_beats_vanilla_on_rederivation() -> None:
    """The spec's strict invariant for T1. If this ever flips, the
    substrate framing breaks -- catch it loudly."""
    vanilla_result = answer_follow_up.run(make_vanilla_agent)
    mnemo_result = answer_follow_up.run(make_mnemo_mock_agent)
    assert vanilla_result.metrics.rederivation_rate > mnemo_result.metrics.rederivation_rate, (
        f"T1 INVARIANT VIOLATED: vanilla rederivation "
        f"({vanilla_result.metrics.rederivation_rate}) must be > mnemo "
        f"rederivation ({mnemo_result.metrics.rederivation_rate}). A "
        f"typed-Graph-RAG memory agent MUST beat the no-memory baseline."
    )


def test_all_four_metrics_populated_for_both_agents() -> None:
    """Every Metrics field is filled in (not just the M1 we focus on)."""
    for agent_factory in (make_vanilla_agent, make_mnemo_mock_agent):
        result = answer_follow_up.run(agent_factory)
        m = result.metrics
        assert 0.0 <= m.rederivation_rate <= 1.0
        assert m.tokens_in >= 0
        assert m.tokens_out >= 0
        assert 0.0 <= m.citation_precision <= 1.0
        assert 0.0 <= m.answer_correctness <= 1.0


def test_score_function_is_pure_and_re_callable() -> None:
    """The scorer must be deterministic given the same inputs --
    enables re-scoring against different judges later (v0.1
    upgrades M4 to a real LLM judge; the per-task fixture stays
    stable so historical scores can be recomputed)."""
    fx = answer_follow_up.load_fixture()
    metrics_a = answer_follow_up.score(fixture=fx, queries=["q1"], output="paho mqtt CONNACK")
    metrics_b = answer_follow_up.score(fixture=fx, queries=["q1"], output="paho mqtt CONNACK")
    assert metrics_a == metrics_b


@pytest.mark.skipif(
    get_daemon_url() is None,
    reason="MNEMO_DAEMON_URL unset; skipping live-daemon mnemo agent",
)
def test_mnemo_http_agent_against_live_daemon() -> None:
    """Live-daemon smoke. Skipped in CI; runs when a developer has
    a daemon up at MNEMO_DAEMON_URL=http://127.0.0.1:7373 (the
    canonical local address)."""
    daemon_url = os.environ["MNEMO_DAEMON_URL"]
    agent = make_mnemo_http_agent(daemon_url)
    fx = answer_follow_up.load_fixture()
    # Just drive one prompt to verify the daemon is reachable +
    # responds with something. Not a strict scoring test.
    out = agent(fx.prompts[0])
    assert isinstance(out, str)
    assert "mnemo-http" in out
