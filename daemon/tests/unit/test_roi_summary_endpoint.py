"""Task 3.4: GET /v1/roi/summary aggregates feedback + retune telemetry.

The plan-spec test from the enterprise execution plan: the endpoint
must return a flat dict with the 5 documented ROI keys. The actual
values are skeleton-quality (project filter is a no-op against
project-aware queries because the queries table doesn't carry a
project column; v0.1 of this endpoint plumbs that through). Field
names + types are the lock-in.

This is the ROI surface the v0 case study (Task 3.6) reads from,
the dashboard card (Task 3.5) renders, and the sponsor application
(Task 1.7) cites in the "traction" section.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Node, Store


@pytest.fixture
def daemon_client(store: Store) -> TestClient:
    """A TestClient backed by an in-memory store. The fixture's
    ``store`` is the standard per-test SQLite from conftest."""
    return TestClient(create_app(store=store))


def _seed_node(store: Store, node_id: str = "n1") -> None:
    """Ensure a node exists -- feedback_event FKs require it."""
    store.upsert_node(
        Node(
            id=node_id,
            type="memory_feedback",
            name=node_id,
            description=None,
            body="seed body",
            source_path=f"/seed/{node_id}.md",
            source_kind="memory_dir",
            project_key=None,
            frontmatter_json=None,
            hash="h",
            created_at=1,
            updated_at=1,
        )
    )


def _seed_query_and_feedback(store: Store, query_id: str, reason: str) -> None:
    """Insert one query + one feedback row so the aggregator has
    something to count. ``reason`` is the v1.2 feedback_event reason
    (``thumbs_up`` / ``thumbs_down`` / ...). The node is shared (seed it
    once via _seed_node before the first call)."""
    store.conn.execute(
        "INSERT INTO queries (id, prompt, intent_tags, retrieved_ids, scores, ts) VALUES "
        "(?, ?, ?, ?, ?, ?)",
        (query_id, f"prompt-{query_id}", "[]", "[]", "[]", int(time.time())),
    )
    store.conn.commit()
    store.log_feedback_event(query_id=query_id, node_id="n1", signal=1.0, reason=reason)


def test_roi_summary_returns_expected_keys_on_empty_db(daemon_client: TestClient) -> None:
    """Empty DB still produces a well-formed response with all 5
    keys, all numeric. The dashboard card must be able to render
    against a fresh install without crashing."""
    r = daemon_client.get("/v1/roi/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    for k in (
        "queries_total",
        "rederivations_avoided",
        "tokens_saved_est",
        "thumbs_up_ratio",
        "auto_tune_iterations",
    ):
        assert k in body, f"missing ROI key: {k!r}; got {sorted(body)}"
    # Empty-DB defaults.
    assert body["queries_total"] == 0
    assert body["rederivations_avoided"] == 0
    assert body["tokens_saved_est"] == 0
    assert body["thumbs_up_ratio"] == 0.0
    assert body["auto_tune_iterations"] == 0


def test_roi_summary_counts_queries_and_thumbs(
    daemon_client: TestClient,
    store: Store,
) -> None:
    """Seed 3 thumbs-up + 1 thumbs-down + 4 queries. Verify the
    aggregator counts them correctly."""
    _seed_node(store)
    for i, reason in enumerate(["thumbs_up", "thumbs_up", "thumbs_up", "thumbs_down"]):
        _seed_query_and_feedback(store, f"q{i}", reason)

    r = daemon_client.get("/v1/roi/summary")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["queries_total"] == 4
    # rederivations_avoided proxy = count of thumbs_up (user said
    # this retrieval was useful, so they didn't re-derive)
    assert body["rederivations_avoided"] == 3
    # thumbs_up_ratio = 3 / (3 + 1) = 0.75
    assert body["thumbs_up_ratio"] == pytest.approx(0.75)
    # tokens_saved_est is queries_total * a documented constant; in
    # v0.1 this is 200 tokens / query (rough estimate of context
    # the cited retrieval saved vs naive re-derivation).
    assert body["tokens_saved_est"] == 4 * 200


def test_roi_summary_accepts_optional_project_query_param(
    daemon_client: TestClient,
) -> None:
    """The ``project`` query param is accepted today (forward
    compatibility) but is a no-op against the project-unaware
    queries table. v0.2 plumbs it through. The endpoint must NOT
    400 / 422 on the param.
    """
    r = daemon_client.get("/v1/roi/summary?project=anything")
    assert r.status_code == 200, r.text


def test_roi_summary_thumbs_ratio_zero_when_no_feedback(
    daemon_client: TestClient,
    store: Store,
) -> None:
    """No thumbs_up AND no thumbs_down -> ratio defaults to 0.0,
    not NaN or 500. The dashboard card formats this as 'No
    feedback yet'."""
    # Seed a query but no feedback
    store.conn.execute(
        "INSERT INTO queries (id, prompt, intent_tags, retrieved_ids, scores, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("q-only", "prompt", "[]", "[]", "[]", int(time.time())),
    )
    store.conn.commit()

    r = daemon_client.get("/v1/roi/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["queries_total"] == 1
    assert body["thumbs_up_ratio"] == 0.0  # no division-by-zero blowup
