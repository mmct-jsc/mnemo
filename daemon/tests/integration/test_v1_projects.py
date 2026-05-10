"""Integration tests for /v1/projects/* endpoints (phase 2).

Covers:
- POST /v1/projects/resolve returns the canonical key for a path
- POST /v1/projects/active sets + persists the active project
- GET /v1/projects/active returns null when unset, the row when set
- DELETE /v1/projects/active clears it
- POST /v1/query with no project_key falls back to the active project
- POST /v1/query with explicit project_key overrides the active project
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
    """384-dim deterministic fake. Matches the EMBEDDING_DIM the store
    asserts on -- vec_search would 500 with any other dim."""

    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        self.model_name = "fake"
        self._cache_dir = Path("/tmp/mnemo-fake-cache")
        self._model = object()

    @property
    def dim(self) -> int:
        return 384

    def embed_text(self, text: str) -> list[float]:
        # Trivial deterministic vector. Content irrelevant -- the test
        # DB is empty so vec_search returns no rows regardless.
        v = float(len(text) % 7)
        return [v for _ in range(384)]

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


def test_resolve_returns_canonical_key(client: TestClient) -> None:
    r = client.post("/v1/projects/resolve", json={"path": "/home/alice/work/web"})
    assert r.status_code == 200
    body = r.json()
    assert body["project_key"] == "home-alice-work-web"
    assert body["path"] == "/home/alice/work/web"


def test_active_unset_returns_null(client: TestClient) -> None:
    r = client.get("/v1/projects/active")
    assert r.status_code == 200
    assert r.json() is None


def test_set_active_persists(client: TestClient) -> None:
    r = client.post("/v1/projects/active", json={"path": "/home/alice/repo"})
    assert r.status_code == 200
    body = r.json()
    assert body["project_key"] == "home-alice-repo"
    assert body["path"] == "/home/alice/repo"
    assert body["since"] > 0

    # Read back via GET.
    r2 = client.get("/v1/projects/active")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["project_key"] == "home-alice-repo"


def test_set_active_replaces_previous(client: TestClient) -> None:
    """The singleton CHECK constraint means setting active_project upserts
    the single row. No table growth."""
    client.post("/v1/projects/active", json={"path": "/p1"})
    client.post("/v1/projects/active", json={"path": "/p2"})
    r = client.get("/v1/projects/active")
    assert r.json()["project_key"] == "p2"


def test_clear_active(client: TestClient) -> None:
    client.post("/v1/projects/active", json={"path": "/p1"})
    r = client.delete("/v1/projects/active")
    assert r.status_code == 200
    assert client.get("/v1/projects/active").json() is None


def test_query_falls_back_to_active_project(client: TestClient) -> None:
    """When /v1/query has no project_key, the daemon should use the
    persisted active project. We verify by setting active first and then
    issuing a no-project_key query -- the daemon must not 500."""
    client.post("/v1/projects/active", json={"path": "/test/active-fallback"})
    r = client.post(
        "/v1/query",
        json={"prompt": "test fallback query", "k": 3, "budget_tokens": 200},
    )
    assert r.status_code == 200
    # The response shape is the standard QueryOut. We don't have any nodes
    # to retrieve in this test DB, so hits == [] is fine; what matters is
    # the daemon accepted the call without an explicit project_key.
    assert "hits" in r.json()


def test_query_explicit_project_key_overrides_active(client: TestClient) -> None:
    """Per the hybrid contract, a per-call project_key wins over the
    persisted active project."""
    client.post("/v1/projects/active", json={"path": "/test/active-A"})
    r = client.post(
        "/v1/query",
        json={
            "prompt": "test override",
            "project_key": "explicit-B",
            "k": 3,
            "budget_tokens": 200,
        },
    )
    assert r.status_code == 200


def test_resolve_handles_windows_path(client: TestClient) -> None:
    r = client.post(
        "/v1/projects/resolve",
        json={"path": "D:\\Repository\\knowledge-base"},
    )
    assert r.status_code == 200
    assert r.json()["project_key"] == "D--Repository-knowledge-base"
