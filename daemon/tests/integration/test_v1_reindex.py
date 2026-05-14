"""Integration tests for /v1/reindex and /v1/reindex/status (v1.1.1).

Covers:
- GET /v1/reindex/status reports `running=False` when idle.
- POST /v1/reindex acquires the lock for the duration of the run.
- Concurrent POST /v1/reindex returns 409 with an error+started_at body.
- GET /v1/reindex/status reports `running=True` mid-flight, then flips
  back to False once the running call returns.

These tests use a slow ``ingest.reindex`` monkeypatch so we can observe
the in-flight state from a parallel thread.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo import ingest as ingest_module
from mnemo.embed import Embedder
from mnemo.server import create_app
from mnemo.store import Store


class _FakeEmbedder(Embedder):
    """Trivial embedder for tests that don't exercise vector search."""

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
def client(tmp_path: Path) -> Iterator[TestClient]:
    db = tmp_path / "test.db"
    store = Store(db)
    embedder = _FakeEmbedder()
    app = create_app(store=store, embedder=embedder)
    with TestClient(app) as c:
        yield c


# --- status endpoint --------------------------------------------------------


def test_reindex_status_idle_when_no_run_in_flight(client: TestClient) -> None:
    r = client.get("/v1/reindex/status")
    assert r.status_code == 200
    body = r.json()
    assert body == {"running": False, "started_at": None}


def test_reindex_status_reports_running_mid_flight(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch ingest.reindex_events to block on an event so we can
    observe the running state from a second request before the first
    one returns.

    v2.6: server now drives reindex_events directly (was: ingest.reindex
    wrapper) so the slow stub must be a generator.
    """
    started = threading.Event()
    release = threading.Event()

    original = ingest_module.reindex_events

    def slow_reindex_events(*args: object, **kwargs: object):
        started.set()
        # Wait until the test releases us, then run the real thing.
        if not release.wait(timeout=5):
            pytest.fail("test did not release the slow reindex within 5 s")
        yield from original(*args, **kwargs)

    monkeypatch.setattr(ingest_module, "reindex_events", slow_reindex_events)

    # Kick off the slow reindex in a background thread.
    result: dict[str, object] = {}

    def fire() -> None:
        r = client.post("/v1/reindex?embed=false")
        result["status"] = r.status_code
        result["body"] = r.json()

    t = threading.Thread(target=fire, daemon=True)
    t.start()

    # Wait until we know the slow reindex is in-flight.
    assert started.wait(timeout=5), "slow reindex never started"

    # Now /v1/reindex/status must report running=True with a timestamp.
    s = client.get("/v1/reindex/status")
    assert s.status_code == 200
    body = s.json()
    assert body["running"] is True
    assert isinstance(body["started_at"], int)
    assert body["started_at"] > 0

    # Release the slow reindex and wait for the request to complete.
    release.set()
    t.join(timeout=5)
    assert not t.is_alive(), "background reindex did not return"

    # After completion the state flips back.
    s2 = client.get("/v1/reindex/status")
    assert s2.json() == {"running": False, "started_at": None}
    assert result["status"] == 200


# --- 409 on concurrent --------------------------------------------------------


def test_concurrent_reindex_returns_409_with_started_at(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second POST /v1/reindex while one is in-flight returns HTTP 409
    with a detail payload the UI can use to show "running since HH:MM"."""
    started = threading.Event()
    release = threading.Event()
    original = ingest_module.reindex_events

    def slow_reindex_events(*args: object, **kwargs: object):
        started.set()
        if not release.wait(timeout=5):
            pytest.fail("test did not release the slow reindex within 5 s")
        yield from original(*args, **kwargs)

    monkeypatch.setattr(ingest_module, "reindex_events", slow_reindex_events)

    first_result: dict[str, object] = {}

    def fire_first() -> None:
        r = client.post("/v1/reindex?embed=false")
        first_result["status"] = r.status_code
        first_result["body"] = r.json()

    t = threading.Thread(target=fire_first, daemon=True)
    t.start()
    assert started.wait(timeout=5), "slow reindex never started"

    # Second concurrent POST should be refused with 409.
    second_before = int(time.time())
    second = client.post("/v1/reindex?embed=false")
    assert second.status_code == 409, second.text
    payload = second.json()
    # FastAPI nests our custom dict under "detail".
    detail = payload.get("detail", payload)
    assert detail["error"] == "reindex_in_progress"
    assert isinstance(detail["started_at"], int)
    # Sanity-check the timestamp is recent.
    assert second_before - 5 <= detail["started_at"] <= second_before + 5

    # Release the first call and let it finish cleanly.
    release.set()
    t.join(timeout=5)
    assert first_result["status"] == 200

    # After release the lock is free again: a fresh POST succeeds.
    third = client.post("/v1/reindex?embed=false")
    assert third.status_code == 200


def test_reindex_lock_released_on_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ingest.reindex raises, the lock must still be released so
    subsequent calls don't hang at 409 forever."""

    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("intentional test failure")

    monkeypatch.setattr(ingest_module, "reindex_events", boom)

    # The endpoint surfaces the error as a 500; the lock should be free
    # afterward.
    try:
        r = client.post("/v1/reindex?embed=false")
        # TestClient by default re-raises server exceptions; if it does
        # not, we should still see a non-200.
        assert r.status_code >= 500
    except RuntimeError:
        # TestClient(raise_server_exceptions=True) is the default; the
        # RuntimeError bubbles up. Either way the finally: block in the
        # endpoint must have released the lock.
        pass

    # Confirm the lock is free.
    s = client.get("/v1/reindex/status")
    assert s.json()["running"] is False
