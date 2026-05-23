"""v5.12.0 -- /analyze UI page contract.

The UI page is the human-facing surface for the knowledge auditor.
Per the design doc, it renders findings in a sortable table — type,
severity, node ids, description. Cosmetic only in Phase 1; no edit
buttons (anti-goal: no silent edits).

We assert the page exists + is reachable + renders the expected
shape. The full Alpine + HTMX behavior is exercised at the
preview-tool level in dev; the unit test just locks the route +
the structural skeleton.
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


def test_analyze_page_is_routed(client) -> None:
    """GET /analyze returns 200."""
    r = client.get("/analyze")
    assert r.status_code == 200, r.text


def test_analyze_page_returns_html(client) -> None:
    """The page is HTML (not JSON / not blank)."""
    r = client.get("/analyze")
    assert "text/html" in r.headers.get("content-type", ""), (
        f"expected HTML content-type; got {r.headers.get('content-type')!r}"
    )


def test_analyze_page_mentions_canonical_strings(client) -> None:
    """The page should mention the auditor concept + the 3 detector
    names so users orient. Lock the canonical vocabulary."""
    r = client.get("/analyze")
    text = r.text.lower()
    # Page heading / intro vocabulary.
    assert "knowledge auditor" in text or "analyze" in text, (
        "page should introduce itself as the knowledge auditor"
    )
    # Detector names so the user knows what we look for.
    for detector in ("stale", "duplicates", "orphan"):
        assert detector in text, f"page should mention the {detector!r} detector"


def test_analyze_page_has_no_edit_buttons(client) -> None:
    """Phase 1 anti-goal: no silent edits. The UI must NOT expose any
    automated apply/fix button -- the auditor surfaces; the user acts
    via existing tools."""
    r = client.get("/analyze")
    text = r.text.lower()
    # Sentinel words that would indicate destructive automation.
    for danger in ("apply all", "auto-fix", "fix all"):
        assert danger not in text, (
            f"Phase 1 anti-goal violated: page mentions {danger!r}; "
            f"the auditor must SURFACE only, never apply edits."
        )
