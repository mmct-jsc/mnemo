"""Phase 2 of the v2.2 progressive-UX rollout: reindex SSE events.

Two contracts under test:

  1. ``ingest.reindex_events(store, ...)`` is a generator that yields
     ``(event_name, payload_dict)`` tuples in this order:

       ('start', {'started_at': int})
       ('file',  {'idx': 1, 'path': str, 'status': str, ...})    [N times]
       ('done',  {'added': int, 'updated': int, 'unchanged': int,
                  'removed': int, 'errors': list, 'duration_ms': int})

     The synchronous ``ingest.reindex()`` keeps its existing return
     shape (it now consumes the generator under the hood).

  2. ``GET /v1/reindex/events`` is a Server-Sent Events endpoint that
     emits the generator's events as ``event: <name>\\ndata: <json>``
     frames, terminated when the generator finishes.

The existing ``POST /v1/reindex`` is unchanged -- a regression test
locks that contract.

Design: docs/plans/2026-05-14-ux-progressive-design.md § 2.
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo import ingest
from mnemo.server import create_app
from mnemo.store import Store
from tests.conftest import FakeEmbedder


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


def _seed_memory_dir(tmp_path: Path, n_files: int = 3) -> Path:
    """Drop ``n_files`` tiny memory-typed files into a fresh dir."""
    src = tmp_path / "mem"
    src.mkdir()
    for i in range(n_files):
        (src / f"feedback_{i}.md").write_text(
            textwrap.dedent(
                f"""\
                ---
                name: rule-{i}
                description: short rule {i}
                type: feedback
                ---
                Body of rule {i}.
                """
            ),
            encoding="utf-8",
        )
    return src


# --- Generator contract --------------------------------------------------


def test_reindex_events_yields_start_first(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    """The first event MUST be ``start`` with a Unix timestamp."""
    src = _seed_memory_dir(tmp_path)
    store.register_source(path=str(src), kind="memory_dir")

    events = list(ingest.reindex_events(store, embedder=fake_embedder))
    assert len(events) > 0, "reindex_events must yield at least one event"
    name, payload = events[0]
    assert name == "start", f"first event must be 'start', got {name!r}"
    assert isinstance(payload, dict)
    assert "started_at" in payload, "start event must include started_at"
    assert isinstance(payload["started_at"], int), "started_at must be a Unix timestamp"


def test_reindex_events_yields_one_file_event_per_seen_file(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    """Each scanned file produces exactly one ``file`` event."""
    src = _seed_memory_dir(tmp_path, n_files=3)
    store.register_source(path=str(src), kind="memory_dir")

    events = list(ingest.reindex_events(store, embedder=fake_embedder))
    file_events = [(n, p) for (n, p) in events if n == "file"]
    assert len(file_events) == 3, (
        f"expected 3 file events for 3 seeded files, got {len(file_events)}"
    )
    for i, (_, payload) in enumerate(file_events, start=1):
        assert payload["idx"] == i, f"idx must increment from 1; got {payload['idx']}"
        assert "path" in payload, "file event must include path"
        assert payload["status"] in {"indexed", "updated", "unchanged", "error"}, (
            f"unexpected status {payload['status']!r}"
        )


def test_reindex_events_yields_done_last_with_totals(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    """The final event is ``done`` with summary counts + duration."""
    src = _seed_memory_dir(tmp_path, n_files=2)
    store.register_source(path=str(src), kind="memory_dir")

    events = list(ingest.reindex_events(store, embedder=fake_embedder))
    name, payload = events[-1]
    assert name == "done", f"final event must be 'done', got {name!r}"
    for field in ("added", "updated", "unchanged", "removed", "errors", "duration_ms"):
        assert field in payload, f"done event missing {field}"
    assert payload["added"] == 2, f"expected added=2, got {payload['added']}"
    assert payload["duration_ms"] >= 0, "duration_ms must be non-negative"


def test_reindex_events_idempotent_second_run(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    """Re-running yields the same file count with status='unchanged'."""
    src = _seed_memory_dir(tmp_path, n_files=2)
    store.register_source(path=str(src), kind="memory_dir")

    list(ingest.reindex_events(store, embedder=fake_embedder))  # first run
    events = list(ingest.reindex_events(store, embedder=fake_embedder))
    file_events = [p for (n, p) in events if n == "file"]
    assert len(file_events) == 2
    for p in file_events:
        assert p["status"] == "unchanged", (
            f"on second run all files should be unchanged; got {p['status']!r} for {p['path']!r}"
        )


def test_reindex_still_returns_same_report_shape(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    """``ingest.reindex()`` keeps its existing tally-return contract.

    Existing callers (the POST /v1/reindex route + the CLI) must not
    break when the generator lands under the hood.
    """
    src = _seed_memory_dir(tmp_path, n_files=2)
    store.register_source(path=str(src), kind="memory_dir")

    report = ingest.reindex(store, embedder=fake_embedder)
    assert report.added == 2
    assert report.updated == 0
    assert report.unchanged == 0
    assert report.removed == 0
    assert report.errors == []


# --- HTTP / SSE wire contract --------------------------------------------


def test_sse_endpoint_serves_event_stream_content_type(client: TestClient, tmp_path: Path) -> None:
    """``GET /v1/reindex/events`` returns text/event-stream."""
    src = _seed_memory_dir(tmp_path, n_files=1)
    client.post("/v1/sources", json={"path": str(src), "kind": "memory_dir"})

    with client.stream("GET", "/v1/reindex/events") as r:
        assert r.status_code == 200, f"expected 200, got {r.status_code}"
        ctype = r.headers.get("content-type", "").lower()
        assert "text/event-stream" in ctype, (
            f"expected text/event-stream content type, got {ctype!r}"
        )


def test_sse_endpoint_emits_start_file_done_sequence(client: TestClient, tmp_path: Path) -> None:
    """The wire format is ``event: <name>\\ndata: <json>\\n\\n`` repeated."""
    src = _seed_memory_dir(tmp_path, n_files=2)
    client.post("/v1/sources", json={"path": str(src), "kind": "memory_dir"})

    with client.stream("GET", "/v1/reindex/events") as r:
        body = "".join(r.iter_text())

    # Parse the SSE frames out.
    events = []
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        name = None
        data = None
        for line in frame.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:") :].strip())
        if name is not None:
            events.append((name, data))

    assert len(events) >= 3, f"expected at least start+file+done, got {len(events)} events"
    assert events[0][0] == "start", f"first SSE event must be start; got {events[0][0]!r}"
    assert events[-1][0] == "done", f"last SSE event must be done; got {events[-1][0]!r}"
    file_count = sum(1 for (n, _) in events if n == "file")
    assert file_count == 2, f"expected 2 file events for 2 seeded files; got {file_count}"


def test_post_reindex_still_works_after_refactor(client: TestClient, tmp_path: Path) -> None:
    """Regression: POST /v1/reindex keeps its current synchronous JSON shape."""
    src = _seed_memory_dir(tmp_path, n_files=1)
    client.post("/v1/sources", json={"path": str(src), "kind": "memory_dir"})

    r = client.post("/v1/reindex")
    assert r.status_code == 200
    payload = r.json()
    for field in ("added", "updated", "unchanged", "removed", "errors"):
        assert field in payload, f"POST /v1/reindex response missing {field}"
    assert payload["added"] == 1


def test_sse_endpoint_respects_reindex_lock(client: TestClient, tmp_path: Path) -> None:
    """A concurrent run gets the 'busy' event then EOF -- no fan-out.

    Today we don't have async concurrent clients to truly race, but
    we can probe the busy-path by acquiring the lock manually before
    the request fires.
    """
    src = _seed_memory_dir(tmp_path, n_files=1)
    client.post("/v1/sources", json={"path": str(src), "kind": "memory_dir"})

    # Grab the lock from outside the request so the SSE handler can't
    # acquire it. The app's reindex_lock lives on its state object;
    # we reach into it via the running TestClient.
    state = client.app.state.mnemo_state  # populated by create_app
    assert state.reindex_lock.acquire(blocking=False), "lock should be free before this test"
    try:
        with client.stream("GET", "/v1/reindex/events") as r:
            assert r.status_code == 200
            body = "".join(r.iter_text())
    finally:
        state.reindex_lock.release()

    assert "event: busy" in body, "concurrent client must see a 'busy' event"
