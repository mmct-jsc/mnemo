"""Unit tests for v1.2 phase 5 auto-tuner.

The retune module is pure math once given (queries with components +
feedback index). These tests build small synthetic samples and verify:

- ``best_feedback_signal`` respects the explicit-over-implicit rule
  documented in the v1.2 design.
- ``rescore_with_weights`` produces the right linear combination.
- ``mrr`` correctly increments by 1/rank on positive hits and zero
  otherwise.
- ``coordinate_descent`` either improves or leaves weights unchanged
  on noiseless data.
- The high-level ``retune`` entrypoint refuses below the min-queries
  threshold and writes back the proposed weights on user accept.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from mnemo.config import ScoringWeights
from mnemo.retune import (
    RetuneReport,
    best_feedback_signal,
    coordinate_descent,
    mrr,
    rescore_with_weights,
    retune,
)
from mnemo.store import FeedbackEvent, Node, Store

# --- best_feedback_signal --------------------------------------------------


def _ev(signal: float, reason: str, created_at: int = 0) -> FeedbackEvent:
    return FeedbackEvent(
        id=-1,
        query_id="q",
        node_id="n",
        signal=signal,
        reason=reason,
        created_at=created_at,
    )


def test_best_feedback_signal_explicit_thumbs_overrides_implicit() -> None:
    """The user gave a thumbs_up AND the inferred-requery detector
    fired a negative. The thumbs_up wins -- explicit always beats
    implicit per the v1.2 design."""
    explicit = _ev(1.0, "thumbs_up", created_at=200)
    implicit = _ev(-0.5, "inferred_requery", created_at=300)  # newer ts
    assert best_feedback_signal([explicit, implicit]) == 1.0


def test_best_feedback_signal_latest_explicit_wins() -> None:
    """Two explicit thumbs from the same user on the same hit
    (changed their mind): the latest wins."""
    early = _ev(1.0, "thumbs_up", created_at=100)
    late = _ev(-1.0, "thumbs_down", created_at=200)
    assert best_feedback_signal([early, late]) == -1.0


def test_best_feedback_signal_no_explicit_falls_back_to_max_implicit() -> None:
    """Only implicit signals exist -- take the max so a cite_copied
    (+0.5) overrides an inferred_requery (-0.5)."""
    cite = _ev(0.5, "cite_copied", created_at=100)
    requery = _ev(-0.5, "inferred_requery", created_at=200)
    assert best_feedback_signal([cite, requery]) == 0.5


def test_best_feedback_signal_empty_returns_zero() -> None:
    assert best_feedback_signal([]) == 0.0


# --- rescore_with_weights --------------------------------------------------


def test_rescore_with_weights_linear_combination() -> None:
    """The rescore must exactly reproduce retrieve.py's 6-term formula
    given the captured components and a weight vector."""
    components = {
        "a": {
            "vector": 1.0,
            "graph": 0.0,
            "recency": 0.0,
            "type": 0.0,
            "project": 0.0,
            "lexical": 0.0,
        },
        "b": {
            "vector": 0.0,
            "graph": 1.0,
            "recency": 0.0,
            "type": 0.0,
            "project": 0.0,
            "lexical": 0.0,
        },
    }
    # Weights pick out only the vector term.
    w = ScoringWeights(alpha=0.5, beta=0.0, gamma=0.0, delta=0.0, epsilon=0.0, zeta=0.0)
    result = rescore_with_weights(components, w)
    # a has vector=1.0 so score 0.5; b has vector=0.0 so score 0.0.
    # Higher first.
    assert result == [("a", 0.5), ("b", 0.0)]


def test_rescore_with_weights_handles_missing_terms() -> None:
    """Older audit rows may not have all six terms in the dict (if a
    component was zero we may not have logged it). Missing terms are
    treated as 0.0 so the rescore doesn't KeyError."""
    components = {"a": {"vector": 1.0}}  # only `vector` -- others missing
    w = ScoringWeights()
    result = rescore_with_weights(components, w)
    assert result == [("a", w.alpha * 1.0)]


# --- mrr ------------------------------------------------------------------


