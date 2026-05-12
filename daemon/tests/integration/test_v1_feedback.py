"""Integration tests for /v1/feedback (v1.2 phase 1).

Covers:
- POST /v1/feedback writes a row and returns it.
- POST is idempotent on (query_id, node_id, reason).
- POST defaults the `signal` field from `reason` when omitted.
- POST rejects unknown reason / bad signal / unknown query_id.
- GET /v1/feedback?query_id=... lists events newest-first.
- GET on unknown query_id returns an empty list (not 404).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.embed import Embedder
from mnemo.server import create_app
from mnemo.store import Node, Store


class _FakeEmbedder(Embedder):
    """Fake embedder so the daemon comes up without downloading MiniLM."""

    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        self.model_name = "fake"
        self._cache_dir = Path("/tmp/mnemo-fake-cache")
        self._model = object()

    @property
    def dim(self) -> int:
        return 384

    def embed_text(self, text: str) -> list[float]:
        return [0.0 for _ in range(384)]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]


@pytest.fixture
def store_and_client(tmp_path: Path) -> Iterator[tuple[Store, TestClient]]:
    db = tmp_path / "test.db"
    store = Store(db)
    app = create_app(store=store, embedder=_FakeEmbedder())
    with TestClient(app) as c:
        yield store, c


def _seed(store: Store) -> tuple[str, str]:
    """Insert one node + one query so /v1/feedback FKs resolve. Returns
    (query_id, node_id)."""
    n = Node.new(
        type="memory_feedback",
        name="fixture",
        body="body",
        source_path="/fixture.md",
        source_kind="memory_dir",
    )
    store.upsert_node(n)
    qid = store.log_query(
        prompt="why mqtt flake?",
        intent_tags=["debug"],
        retrieved_ids=[n.id],
        scores={n.id: 0.91},
    )
    return qid, n.id


# --- POST -------------------------------------------------------------------


def test_post_feedback_writes_row_and_returns_it(
    store_and_client: tuple[Store, TestClient],
) -> None:
    store, client = store_and_client
    qid, node_id = _seed(store)

    r = client.post(
        "/v1/feedback",
        json={
            "query_id": qid,
            "node_id": node_id,
            "signal": 1.0,
            "reason": "thumbs_up",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query_id"] == qid
    assert body["node_id"] == node_id
    assert body["signal"] == 1.0
    assert body["reason"] == "thumbs_up"
    assert isinstance(body["id"], int)
    assert isinstance(body["created_at"], int)
    assert body["created_at"] > 0


def test_post_feedback_defaults_signal_from_reason(
    store_and_client: tuple[Store, TestClient],
) -> None:
    """If the client omits `signal`, the daemon fills in the canonical
    value (1.0 for thumbs_up, -1.0 for thumbs_down, etc.). Lets the UI
    POST just `{query_id, node_id, reason}` from a thumbs button."""
    store, client = store_and_client
    qid, node_id = _seed(store)

    r = client.post(
        "/v1/feedback",
        json={
            "query_id": qid,
            "node_id": node_id,
            "reason": "thumbs_down",
            # signal intentionally omitted
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["signal"] == -1.0


def test_post_feedback_is_idempotent_on_triple(
    store_and_client: tuple[Store, TestClient],
) -> None:
    """A double-clicked thumbs button POSTs twice; the daemon must
    converge to a single feedback_event row."""
    store, client = store_and_client
    qid, node_id = _seed(store)
    payload = {"query_id": qid, "node_id": node_id, "reason": "thumbs_up"}

    first = client.post("/v1/feedback", json=payload)
    second = client.post("/v1/feedback", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    events = store.list_feedback_events(query_id=qid)
    assert len(events) == 1


def test_post_feedback_rejects_unknown_reason(
    store_and_client: tuple[Store, TestClient],
) -> None:
    store, client = store_and_client
    qid, node_id = _seed(store)
    r = client.post(
        "/v1/feedback",
        json={"query_id": qid, "node_id": node_id, "reason": "emoji_smiley"},
    )
    # Pydantic enum validation -> 422; or the explicit reason check ->
    # 400. Either is acceptable per FastAPI conventions; accept both.
    assert r.status_code in (400, 422), r.text


def test_post_feedback_rejects_signal_out_of_range(
    store_and_client: tuple[Store, TestClient],
) -> None:
    """Signal must be in [-1.0, 1.0]. A client sending 99 (probably a
    bug) should be rejected at validation time, not silently stored."""
    store, client = store_and_client
    qid, node_id = _seed(store)
    r = client.post(
        "/v1/feedback",
        json={
            "query_id": qid,
            "node_id": node_id,
            "signal": 99.0,
            "reason": "thumbs_up",
        },
    )
    assert r.status_code == 422, r.text


def test_post_feedback_unknown_query_id_returns_404(
    store_and_client: tuple[Store, TestClient],
) -> None:
    """Feedback for a query that doesn't exist is meaningless. SQLite's
    FK enforcement would error at insert time; the server should map
    that to a clean 404 with a useful message."""
    store, client = store_and_client
    _, node_id = _seed(store)
    r = client.post(
        "/v1/feedback",
        json={
            "query_id": "does-not-exist",
            "node_id": node_id,
            "reason": "thumbs_up",
        },
    )
    assert r.status_code == 404, r.text


# --- GET --------------------------------------------------------------------


def test_get_feedback_lists_by_query_id_newest_first(
    store_and_client: tuple[Store, TestClient],
) -> None:
    store, client = store_and_client
    qid, node_id = _seed(store)
    # Two events: cite_copied first, then thumbs_up. List should put
    # the newer one first.
    store.log_feedback_event(
        query_id=qid, node_id=node_id, signal=0.5, reason="cite_copied", when=1000
    )
    store.log_feedback_event(
        query_id=qid, node_id=node_id, signal=1.0, reason="thumbs_up", when=2000
    )

    r = client.get(f"/v1/feedback?query_id={qid}")
    assert r.status_code == 200
    rows = r.json()
    assert [row["reason"] for row in rows] == ["thumbs_up", "cite_copied"]


def test_get_feedback_unknown_query_id_returns_empty_list(
    store_and_client: tuple[Store, TestClient],
) -> None:
    """An unknown query_id is not a 404 -- it's "no feedback yet", which
    is the normal state for every brand-new query. UI calls this every
    time it renders hits; making the empty case 200 keeps the path
    boring."""
    _, client = store_and_client
    r = client.get("/v1/feedback?query_id=never-existed")
    assert r.status_code == 200
    assert r.json() == []


def test_get_feedback_requires_query_id_or_node_id(
    store_and_client: tuple[Store, TestClient],
) -> None:
    """A bare GET /v1/feedback with no filter would dump every feedback
    row -- not useful and accidentally expensive. Require at least one
    of query_id / node_id."""
    _, client = store_and_client
    r = client.get("/v1/feedback")
    assert r.status_code == 400, r.text
