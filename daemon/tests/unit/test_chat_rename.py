"""C3 (v4.3): conversation rename. Backend (PATCH /v1/chat/{id} +
ChatPatchIn.name + store.rename_conversation) was already COMPLETE in
v3.2 -- the frontend was the only missing piece."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Store
from tests.conftest import FakeEmbedder

_UI = Path(__file__).resolve().parents[2] / "mnemo" / "ui"
CHAT_JS = (_UI / "static" / "chat.js").read_text(encoding="utf-8")
CHAT_RAIL = (_UI / "templates" / "_chat_rail.html").read_text(encoding="utf-8")


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


def test_factory_has_rename_conversation_calling_patch() -> None:
    assert "renameConversation" in CHAT_JS, "frontend rename was the missing piece"
    assert "PATCH" in CHAT_JS
    assert "/v1/chat/" in CHAT_JS


def test_rail_has_inline_rename_affordance() -> None:
    # the shared rail wires it (so BOTH surfaces get rename), gated by
    # the CHAT_SURFACES matrix:
    assert "renameConversation(" in CHAT_RAIL
    assert "chat_surfaces[surface].rename" in CHAT_RAIL
    assert 'class="cv-ren"' in CHAT_RAIL


def test_backend_rename_roundtrip_still_works(client: TestClient) -> None:
    """Documents the already-complete backend contract."""
    conv = client.post("/v1/chat", json={}).json()
    r = client.patch(f"/v1/chat/{conv['id']}", json={"name": "Renamed"})
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"
