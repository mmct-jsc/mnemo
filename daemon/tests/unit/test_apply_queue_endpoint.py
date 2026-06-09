"""v5.23.0 Phase 4b -- the apply endpoints (preview + confirm-then-apply).

POST .../apply/preview is READ-ONLY (before/after + the confirm token).
POST .../apply is the FIRST node mutation -- gated by the node-hash the
preview returned: 200 applied / 404 unknown / 422 not-applyable / 409 stale.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from mnemo import server
from mnemo.store import Node, Store, _finding_fingerprint

DEAD = "d" * 32


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


def _mknode(store: Store, *, id: str, body: str) -> None:
    now = int(time.time())
    store.upsert_node(
        Node(
            id=id,
            type="memory_feedback",
            name=id,
            description="",
            body=body,
            source_path=f"/m/{id}.md",
            source_kind="memory",
            project_key=None,
            frontmatter_json=None,
            hash="h-" + id,
            created_at=now,
            updated_at=now,
        )
    )


def _seed_orphan(store: Store, node_id: str, missing: list[str]) -> str:
    finding = {
        "type": "orphan_reference",
        "node_ids": [node_id],
        "description": "cites missing",
        "severity": "high",
        "missing_targets": sorted(missing),
    }
    store.reconcile_audit_queue([finding], ("orphan_reference",))
    return _finding_fingerprint(finding)


def test_preview_returns_before_after(app_store) -> None:
    client, store = app_store
    _mknode(store, id="A", body=f"See [mnemo:{DEAD}] now.")
    fp = _seed_orphan(store, "A", [DEAD])
    r = client.post(f"/v1/analyze/queue/{fp}/apply/preview")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["applyable"] is True
    assert d["removed"] == [DEAD]
    assert f"[mnemo:{DEAD}]" not in d["after"]
    assert d["node_hash"]
    assert store.get_audit_finding(fp).status == "open", "preview must be read-only"


def test_preview_unknown_404(app_store) -> None:
    client, _ = app_store
    assert client.post("/v1/analyze/queue/nope/apply/preview").status_code == 404


def test_apply_happy_path(app_store) -> None:
    client, store = app_store
    _mknode(store, id="A", body=f"See [mnemo:{DEAD}] now.")
    fp = _seed_orphan(store, "A", [DEAD])
    pv = client.post(f"/v1/analyze/queue/{fp}/apply/preview").json()
    r = client.post(f"/v1/analyze/queue/{fp}/apply", json={"node_hash": pv["node_hash"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "resolved"
    assert f"[mnemo:{DEAD}]" not in store.get_node("A").body
    assert store.get_audit_finding(fp).status == "resolved"


def test_apply_placeholder_422(app_store) -> None:
    client, store = app_store
    _mknode(store, id="B", body="cite as [mnemo:<id>] here")
    fp = _seed_orphan(store, "B", ["<id>"])
    pv = client.post(f"/v1/analyze/queue/{fp}/apply/preview").json()
    r = client.post(f"/v1/analyze/queue/{fp}/apply", json={"node_hash": pv["node_hash"]})
    assert r.status_code == 422


def test_apply_stale_409_leaves_node_untouched(app_store) -> None:
    client, store = app_store
    _mknode(store, id="A", body=f"See [mnemo:{DEAD}] now.")
    fp = _seed_orphan(store, "A", [DEAD])
    r = client.post(f"/v1/analyze/queue/{fp}/apply", json={"node_hash": "wrong-hash"})
    assert r.status_code == 409
    assert f"[mnemo:{DEAD}]" in store.get_node("A").body
    assert store.get_audit_finding(fp).status == "open"


def test_apply_unknown_404(app_store) -> None:
    client, _ = app_store
    r = client.post("/v1/analyze/queue/nope/apply", json={"node_hash": "x"})
    assert r.status_code == 404
