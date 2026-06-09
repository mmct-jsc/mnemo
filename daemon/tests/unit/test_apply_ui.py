"""v5.23.0 Phase 4b -- /analyze Apply button + preview/confirm modal.

orphan_reference queue rows get an Apply button -> fetches the read-only
preview -> a modal shows the before/after + the removed tokens -> Confirm
posts the preview's node_hash to apply. Structural contract only (string
presence in the rendered HTML); behavior is exercised live via preview.
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


def test_orphan_rows_have_apply_button(client) -> None:
    raw = client.get("/analyze").text
    assert "openApply(" in raw, "queue rows must wire an Apply action"
    assert "Apply</button>" in raw, "there must be an Apply button label"
    assert "f.type === 'orphan_reference'" in raw, "Apply is gated to orphan_reference rows"


def test_apply_modal_binds_before_after_removed(client) -> None:
    raw = client.get("/analyze").text
    assert "applyModal.before" in raw
    assert "applyModal.after" in raw
    assert "applyModal.removed" in raw


def test_apply_previews_then_confirms_with_node_hash(client) -> None:
    raw = client.get("/analyze").text
    assert "/apply/preview" in raw, "Apply must PREVIEW (read-only) first"
    assert "node_hash: this.applyModal.node_hash" in raw, (
        "Confirm must post the preview's node_hash (the stale-check handshake)"
    )


def test_no_apply_all(client) -> None:
    text = client.get("/analyze").text.lower()
    for danger in ("apply all", "auto-fix", "fix all"):
        assert danger not in text, f"Phase 4b anti-goal violated: page exposes {danger!r}"
