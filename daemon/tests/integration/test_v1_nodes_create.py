"""Integration tests for POST /v1/nodes (v1.2 phase 7 housekeeping).

The HTTP create endpoint lets non-filesystem clients (the VS Code
"Add Note" command, future SDKs, scripts) drop a memory node into the
store without first writing a markdown file under the project's
memory dir.

Contract:
- Body is ``NodeCreateIn`` (type / name / body required; description,
  project_key, base, source_path, source_kind optional).
- Returns a full ``NodeOut`` of the newly created node.
- Auto-fills source_path with ``http://api/<uuid>`` when omitted so
  later watcher reconciliation doesn't try to read it from disk.
- Auto-fills source_kind to ``memory_dir`` when omitted -- matches the
  default kind for hand-written memory entries.
- 400 on unknown ``type`` or ``source_kind`` (validation surfaces the
  enum).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.embed import Embedder
from mnemo.server import create_app
from mnemo.store import Store


class _FakeEmbedder(Embedder):
    """Fake embedder so the daemon comes up without a real MiniLM."""

    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        self.model_name = "fake"
        self._cache_dir = Path("/tmp/mnemo-fake-cache")
        self._model = object()

    @property
    def dim(self) -> int:
        return 384

    def embed_text(self, text: str) -> list[float]:
        return [0.1 for _ in range(384)]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    db = tmp_path / "test.db"
    store = Store(db)
    app = create_app(store=store, embedder=_FakeEmbedder())
    with TestClient(app) as c:
        yield c


# --- Happy path -----------------------------------------------------------


def test_post_nodes_creates_and_returns_full_node(client: TestClient) -> None:
    r = client.post(
        "/v1/nodes",
        json={
            "type": "memory_user",
            "name": "favorite-editor",
            "description": "User prefers VS Code with the @mnemo participant.",
            "body": "Always offer @mnemo first when chat is available.",
            "project_key": "D--Repository-knowledge-base",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "memory_user"
    assert body["name"] == "favorite-editor"
    assert body["description"].startswith("User prefers")
    assert body["body"].startswith("Always offer")
    assert body["project_key"] == "D--Repository-knowledge-base"
    # Auto-filled defaults:
    assert body["source_kind"] == "memory_dir"
    assert body["source_path"].startswith("http://api/")
    # Sanity: the new node is queryable via GET /v1/nodes/{id}.
    again = client.get(f"/v1/nodes/{body['id']}")
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]


def test_post_nodes_respects_explicit_source_path_and_kind(
    client: TestClient,
) -> None:
    """Clients can pass explicit ``source_path`` / ``source_kind`` (e.g.
    a SaaS ingester emitting a stable URL like ``notion://<page-id>``)."""
    r = client.post(
        "/v1/nodes",
        json={
            "type": "memory_reference",
            "name": "notion-spec",
            "body": "External Notion doc.",
            "source_path": "notion://abc-123",
            "source_kind": "memory_dir",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_path"] == "notion://abc-123"


def test_post_nodes_base_flag_propagates(client: TestClient) -> None:
    """``base: true`` should land as a BASE-flagged node so the
    project-isolation hard-filter bypasses it for cross-project surfacing."""
    r = client.post(
        "/v1/nodes",
        json={
            "type": "memory_user",
            "name": "always-prefer-tests-first",
            "body": "TDD across all projects.",
            "base": True,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["base"] is True


# --- Validation -----------------------------------------------------------


def test_post_nodes_rejects_unknown_type(client: TestClient) -> None:
    r = client.post(
        "/v1/nodes",
        json={
            "type": "bogus_type",
            "name": "x",
            "body": "y",
        },
    )
    assert r.status_code in (400, 422), r.text


def test_post_nodes_requires_name_and_body(client: TestClient) -> None:
    r = client.post(
        "/v1/nodes",
        json={"type": "memory_user"},  # name + body missing
    )
    assert r.status_code == 422, r.text


def test_post_nodes_rejects_unknown_source_kind(client: TestClient) -> None:
    r = client.post(
        "/v1/nodes",
        json={
            "type": "memory_user",
            "name": "x",
            "body": "y",
            "source_kind": "bogus_kind",
        },
    )
    assert r.status_code in (400, 422), r.text
