"""Integration tests for /v1 versioned API + legacy-path removal.

The 308 bridge from v1.1 is GONE in v1.2 (phase 7 housekeeping):
- ``/health``, ``/sources``, ``/nodes``, ``/audit``, ``/config`` no
  longer redirect; they return 404 because they're not registered.
- Every response still carries ``X-Mnemo-Api-Version: 1`` (that
  middleware stayed -- adapters use it to sanity-check the daemon
  they're talking to).
- ``/v1/openapi.json`` and the default ``/openapi.json`` both still
  expose only v1 paths.
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
    """Embedder stub: skip the heavy MiniLM load. Returns deterministic
    384-dim vectors so the store's vec_search dim assertion passes."""

    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        # Don't call parent __init__ -- it would try to set up the cache dir.
        self.model_name = "fake"
        self._cache_dir = Path("/tmp/mnemo-fake-cache")
        self._model = object()  # truthy so embedding_loaded is True

    @property
    def dim(self) -> int:
        return 384

    def embed_text(self, text: str) -> list[float]:
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
    # follow_redirects=False so a stray 308 from an unintended re-add
    # would show up cleanly in the test output.
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


# --- Version header (kept in v1.2) ----------------------------------------


def test_v1_response_has_api_version_header(client: TestClient) -> None:
    r = client.get("/v1/health")
    assert r.headers.get("X-Mnemo-Api-Version") == "1"


def test_404_response_still_has_api_version_header(client: TestClient) -> None:
    """Even the 404 from a now-removed legacy path should carry the
    version header -- it's stamped by middleware that wraps everything,
    so adapters introspecting failed requests still see the daemon
    version."""
    r = client.get("/health")
    assert r.status_code == 404
    assert r.headers.get("X-Mnemo-Api-Version") == "1"


# --- Legacy paths now 404 (the v1.2 cliff) --------------------------------


@pytest.mark.parametrize(
    "legacy",
    [
        "/health",
        "/sources",
        "/nodes",
        "/audit",
        "/config",
        "/reindex",
        "/query",
    ],
)
def test_legacy_paths_return_404(client: TestClient, legacy: str) -> None:
    """v1.2 phase 7: the 308 bridge from v1.1 is gone. Anything that
    tries the un-versioned path now gets a clean 404 -- not a redirect
    chain -- so misbehaving adapters fail loudly instead of silently
    routing through middleware that no longer exists."""
    r = client.get(legacy)
    assert r.status_code == 404


def test_legacy_post_no_longer_redirects(client: TestClient) -> None:
    """POST /reindex used to 308 to /v1/reindex; now it's a flat 404."""
    r = client.post("/reindex", json={})
    assert r.status_code == 404


def test_legacy_path_with_tail_returns_404(client: TestClient) -> None:
    """``/nodes/abc123`` used to redirect; now 404."""
    r = client.get("/nodes/abc123")
    assert r.status_code == 404


# --- UI HTML routes are untouched ------------------------------------------


def test_ui_html_routes_unchanged(client: TestClient) -> None:
    """The browser-facing pages still live at their own paths -- they
    never went through the redirect middleware in the first place,
    and removing the middleware can't affect them."""
    for ui_path in ("/", "/nodes-page", "/audit-page", "/sources-page", "/settings"):
        r = client.get(ui_path, headers={"Accept": "text/html"})
        # 200 (page renders) is the happy path. We assert the request
        # didn't accidentally redirect (which would mean the legacy
        # middleware came back).
        assert r.status_code != 308
        assert r.status_code != 301
