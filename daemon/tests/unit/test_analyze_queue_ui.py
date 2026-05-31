"""v5.22.0 Phase 4a -- /analyze Queue view + nav open-count badge.

The /analyze page gains a standing Queue view (the default landing view)
that lists the persisted findings the post-reindex auditor reconciled,
with status chips + a Dismiss/Restore control. The nav 'Analyze' link
shows an open-count badge. Structural contract only (string presence in
the rendered HTML); behavior is exercised live via the preview tool.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mnemo import server
from mnemo.store import Store


@pytest.fixture
def client(tmp_path):
    class _FakeEmbedder:
        dim = 384
        _model = None

        def embed_text(self, text):
            return [0.0] * 384

        def embed_batch(self, texts):
            return [[0.0] * 384 for _ in texts]

    store = Store(tmp_path / "mnemo.db")
    app = server.create_app(store=store, embedder=_FakeEmbedder())
    yield TestClient(app)
    store.close()


def test_queue_is_default_view(client) -> None:
    raw = client.get("/analyze").text
    assert "view: 'queue'" in raw, "the Queue must be the default landing view"
    assert "loadQueue" in raw, "the page must load the standing queue"
    assert "view === 'queue'" in raw


def test_queue_view_has_tabs(client) -> None:
    raw = client.get("/analyze").text
    assert "audit-views" in raw, "page should expose the Queue / Run audit view tabs"
    assert "view === 'adhoc'" in raw, "the on-demand Run audit view must remain reachable"


def test_queue_view_has_status_chips(client) -> None:
    raw = client.get("/analyze").text
    assert "status-chip" in raw, "queue should expose open/dismissed/resolved status chips"
    assert "setQueueStatus" in raw
    # the three lifecycle statuses are the chip set
    assert "'open', 'dismissed', 'resolved'" in raw


def test_queue_view_has_dismiss_and_restore(client) -> None:
    raw = client.get("/analyze").text
    assert "Dismiss" in raw, "open findings need a Dismiss control"
    assert "Restore" in raw, "dismissed/resolved findings need a Restore control"
    assert "dismiss(f.fingerprint)" in raw
    assert "reopen(f.fingerprint)" in raw


def test_queue_view_calls_queue_endpoint(client) -> None:
    raw = client.get("/analyze").text
    assert "/v1/analyze/queue" in raw, "the queue view must read GET /v1/analyze/queue"
    assert "/status" in raw, "dismiss/restore must POST the status flip endpoint"


def test_nav_has_analyze_badge(client) -> None:
    # The badge markup lives in base.html (shared layout), so it renders
    # on every page incl. /analyze.
    raw = client.get("/analyze").text
    assert "analyzeNavBadge" in raw, "nav Analyze link must carry the open-count badge component"
    assert "nav-badge" in raw


def test_queue_dismiss_is_metadata_not_a_node_edit(client) -> None:
    """Phase 4a anti-goal: Dismiss is queue metadata, never a node
    mutation. The page must not expose any node apply/fix automation."""
    text = client.get("/analyze").text.lower()
    for danger in ("apply all", "auto-fix", "fix all", "delete node"):
        assert danger not in text, f"Phase 4a anti-goal violated: page exposes {danger!r}"
