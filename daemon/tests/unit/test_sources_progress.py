"""Phase 3 of v2.2: Sources page consumes /v1/reindex/events.

We can't execute the Alpine factory in pytest (no Node toolchain),
but we CAN assert the template ships:

  - the new progress markup with the standard ``.reindex-progress``
    block + a ``.bar-track`` / ``.bar-fill`` that reuses the
    palette-driven CSS;
  - a stable counter / current-file / summary surface so the JS
    has something to bind to;
  - a hook into ``mnemoStreamFromSSE`` (the primitive from phase 1)
    pointed at ``/v1/reindex/events`` (the endpoint from phase 2).

Design: docs/plans/2026-05-14-ux-progressive-design.md § 3.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

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


# --- Progress block renders ----------------------------------------------


def test_sources_page_has_progress_block(client: TestClient) -> None:
    """The page must include a ``.reindex-progress`` container."""
    r = client.get("/sources-page")
    assert r.status_code == 200
    body = r.text
    assert "reindex-progress" in body, (
        "sources.html must include the .reindex-progress block (phase 3, see design doc § 3)"
    )


def test_sources_page_progress_block_has_bar_track(client: TestClient) -> None:
    """The progress block must reuse the palette-driven .bar-fill."""
    r = client.get("/sources-page")
    body = r.text
    # bar-track + bar-fill are the shared palette primitive.
    # We grep for both within the reindex-progress block.
    pattern = re.compile(
        r'class="reindex-progress"[\s\S]*?bar-track[\s\S]*?bar-fill',
        re.MULTILINE,
    )
    assert pattern.search(body), (
        "the reindex-progress block must contain .bar-track + .bar-fill "
        "so it reuses the dashboard's palette-driven bar primitive"
    )


def test_sources_page_progress_has_counter_and_current_file(
    client: TestClient,
) -> None:
    """The progress UI must expose idx / total + a current-file readout.

    These are the user-visible "Are we doing anything? What's it on?"
    affordances. We bind them via ``progress.idx`` / ``progress.total``
    / ``progress.currentFile`` on the Alpine state.
    """
    r = client.get("/sources-page")
    body = r.text
    for token in ("progress.idx", "progress.total", "progress.currentFile"):
        assert token in body, (
            f"sources.html must reference {token!r} in the progress block "
            "so the SSE handler can update it"
        )


def test_sources_page_wires_sse_endpoint(client: TestClient) -> None:
    """The factory must subscribe to ``/v1/reindex/events`` via
    ``mnemoStreamFromSSE`` -- the phase 2 endpoint consumed via the
    phase 1 primitive."""
    r = client.get("/sources-page")
    body = r.text
    assert "mnemoStreamFromSSE" in body, (
        "sources.html must call window.mnemoStreamFromSSE -- see design § 3 for the wiring"
    )
    assert "/v1/reindex/events" in body, "sources.html must point the stream at /v1/reindex/events"


def test_sources_page_progress_has_cancel_affordance(client: TestClient) -> None:
    """The progress block must expose a Stop button that closes the stream.

    Implementation uses an ``AbortController`` whose ``signal`` is
    passed into ``mnemoStreamFromSSE``; the Stop button calls its
    ``.abort()``.
    """
    r = client.get("/sources-page")
    body = r.text
    assert "AbortController" in body, (
        "sources.html must construct an AbortController so the user can "
        "cancel an in-flight reindex stream"
    )
    # The Stop button could be labeled 'stop' or 'cancel'. We accept either.
    assert re.search(r">(stop|cancel)<", body, re.IGNORECASE), (
        "sources.html must have a Stop/Cancel button inside the progress block"
    )


def test_sources_page_progress_summary_field(client: TestClient) -> None:
    """After ``done``, the bar shows ``added / updated / unchanged /
    removed`` totals. These bind to ``progress.done`` + the same
    fields the SSE ``done`` event payload carries."""
    r = client.get("/sources-page")
    body = r.text
    assert "progress.done" in body, (
        "sources.html must read progress.done so the summary appears "
        "only after the done event arrives"
    )
    for field in ("progress.added", "progress.updated", "progress.removed"):
        assert field in body, (
            f"sources.html must surface {field!r} in the summary line "
            "(the SSE done event carries these counts)"
        )


# --- Backward compat: legacy POST flow still wired ----------------------


def test_sources_page_still_has_post_fallback(client: TestClient) -> None:
    """The phase 3 wiring is feature-additive. If SSE fails (proxies,
    legacy browsers, etc.) the page falls back to the existing POST +
    poll pattern. We assert the POST call site is still present so
    the fallback path stays intact for v2.2.x."""
    r = client.get("/sources-page")
    body = r.text
    # The POST fallback fires fetch('/v1/reindex', { method: 'POST' }).
    # We tolerate single or double quotes around the path.
    pattern = re.compile(
        r"""fetch\(\s*['"]/v1/reindex['"]\s*,\s*\{\s*method:\s*['"]POST['"]""",
    )
    assert pattern.search(body), (
        "the POST /v1/reindex fallback must remain wired in sources.html "
        "until v2.3 (one-minor-version overlap per the rollback plan)"
    )
