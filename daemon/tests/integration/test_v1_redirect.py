"""Integration tests for /v1 versioned API + 308 redirects from legacy paths.

Phase 1 of the v1.1 plan. We verify:
- Public endpoints respond at /v1/...
- Legacy paths return 308 to their /v1/... equivalent
- 308 preserves the HTTP method (POST, DELETE)
- Every response carries X-Mnemo-Api-Version: 1
- /v1/openapi.json contains only /v1 paths
- The default /openapi.json is also v1-only (used by /docs)
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
    """Embedder stub: skip the heavy MiniLM load. Returns deterministic vectors."""

    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        # Don't call parent __init__ -- it would try to set up the cache dir.
        self.model_name = "fake"
        self._cache_dir = Path("/tmp/mnemo-fake-cache")
        self._model = object()  # truthy so embedding_loaded is True

    @property
    def dim(self) -> int:
        return 8

    def embed_text(self, text: str) -> list[float]:
        return [float(len(text) % 7) for _ in range(8)]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    db = tmp_path / "test.db"
    store = Store(db)
    embedder = _FakeEmbedder()
    app = create_app(store=store, embedder=embedder)
    # follow_redirects=False so we can assert the 308 itself.
    with TestClient(app, follow_redirects=False) as c:
        yield c


# --- /v1 routing ----------------------------------------------------------


def test_v1_health_responds(client: TestClient) -> None:
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["version"]


def test_v1_openapi_json_has_only_v1_paths(client: TestClient) -> None:
    r = client.get("/v1/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    paths = list(schema["paths"].keys())
    assert paths, "expected at least one path in the spec"
    for p in paths:
        assert p.startswith("/v1/"), f"non-v1 path leaked into spec: {p}"


def test_default_openapi_also_v1_only(client: TestClient) -> None:
    """The default /openapi.json drives the built-in /docs UI; both should
    reflect only the public v1 contract."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    for p in schema["paths"]:
        assert p.startswith("/v1/")


# --- Version header -------------------------------------------------------


def test_v1_response_has_api_version_header(client: TestClient) -> None:
    r = client.get("/v1/health")
    assert r.headers.get("X-Mnemo-Api-Version") == "1"


def test_redirect_response_has_api_version_header(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 308
    assert r.headers.get("X-Mnemo-Api-Version") == "1"


# --- Legacy redirects -----------------------------------------------------


@pytest.mark.parametrize(
    "legacy",
    [
        "/health",
        "/sources",
        "/nodes",
        "/audit",
        "/config",
    ],
)
def test_legacy_get_redirects_308_to_v1(client: TestClient, legacy: str) -> None:
    r = client.get(legacy)
    assert r.status_code == 308
    assert r.headers["location"] == f"/v1{legacy}"


def test_legacy_get_with_query_string_preserves_query(client: TestClient) -> None:
    r = client.get("/nodes?type=memory_user&limit=5")
    assert r.status_code == 308
    assert r.headers["location"] == "/v1/nodes?type=memory_user&limit=5"


def test_legacy_get_with_path_tail_redirects(client: TestClient) -> None:
    r = client.get("/nodes/abc123")
    assert r.status_code == 308
    assert r.headers["location"] == "/v1/nodes/abc123"


def test_legacy_post_redirects_308_preserves_method(client: TestClient) -> None:
    """308 must preserve the HTTP method. The TestClient with follow_redirects
    will replay the POST against /v1/reindex if we let it."""
    r = client.post("/reindex", json={})
    assert r.status_code == 308
    assert r.headers["location"] == "/v1/reindex"

    # Now verify the redirect actually lands on a working POST endpoint.
    with TestClient(client.app, follow_redirects=True) as following:
        r2 = following.post("/reindex", json={})
        assert r2.status_code == 200


def test_legacy_delete_redirects(client: TestClient) -> None:
    r = client.delete("/sources?path=/nonexistent")
    assert r.status_code == 308
    assert r.headers["location"].startswith("/v1/sources?")


# --- UI HTML routes are NOT redirected ------------------------------------


def test_ui_html_routes_unchanged(client: TestClient) -> None:
    """The browser-facing pages should NOT be redirected -- they live at
    their own paths. Sanity check a few."""
    for ui_path in ("/", "/nodes-page", "/audit-page", "/sources-page", "/settings"):
        r = client.get(ui_path, headers={"Accept": "text/html"})
        # 200 (page renders) or 404 (route exists but template missing in
        # this test app) is fine -- the assertion is "NOT 308".
        assert r.status_code != 308, (
            f"UI path {ui_path} was redirected -- redirect middleware is too aggressive"
        )
