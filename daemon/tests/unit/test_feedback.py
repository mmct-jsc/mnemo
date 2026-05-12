"""Unit tests for v1.2 phase 2 inferred-re-query feedback detector.

The detector lives in ``mnemo.feedback`` and is called by the retrieve
path BEFORE the current query's audit row is written, so it never
compares the current query to itself.
"""

from __future__ import annotations

import time
from pathlib import Path

from mnemo.feedback import infer_requery_feedback
from mnemo.store import Node, Store


def _node(store: Store, name: str) -> Node:
    n = Node.new(
        type="memory_feedback",
        name=name,
        body="b",
        source_path=f"/{name}.md",
        source_kind="memory_dir",
    )
    store.upsert_node(n)
    return n


def _identity_embed(seed: float, dim: int = 384) -> list[float]:
    """Deterministic embedding: every component = seed. Cosine of two
    such vectors is 1.0 if seeds have the same sign, -1.0 if opposite.
    Lets tests dial similarity by picking seeds."""
    return [seed] * dim


def _orthogonal_embed_pair(dim: int = 384) -> tuple[list[float], list[float]]:
    """Two orthogonal vectors (cosine = 0). First half / second half
    pattern."""
    a = [1.0 if i < dim // 2 else 0.0 for i in range(dim)]
    b = [0.0 if i < dim // 2 else 1.0 for i in range(dim)]
    return a, b


def test_infer_requery_emits_feedback_on_similar_recent_query(
    tmp_path: Path,
) -> None:
    """Two queries logged inside the window with embeddings cosine = 1.0
    must produce inferred_requery feedback rows for the earlier query's
    retrieved hits."""
    store = Store(tmp_path / "test.db")
    try:
        n1, n2 = _node(store, "a"), _node(store, "b")
        old_emb = _identity_embed(1.0)

        old_qid = store.log_query(
            prompt="mqtt auth",
            intent_tags=["debug"],
            retrieved_ids=[n1.id, n2.id],
            scores={n1.id: 0.9, n2.id: 0.8},
            embedding=old_emb,
        )

        # Current (re-asked) query: same vector -> cosine 1.0.
        emitted = infer_requery_feedback(
            store,
            query_emb=_identity_embed(1.0),
            window_seconds=300,
            threshold=0.85,
            top_n=3,
        )

        assert emitted == 2  # 2 hits in the earlier query, both flagged
        events = store.list_feedback_events(query_id=old_qid)
        assert len(events) == 2
        assert {e.node_id for e in events} == {n1.id, n2.id}
        for e in events:
            assert e.reason == "inferred_requery"
            assert e.signal == -0.5
    finally:
        store.close()


def test_infer_requery_no_emit_below_threshold(tmp_path: Path) -> None:
    """Orthogonal vectors (cosine = 0) must not trigger the detector
    even within the time window."""
    store = Store(tmp_path / "test.db")
    try:
        n = _node(store, "a")
        a_vec, b_vec = _orthogonal_embed_pair()
        old_qid = store.log_query(
            prompt="old",
            intent_tags=[],
            retrieved_ids=[n.id],
            scores={n.id: 0.9},
            embedding=a_vec,
        )

        emitted = infer_requery_feedback(
            store,
            query_emb=b_vec,
            window_seconds=300,
            threshold=0.85,
            top_n=3,
        )

        assert emitted == 0
        assert store.list_feedback_events(query_id=old_qid) == []
    finally:
        store.close()


def test_infer_requery_no_emit_outside_window(tmp_path: Path) -> None:
    """A similar prompt logged 30 minutes ago is no longer 'a re-ask';
    the detector ignores it."""
    store = Store(tmp_path / "test.db")
    try:
        n = _node(store, "a")
        old_qid = store.log_query(
            prompt="old",
            intent_tags=[],
            retrieved_ids=[n.id],
            scores={n.id: 0.9},
            embedding=_identity_embed(1.0),
        )
        # Backdate the query 30 minutes.
        now = int(time.time())
        store.conn.execute("UPDATE queries SET ts = ? WHERE id = ?", (now - 1800, old_qid))
        store.conn.commit()

        emitted = infer_requery_feedback(
            store,
            query_emb=_identity_embed(1.0),
            window_seconds=300,
            threshold=0.85,
            top_n=3,
        )

        assert emitted == 0
        assert store.list_feedback_events(query_id=old_qid) == []
    finally:
        store.close()


def test_infer_requery_caps_at_top_n_hits(tmp_path: Path) -> None:
    """If the earlier query returned 10 hits, the detector only flags
    the top 3 (or whatever ``top_n`` is) so the feedback signal stays
    concentrated on the most-likely-relevant candidates."""
    store = Store(tmp_path / "test.db")
    try:
        nodes = [_node(store, f"n{i}") for i in range(5)]
        old_qid = store.log_query(
            prompt="five hits",
            intent_tags=[],
            retrieved_ids=[n.id for n in nodes],
            scores={n.id: 0.9 - 0.1 * i for i, n in enumerate(nodes)},
            embedding=_identity_embed(1.0),
        )

        emitted = infer_requery_feedback(
            store,
            query_emb=_identity_embed(1.0),
            window_seconds=300,
            threshold=0.85,
            top_n=3,
        )

        assert emitted == 3
        events = store.list_feedback_events(query_id=old_qid)
        flagged_ids = {e.node_id for e in events}
        # Only first 3 retrieved_ids should be flagged.
        assert flagged_ids == {n.id for n in nodes[:3]}
    finally:
        store.close()


def test_infer_requery_idempotent_on_repeated_calls(tmp_path: Path) -> None:
    """Calling the detector twice within the window for the same
    similar-prompt scenario must not produce duplicate feedback rows
    (the UNIQUE constraint on (query_id, node_id, reason) handles it
    via UPSERT). Caller can run this on every query without bloating
    the feedback table."""
    store = Store(tmp_path / "test.db")
    try:
        n = _node(store, "a")
        old_qid = store.log_query(
            prompt="old",
            intent_tags=[],
            retrieved_ids=[n.id],
            scores={n.id: 0.9},
            embedding=_identity_embed(1.0),
        )

        infer_requery_feedback(
            store,
            query_emb=_identity_embed(1.0),
            window_seconds=300,
            threshold=0.85,
            top_n=3,
        )
        infer_requery_feedback(
            store,
            query_emb=_identity_embed(1.0),
            window_seconds=300,
            threshold=0.85,
            top_n=3,
        )

        events = store.list_feedback_events(query_id=old_qid)
        assert len(events) == 1  # not 2
    finally:
        store.close()


def test_infer_requery_skips_queries_without_embedding(tmp_path: Path) -> None:
    """A pre-v1.2 query in the audit log has a NULL embedding. The
    detector cannot compute cosine against NULL, so it skips those
    rows entirely (the store-level helper filters them out)."""
    store = Store(tmp_path / "test.db")
    try:
        n = _node(store, "a")
        old_qid = store.log_query(
            prompt="legacy",
            intent_tags=[],
            retrieved_ids=[n.id],
            scores={n.id: 0.9},
            # embedding intentionally omitted
        )

        emitted = infer_requery_feedback(
            store,
            query_emb=_identity_embed(1.0),
            window_seconds=300,
            threshold=0.85,
            top_n=3,
        )

        assert emitted == 0
        assert store.list_feedback_events(query_id=old_qid) == []
    finally:
        store.close()