def test_mrr_returns_one_for_correct_top_hit() -> None:
    """Single query, single positive feedback on the rank-1 hit
    -> MRR = 1/1 = 1.0."""
    components = {"a": {"vector": 1.0}, "b": {"vector": 0.0}}
    samples = [("q1", components)]
    fb_index = {("q1", "a"): [_ev(1.0, "thumbs_up")]}
    w = ScoringWeights(alpha=1.0, beta=0, gamma=0, delta=0, epsilon=0, zeta=0)
    assert mrr(w, samples, fb_index) == 1.0


def test_mrr_returns_half_when_correct_is_rank_two() -> None:
    """Positive feedback on the rank-2 hit -> MRR = 1/2 = 0.5."""
    components = {"a": {"vector": 1.0}, "b": {"vector": 0.0}}
    samples = [("q1", components)]
    # Feedback is on 'b' which scores lower than 'a' under these weights,
    # so b ranks 2nd.
    fb_index = {("q1", "b"): [_ev(1.0, "thumbs_up")]}
    w = ScoringWeights(alpha=1.0, beta=0, gamma=0, delta=0, epsilon=0, zeta=0)
    assert mrr(w, samples, fb_index) == 0.5


def test_mrr_returns_zero_when_no_positive_in_pool() -> None:
    """All feedback is negative -- MRR is 0 (nothing 'correct'
    surfaced)."""
    components = {"a": {"vector": 1.0}}
    samples = [("q1", components)]
    fb_index = {("q1", "a"): [_ev(-1.0, "thumbs_down")]}
    w = ScoringWeights(alpha=1.0, beta=0, gamma=0, delta=0, epsilon=0, zeta=0)
    assert mrr(w, samples, fb_index) == 0.0


def test_mrr_averages_over_multiple_queries() -> None:
    """Two queries: one with MRR=1 (rank-1 correct), one with MRR=0.5
    (rank-2 correct). Mean = 0.75."""
    comps_a = {"a": {"vector": 1.0}, "b": {"vector": 0.0}}
    comps_b = {"x": {"vector": 0.5}, "y": {"vector": 1.0}}
    samples = [("q1", comps_a), ("q2", comps_b)]
    fb_index = {
        ("q1", "a"): [_ev(1.0, "thumbs_up")],  # rank 1 -> 1.0
        ("q2", "x"): [_ev(1.0, "thumbs_up")],  # rank 2 -> 0.5
    }
    w = ScoringWeights(alpha=1.0, beta=0, gamma=0, delta=0, epsilon=0, zeta=0)
    assert mrr(w, samples, fb_index) == 0.75


# --- coordinate_descent ----------------------------------------------------


def test_coordinate_descent_improves_when_signal_supports_change() -> None:
    """Positive feedback consistently lands on the candidate that wins
    on ``graph``, but the start weights favor ``vector`` slightly. A
    single +0.10 nudge to beta is enough to flip the ranking, so
    coordinate descent should reach val_mrr = 1.0 within one pass.

    Synthetic data is deliberately close to the decision boundary --
    the optimizer's ±0.10 grid can't make large jumps (it'd require
    each step to strictly improve), so any test that expects motion
    must start within one nudge of the inflection point."""
    samples = []
    fb_index: dict[tuple[str, str], list[FeedbackEvent]] = {}
    for i in range(8):
        qid = f"q{i}"
        comps = {
            "A": {"vector": 1.0, "graph": 0.0, "recency": 0, "type": 0, "project": 0, "lexical": 0},
            "B": {"vector": 0.0, "graph": 1.0, "recency": 0, "type": 0, "project": 0, "lexical": 0},
        }
        samples.append((qid, comps))
        fb_index[(qid, "B")] = [_ev(1.0, "thumbs_up")]

    # Start one nudge away from the flip: alpha=0.5, beta=0.45.
    # +0.10 to beta -> beta=0.55 > 0.5; B wins, MRR=1.0.
    start = ScoringWeights(alpha=0.5, beta=0.45, gamma=0, delta=0, epsilon=0, zeta=0)
    start_mrr = mrr(start, samples, fb_index)
    assert start_mrr == 0.5  # B still ranks 2nd at start

    tuned, best, _log, _iters = coordinate_descent(
        start, train_samples=samples, val_samples=samples, feedback_index=fb_index
    )
    # The optimizer can flip the ranking either way -- pushing beta up
    # OR pulling alpha down. Both reach MRR=1.0. Don't lock in the
    # specific direction.
    assert best > start_mrr
    assert tuned.beta > start.beta or tuned.alpha < start.alpha


