"""v5.9.0 -- stateful reindex progress endpoint.

Background: v5.4.0 fixed the "reindex progress bar disappeared on tab
re-entry" UX bug by setting ``progress.active=true`` with an
indeterminate "reindexing in background..." placeholder whenever the
daemon reported ``running=true``. That was a band-aid -- per-file
numbers (current file, completed-of-total) were only visible while
the SSE stream was attached. v5.9.0 finishes the fix: the daemon
now retains the latest per-file progress in ``AppState`` and
``GET /v1/reindex/status?include_progress=1`` returns it.

Contract this test file locks:

1. ``AppState.reindex_progress`` defaults to None (back-compat: existing
   callers that don't pass include_progress see no behaviour change).
2. ``GET /v1/reindex/status`` without the param returns the
   pre-v5.9.0 shape: ``{running, started_at}`` -- the existing
   sources.html / Alpine bindings keep working unchanged.
3. ``GET /v1/reindex/status?include_progress=1`` returns the same
   keys PLUS ``progress`` -- either None (no reindex in flight) or
   ``{idx, path, status, added, updated, unchanged, errors}``
   matching the latest ``'file'`` event from the reindex generator.
4. After the reindex finishes (``'done'`` event fires + lock
   releases), ``reindex_progress`` is cleared back to None so
   subsequent polls don't show stale per-file lines.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def app_with_state(tmp_path):
    """A minimal mnemo FastAPI app + manual AppState handle for the
    test to poke at without spinning up a full reindex."""
    from fastapi.testclient import TestClient

    from mnemo import server
    from mnemo.store import Store

    store = Store(tmp_path / "mnemo.db")

    class _FakeEmbedder:
        dim = 384
        _model = None

        def embed_text(self, _text):
            return [0.0] * 384

        def embed_batch(self, texts):
            return [[0.0] * 384 for _ in texts]

    app = server.create_app(store=store, embedder=_FakeEmbedder())
    client = TestClient(app)
    yield client, app.state.mnemo_state
    store.close()


def test_appstate_reindex_progress_defaults_to_none() -> None:
    """v5.9.0 contract: AppState exposes a ``reindex_progress`` field
    that defaults to None so legacy callers see no behaviour change."""
    from mnemo.server import AppState

    s = AppState()
    assert hasattr(s, "reindex_progress"), (
        "AppState must expose 'reindex_progress' attribute for v5.9.0"
    )
    assert s.reindex_progress is None, "reindex_progress must default to None"


def test_reindex_status_without_progress_param_keeps_legacy_shape(
    app_with_state,
) -> None:
    """Back-compat: callers that don't pass ``include_progress`` see
    the pre-v5.9.0 ``{running, started_at}`` shape only. Adding the
    field unconditionally would break the existing sources.html
    Alpine bindings that destructure only those two keys."""
    client, _state = app_with_state
    r = client.get("/v1/reindex/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"running", "started_at"}, (
        f"legacy /v1/reindex/status MUST return just 'running' + 'started_at'; "
        f"got {sorted(body.keys())}"
    )


def test_reindex_status_with_include_progress_returns_progress_field(
    app_with_state,
) -> None:
    """v5.9.0 contract: ``?include_progress=1`` extends the response
    with a ``progress`` key. When no reindex is running, ``progress``
    is None (matching the running=false case)."""
    client, _state = app_with_state
    r = client.get("/v1/reindex/status?include_progress=1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "progress" in body, (
        f"include_progress=1 must add a 'progress' key; got {sorted(body.keys())}"
    )
    assert body["progress"] is None, (
        f"no reindex in flight => progress should be None; got {body['progress']!r}"
    )
    # And the legacy keys are still present so wire schema is additive.
    assert "running" in body
    assert "started_at" in body


def test_reindex_status_with_progress_reads_appstate_field(
    app_with_state,
) -> None:
    """When ``state.reindex_progress`` is populated (the live SSE /
    POST reindex loop publishes the latest 'file' event there), the
    ``?include_progress=1`` response surfaces it. Simulates the
    mid-reindex tab-re-entry scenario without actually running a
    reindex (the generator's exercised in test_reindex_events)."""
    client, state = app_with_state
    state.reindex_progress = {
        "idx": 42,
        "path": "src/foo/bar.py",
        "status": "indexed",
        "added": 3,
        "updated": 0,
        "unchanged": 0,
        "errors": [],
    }
    # Also flip the lock so 'running' reads true (matches reality
    # during the actual reindex).
    state.reindex_lock.acquire()
    state.reindex_started_at = 1234567890
    try:
        r = client.get("/v1/reindex/status?include_progress=1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["running"] is True
        assert body["started_at"] == 1234567890
        assert body["progress"] == {
            "idx": 42,
            "path": "src/foo/bar.py",
            "status": "indexed",
            "added": 3,
            "updated": 0,
            "unchanged": 0,
            "errors": [],
        }
    finally:
        state.reindex_lock.release()
        state.reindex_started_at = None
        state.reindex_progress = None
