"""HTTP tests for v2.6 phase 6: source override + reindex report endpoints.

Covers:
- GET / POST / DELETE /v1/source_overrides
- GET /v1/reindex/report (404 when no run; payload after a run completes)
- Reindex broadcasts reindex_started + reindex_done on /v1/events
- Server caches the latest report into app.state.last_reindex_report
"""

from __future__ import annotations

import queue
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Store
from tests.conftest import FakeEmbedder


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


def _attach_subscriber(client: TestClient) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=64)
    state = client.app.state.mnemo_state
    with state.event_subscribers_lock:
        state.event_subscribers.append(q)
    return q


# --- /v1/source_overrides ---------------------------------------------------


def test_list_overrides_empty(client: TestClient) -> None:
    resp = client.get("/v1/source_overrides")
    assert resp.status_code == 200
    assert resp.json() == []


def test_batch_upsert_overrides(client: TestClient) -> None:
    resp = client.post(
        "/v1/source_overrides",
        json={
            "items": [
                {"source_path": "/a", "decision": "always_skip", "reason": "r1"},
                {"source_path": "/b", "decision": "always_keep", "reason": "r2"},
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    paths = {ov["source_path"] for ov in body}
    assert paths == {"/a", "/b"}


def test_overrides_listed_after_upsert(client: TestClient) -> None:
    client.post(
        "/v1/source_overrides",
        json={"items": [{"source_path": "/x", "decision": "always_skip"}]},
    )
    listed = client.get("/v1/source_overrides").json()
    assert any(ov["source_path"] == "/x" for ov in listed)


def test_delete_override(client: TestClient) -> None:
    client.post(
        "/v1/source_overrides",
        json={"items": [{"source_path": "/x", "decision": "always_skip"}]},
    )
    # POST with a body keeps the path inside the query; DELETE the path via
    # query parameter so we don't need to escape it inside the URL.
    resp = client.delete("/v1/source_overrides", params={"source_path": "/x"})
    assert resp.status_code == 200
    listed = client.get("/v1/source_overrides").json()
    assert not any(ov["source_path"] == "/x" for ov in listed)


def test_delete_override_returns_404_when_missing(client: TestClient) -> None:
    resp = client.delete("/v1/source_overrides", params={"source_path": "/missing"})
    assert resp.status_code == 404


def test_batch_upsert_rejects_unknown_decision(client: TestClient) -> None:
    resp = client.post(
        "/v1/source_overrides",
        json={"items": [{"source_path": "/x", "decision": "banana"}]},
    )
    assert resp.status_code == 400


# --- /v1/reindex/report -----------------------------------------------------


def _seed_memory_dir(tmp_path: Path, n_files: int = 2) -> Path:
    src = tmp_path / "memory"
    src.mkdir(exist_ok=True)
    for i in range(n_files):
        (src / f"f{i}.md").write_text(f"# file {i}")
    return src


def test_get_report_returns_404_before_any_reindex(client: TestClient) -> None:
    resp = client.get("/v1/reindex/report")
    assert resp.status_code == 404


def test_report_populated_after_post_reindex(
    client: TestClient, store: Store, tmp_path: Path
) -> None:
    src = _seed_memory_dir(tmp_path, n_files=3)
    store.register_source(path=str(src), kind="memory_dir")

    resp = client.post("/v1/reindex", params={"embed": "false"})
    assert resp.status_code == 200

    rep = client.get("/v1/reindex/report")
    assert rep.status_code == 200
    body = rep.json()
    for f in (
        "auto_skipped",
        "malformed",
        "suspicious",
        "indexed_count",
        "duration_ms",
        "finished_at",
    ):
        assert f in body
    assert body["indexed_count"] == 3


def test_report_populated_after_sse_reindex(
    client: TestClient, store: Store, tmp_path: Path
) -> None:
    """The /v1/reindex/events stream also caches the report."""
    src = _seed_memory_dir(tmp_path, n_files=2)
    store.register_source(path=str(src), kind="memory_dir")

    # Consume the entire SSE stream (TestClient is sync; iter_lines reads
    # until the generator finishes).
    with client.stream("GET", "/v1/reindex/events", params={"embed": "false"}) as stream:
        for _ in stream.iter_lines():
            pass

    rep = client.get("/v1/reindex/report")
    assert rep.status_code == 200
    assert rep.json()["indexed_count"] == 2


# --- Reindex broadcasts -----------------------------------------------------


def test_post_reindex_broadcasts_started_and_done(
    client: TestClient, store: Store, tmp_path: Path
) -> None:
    src = _seed_memory_dir(tmp_path, n_files=1)
    store.register_source(path=str(src), kind="memory_dir")
    q = _attach_subscriber(client)
    client.post("/v1/reindex", params={"embed": "false"})
    names: list[str] = []
    while not q.empty():
        name, _payload = q.get_nowait()
        names.append(name)
    assert "reindex_started" in names
    assert "reindex_done" in names


def test_sse_reindex_broadcasts_started_and_done(
    client: TestClient, store: Store, tmp_path: Path
) -> None:
    src = _seed_memory_dir(tmp_path, n_files=1)
    store.register_source(path=str(src), kind="memory_dir")
    q = _attach_subscriber(client)
    with client.stream("GET", "/v1/reindex/events", params={"embed": "false"}) as stream:
        for _ in stream.iter_lines():
            pass
    names: list[str] = []
    while not q.empty():
        name, _payload = q.get_nowait()
        names.append(name)
    assert "reindex_started" in names
    assert "reindex_done" in names
