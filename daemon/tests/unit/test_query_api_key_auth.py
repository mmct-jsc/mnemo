"""Task 2.3: optional api-key auth on /v1/query.

The contract this test layer locks in (anti-goal #1: "free local-first
plugin stays fully capable"):

1. The default (``Config.hosted_auth_enabled = False``) MUST leave
   ``/v1/query`` exactly as today -- no Authorization header
   required, no behavior change for self-host installs.
2. Even with the flag ON, loopback (127.0.0.1 / ::1 / localhost) is
   exempt so the local UI / CLI / plugin keeps working on a hosted
   deployment running on the same machine.
3. Flag ON + non-loopback + no/invalid/revoked key -> 401 with a
   ``WWW-Authenticate: Bearer`` header so standard HTTP clients can
   handle it.
4. Flag ON + non-loopback + valid key -> 200; the dependency
   returns the api_key.id for downstream metering (Task 2.4).

Tests use the TestClient (default client host == ``"testclient"``,
which is NOT loopback), and monkey-patch either ``config.load`` to
flip the flag or ``_is_loopback`` to simulate the loopback path.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mnemo import config, server
from mnemo.config import Config
from mnemo.server import LOOPBACK_HOSTS, _is_loopback, create_app
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder


@pytest.fixture
def daemon_client(store: Store) -> TestClient:
    """TestClient with both store and embedder injected so /v1/query
    works without triggering the real Embedder model load."""
    return TestClient(create_app(store=store, embedder=FakeEmbedder()))


def _seed_one_node(store: Store) -> str:
    """Seed a single node so /v1/query has something to retrieve.
    Returns the node id."""
    n = Node(
        id="n1",
        type="memory_feedback",
        name="seed",
        description=None,
        body="seed body for auth tests",
        source_path="/seed/n1.md",
        source_kind="memory_dir",
        project_key=None,
        frontmatter_json=None,
        hash="h",
        created_at=1,
        updated_at=1,
    )
    store.upsert_node(n)
    return n.id


def _enable_hosted_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch config.load to return a Config with hosted_auth_enabled=True."""
    cfg = Config()
    cfg.hosted_auth_enabled = True
    monkeypatch.setattr(config, "load", lambda: cfg)


def _force_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch _is_loopback so the TestClient request (host='testclient')
    is treated as loopback."""
    monkeypatch.setattr(server, "_is_loopback", lambda host: True)


# --- Unit: the _is_loopback helper --------------------------------------


def test_is_loopback_recognises_ipv4_and_ipv6_and_localhost() -> None:
    for host in ("127.0.0.1", "::1", "localhost"):
        assert _is_loopback(host), f"{host!r} should count as loopback"


def test_is_loopback_rejects_anything_else() -> None:
    for host in ("testclient", "0.0.0.0", "10.0.0.1", "example.com", None):
        assert not _is_loopback(host), f"{host!r} must NOT count as loopback"


def test_loopback_hosts_constant_is_frozen() -> None:
    """LOOPBACK_HOSTS is frozen so accidental mutation can't widen the
    exemption at runtime."""
    assert isinstance(LOOPBACK_HOSTS, frozenset)
    assert "127.0.0.1" in LOOPBACK_HOSTS


# --- Default (flag off): unchanged behavior -----------------------------


def test_query_unchanged_when_flag_off(daemon_client: TestClient, store: Store) -> None:
    """The anti-goal-critical test: flag off -> no auth required, no
    behavior change. Self-host installs must continue working
    exactly as they did before Task 2.3."""
    _seed_one_node(store)
    r = daemon_client.post("/v1/query", json={"prompt": "seed", "k": 1, "budget_tokens": 200})
    assert r.status_code == 200, r.text


def test_query_unchanged_when_flag_off_even_with_random_auth_header(
    daemon_client: TestClient,
    store: Store,
) -> None:
    """Flag off -> Authorization header is ignored (not even parsed).
    A nonsense header must not error."""
    _seed_one_node(store)
    r = daemon_client.post(
        "/v1/query",
        json={"prompt": "seed", "k": 1, "budget_tokens": 200},
        headers={"Authorization": "Bearer nonsense"},
    )
    assert r.status_code == 200, r.text


# --- Flag ON: loopback exempt -------------------------------------------


def test_query_loopback_exempt_when_flag_on(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with hosted_auth_enabled=True, loopback clients (same
    machine) skip the auth check. Preserves the local UI / CLI /
    plugin path on hosted-mode deployments."""
    _enable_hosted_auth(monkeypatch)
    _force_loopback(monkeypatch)
    _seed_one_node(store)
    r = daemon_client.post("/v1/query", json={"prompt": "seed", "k": 1, "budget_tokens": 200})
    assert r.status_code == 200, r.text


# --- Flag ON: non-loopback requires a valid key -------------------------


def test_query_non_loopback_rejected_without_auth_header(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_hosted_auth(monkeypatch)
    _seed_one_node(store)
    r = daemon_client.post("/v1/query", json={"prompt": "seed", "k": 1, "budget_tokens": 200})
    assert r.status_code == 401, r.text
    assert "WWW-Authenticate" in r.headers
    assert "Bearer" in r.headers["WWW-Authenticate"]


def test_query_non_loopback_rejected_with_non_bearer_auth(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_hosted_auth(monkeypatch)
    _seed_one_node(store)
    r = daemon_client.post(
        "/v1/query",
        json={"prompt": "seed", "k": 1, "budget_tokens": 200},
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert r.status_code == 401, r.text


def test_query_non_loopback_rejected_with_invalid_bearer_key(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_hosted_auth(monkeypatch)
    _seed_one_node(store)
    r = daemon_client.post(
        "/v1/query",
        json={"prompt": "seed", "k": 1, "budget_tokens": 200},
        headers={"Authorization": "Bearer not-a-real-key"},
    )
    assert r.status_code == 401, r.text
    assert "invalid_token" in r.headers.get("WWW-Authenticate", "")


def test_query_non_loopback_accepted_with_valid_key(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_hosted_auth(monkeypatch)
    _seed_one_node(store)
    raw_key, _key_id = store.create_api_key("test-partner")
    r = daemon_client.post(
        "/v1/query",
        json={"prompt": "seed", "k": 1, "budget_tokens": 200},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 200, r.text


def test_query_revoked_key_rejected(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key that was valid yesterday and got revoked today must be
    rejected today. Tests the verify_api_key revoked-path branch."""
    _enable_hosted_auth(monkeypatch)
    _seed_one_node(store)
    raw_key, key_id = store.create_api_key("about-to-revoke")
    store.revoke_api_key(key_id)
    r = daemon_client.post(
        "/v1/query",
        json={"prompt": "seed", "k": 1, "budget_tokens": 200},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 401, r.text