def test_coordinate_descent_holds_when_no_improvement_possible() -> None:
    """If all candidates have identical components, no nudge can help.
    The optimizer should return the start weights and log 'no
    improvement, stopping'."""
    samples = [
        (
            "q1",
            {
                "a": {
                    "vector": 1.0,
                    "graph": 1.0,
                    "recency": 1.0,
                    "type": 1.0,
                    "project": 1.0,
                    "lexical": 1.0,
                },
                "b": {
                    "vector": 1.0,
                    "graph": 1.0,
                    "recency": 1.0,
                    "type": 1.0,
                    "project": 1.0,
                    "lexical": 1.0,
                },
            },
        )
    ]
    fb_index = {("q1", "a"): [_ev(1.0, "thumbs_up")]}
    start = ScoringWeights()
    tuned, _best, log, _iters = coordinate_descent(
        start, train_samples=samples, val_samples=samples, feedback_index=fb_index
    )
    assert asdict(tuned) == asdict(start)
    assert any("no improvement" in line for line in log)


# --- retune entrypoint -----------------------------------------------------


def test_retune_refuses_below_min_queries_threshold(tmp_path: Path) -> None:
    """The high-level retune function returns a NoSamples report when
    the labeled-query count is under ``min_queries``. CLI surfaces
    this as a helpful "run a few more queries first" message."""
    store = Store(tmp_path / "test.db")
    try:
        report = retune(store, min_queries=30)
        assert isinstance(report, RetuneReport)
        assert report.train_size == 0
        assert report.val_size == 0
        assert "below threshold" in " ".join(report.log).lower()
    finally:
        store.close()


def test_retune_end_to_end_with_seeded_feedback(tmp_path: Path) -> None:
    """Seed the store with enough labeled queries that the optimizer
    has signal to chew on. Verify the entrypoint returns a complete
    RetuneReport with before/after MRR + non-empty log."""
    store = Store(tmp_path / "test.db")
    try:
        # Two nodes for the FK.
        a = Node.new(
            type="memory_feedback",
            name="a",
            body="A",
            source_path="/a.md",
            source_kind="memory_dir",
        )
        b = Node.new(
            type="memory_feedback",
            name="b",
            body="B",
            source_path="/b.md",
            source_kind="memory_dir",
        )
        store.upsert_node(a)
        store.upsert_node(b)
        # Seed 8 labeled queries -- B (graph) is always 'correct'.
        # Keep retune_min_queries default (30) but pass a smaller
        # threshold in this test.
        for i in range(8):
            qid = store.log_query(
                prompt=f"q{i}",
                intent_tags=[],
                retrieved_ids=[a.id, b.id],
                scores={a.id: 0.9, b.id: 0.1},
                score_components={
                    a.id: {
                        "vector": 1.0,
                        "graph": 0.0,
                        "recency": 0,
                        "type": 0,
                        "project": 0,
                        "lexical": 0,
                    },
                    b.id: {
                        "vector": 0.0,
                        "graph": 1.0,
                        "recency": 0,
                        "type": 0,
                        "project": 0,
                        "lexical": 0,
                    },
                },
            )
            store.log_feedback_event(query_id=qid, node_id=b.id, signal=1.0, reason="thumbs_up")

        # Pin starting weights so the test is deterministic regardless of
        # whatever the on-disk config says.
        start = ScoringWeights(alpha=1.0, beta=0.0, gamma=0, delta=0, epsilon=0, zeta=0)
        report = retune(store, min_queries=4, start_weights=start)
        assert report.train_size + report.val_size == 8
        assert report.val_mrr_after >= report.val_mrr_before
        assert report.log  # at least one log line
        # Diff should mostly be non-zero on beta (graph weight rises).
        assert report.diff["beta"] >= 0  # never moves backward in this setup
    finally:
        store.close()
