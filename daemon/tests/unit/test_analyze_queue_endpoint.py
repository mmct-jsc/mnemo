"""v5.22.0 Phase 4a -- /v1/analyze/queue endpoints (read + status flip).

GET lists the persisted, de-duplicated proactive findings (open by
default), paginated, with counts + inline node_labels. POST flips ONE
finding's status (the user's ignore / restore) -- queue metadata, NOT a
node edit (honours the forever no-silent-edits anti-goal).
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from mnemo import server
from mnemo.store import Node, Store, _finding_fingerprint


class _FakeEmbedder:
    dim = 384
    _model = None

    def embed_text(self, text):
        return [0.0] * 384

    def embed_batch(self, texts):
        return [[0.0] * 384 for _ in texts]


@pytest.fixture
def app_store(tmp_path):
    store = Store(tmp_path / "mnemo.db")
    app = server.create_app(store=store, embedder=_FakeEmbedder())
    yield TestClient(app), store
    store.close()


def _stale(node_id: str) -> dict:
    return {
        "type": "stale",
        "node_ids": [node_id],
        "description": f"{node_id} SUPERSEDED",
        "severity": "low",
    }


def _orphan(node_id: str, missing: list[str]) -> dict:
    return {
        "type": "orphan_reference",
        "node_ids": [node_id],
        "description": f"{node_id} cites missing",
        "severity": "high",
        "missing_targets": sorted(missing),
    }


def _mknode(store: Store, *, id: str, name: str) -> None:
    now = int(time.time())
    store.upsert_node(
        Node(
            id=id,
            type="memory_feedback",
            name=name,
            description="",
            body="",
            source_path=f"/m/{id}.md",
            source_kind="memory",
            project_key=None,
            frontmatter_json=None,
            hash="",
            created_at=now,
            updated_at=now,
        )
    )


def test_queue_lists_open_with_counts(app_store) -> None:
    client, store = app_store
    store.reconcile_audit_queue([_stale("a"), _orphan("b", ["x"])], ("stale", "orphan_reference"))
    r = client.get("/v1/analyze/queue")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2
    assert data["counts"]["open"] == 2
    assert {f["type"] for f in data["findings"]} == {"stale", "orphan_reference"}


def test_queue_includes_node_labels(app_store) -> None:
    client, store = app_store
    _mknode(store, id="a", name="Alpha")
    store.reconcile_audit_queue([_stale("a")], ("stale",))
    data = client.get("/v1/analyze/queue").json()
    assert data["node_labels"]["a"]["name"] == "Alpha"


def test_queue_pagination(app_store) -> None:
    client, store = app_store
    store.reconcile_audit_queue([_stale(f"n{i}") for i in range(30)], ("stale",))
    page1 = client.get("/v1/analyze/queue?limit=25&offset=0").json()
    page2 = client.get("/v1/analyze/queue?limit=25&offset=25").json()
    assert len(page1["findings"]) == 25
    assert len(page2["findings"]) == 5
    assert page1["total"] == 30


def test_status_flip_dismiss(app_store) -> None:
    client, store = app_store
    store.reconcile_audit_queue([_stale("a")], ("stale",))
    fp = _finding_fingerprint(_stale("a"))
    r = client.post(f"/v1/analyze/queue/{fp}/status", json={"status": "dismissed"})
    assert r.status_code == 200, r.text
    assert client.get("/v1/analyze/queue?status=open").json()["total"] == 0
    assert client.get("/v1/analyze/queue?status=dismissed").json()["total"] == 1


def test_status_open_excludes_dismissed(app_store) -> None:
    client, store = app_store
    store.reconcile_audit_queue([_stale("a"), _stale("b")], ("stale",))
    fp = _finding_fingerprint(_stale("a"))
    client.post(f"/v1/analyze/queue/{fp}/status", json={"status": "dismissed"})
    open_data = client.get("/v1/analyze/queue?status=open").json()
    assert {f["node_ids"][0] for f in open_data["findings"]} == {"b"}


def test_status_unknown_fingerprint_404(app_store) -> None:
    client, _ = app_store
    r = client.post("/v1/analyze/queue/deadbeef/status", json={"status": "dismissed"})
    assert r.status_code == 404


def test_status_rejects_invalid_value(app_store) -> None:
    client, store = app_store
    store.reconcile_audit_queue([_stale("a")], ("stale",))
    fp = _finding_fingerprint(_stale("a"))
    r = client.post(f"/v1/analyze/queue/{fp}/status", json={"status": "bogus"})
    assert r.status_code == 422
