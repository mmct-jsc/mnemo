"""HTTP tests for v2.6 phase 5: /v1/workspaces/* + /v1/events SSE.

Covers CRUD endpoints, activation broadcasting on /v1/events, and
cap enforcement (409 WorkspaceTooLarge when total nodes exceeds the
hard cap).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


def _seed_nodes(store: Store, project_key: str, n: int) -> None:
    for i in range(n):
        store.upsert_node(
            Node.new(
                type="memory_project",
                name=f"n{i}",
                body="body",
                source_path=f"/mem/{project_key}/{i}.md",
                source_kind="memory_dir",
                project_key=project_key,
            )
        )


# --- CRUD --------------------------------------------------------------------


def test_list_workspaces_empty(client: TestClient) -> None:
    resp = client.get("/v1/workspaces")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_workspace(client: TestClient) -> None:
    resp = client.post(
        "/v1/workspaces",
        json={"name": "ai-edge", "project_keys": ["P1", "P2"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "ai-edge"
    assert body["project_keys"] == ["P1", "P2"]
    assert "id" in body


def test_create_workspace_rejects_duplicate_name(client: TestClient) -> None:
    client.post("/v1/workspaces", json={"name": "dup", "project_keys": []})
    resp = client.post("/v1/workspaces", json={"name": "dup", "project_keys": ["P1"]})
    assert resp.status_code == 400


def test_get_workspace_by_id(client: TestClient) -> None:
    created = client.post("/v1/workspaces", json={"name": "x", "project_keys": ["P1"]}).json()
    resp = client.get(f"/v1/workspaces/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_workspace_returns_404_when_missing(client: TestClient) -> None:
    resp = client.get("/v1/workspaces/ghost")
    assert resp.status_code == 404


def test_patch_workspace(client: TestClient) -> None:
    created = client.post("/v1/workspaces", json={"name": "orig", "project_keys": ["P1"]}).json()
    resp = client.patch(
        f"/v1/workspaces/{created['id']}",
        json={"name": "renamed", "project_keys": ["P1", "P2"]},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed"
    assert resp.json()["project_keys"] == ["P1", "P2"]


def test_delete_workspace(client: TestClient) -> None:
    created = client.post("/v1/workspaces", json={"name": "del", "project_keys": []}).json()
    resp = client.delete(f"/v1/workspaces/{created['id']}")
    assert resp.status_code == 200
    resp = client.get(f"/v1/workspaces/{created['id']}")
    assert resp.status_code == 404


def test_delete_workspace_returns_404_when_missing(client: TestClient) -> None:
    resp = client.delete("/v1/workspaces/ghost")
    assert resp.status_code == 404


# --- Activation -------------------------------------------------------------


def test_get_active_returns_null_initially(client: TestClient) -> None:
    resp = client.get("/v1/workspaces/active")
    assert resp.status_code == 200
    assert resp.json() == {"active": None}


def test_activate_workspace_updates_pointer(client: TestClient) -> None:
    created = client.post("/v1/workspaces", json={"name": "x", "project_keys": ["P1"]}).json()
    resp = client.post(f"/v1/workspaces/{created['id']}/activate")
    assert resp.status_code == 200
    active = client.get("/v1/workspaces/active").json()
    assert active["active"]["id"] == created["id"]


def test_activate_missing_workspace_returns_404(client: TestClient) -> None:
    resp = client.post("/v1/workspaces/ghost/activate")
    assert resp.status_code == 404


def test_clear_active_workspace_returns_to_no_workspace(client: TestClient) -> None:
    created = client.post("/v1/workspaces", json={"name": "x", "project_keys": []}).json()
    client.post(f"/v1/workspaces/{created['id']}/activate")
    resp = client.post("/v1/workspaces/clear")
    assert resp.status_code == 200
    assert client.get("/v1/workspaces/active").json()["active"] is None


# --- Cap enforcement --------------------------------------------------------


def test_activate_returns_node_count_in_payload(client: TestClient, store: Store) -> None:
    _seed_nodes(store, "P1", 5)
    created = client.post("/v1/workspaces", json={"name": "x", "project_keys": ["P1"]}).json()
    resp = client.post(f"/v1/workspaces/{created['id']}/activate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workspace"]["id"] == created["id"]
    assert "total_nodes" in body
    assert body["total_nodes"] >= 5


def test_activate_409_when_over_hard_cap(client: TestClient, store: Store) -> None:
    """Force a tiny hard cap so the test can verify the refusal."""
    _seed_nodes(store, "P1", 20)
    created = client.post("/v1/workspaces", json={"name": "big", "project_keys": ["P1"]}).json()
    resp = client.post(
        f"/v1/workspaces/{created['id']}/activate",
        params={"hard_cap_nodes": 5},
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "workspace_too_large"
    assert detail["total_nodes"] >= 20
    assert detail["hard_cap"] == 5
    # Active pointer must NOT change on 409.
    assert client.get("/v1/workspaces/active").json()["active"] is None


def test_activate_warns_at_soft_cap(client: TestClient, store: Store) -> None:
    _seed_nodes(store, "P1", 20)
    created = client.post("/v1/workspaces", json={"name": "soft", "project_keys": ["P1"]}).json()
    resp = client.post(
        f"/v1/workspaces/{created['id']}/activate",
        params={"soft_cap_nodes": 5, "hard_cap_nodes": 1_000_000},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("soft_cap_exceeded") is True


# --- SSE broadcast on /v1/events --------------------------------------------
#
# The /v1/events endpoint is a long-poll SSE stream; testing the wire format
# via TestClient.stream() deadlocks because TestClient executes requests
# serially against an in-process ASGI app. Instead, we attach a queue.Queue
# subscriber DIRECTLY to ``app.state.mnemo_state.event_subscribers`` (which
# is exactly what the route handler does internally) and assert that
# broadcasting fires when the workspace endpoints execute. This covers the
# observable contract: clients that subscribe receive the frames.


def _attach_subscriber(client: TestClient):
    import queue

    q: queue.Queue = queue.Queue(maxsize=64)
    state = client.app.state.mnemo_state
    with state.event_subscribers_lock:
        state.event_subscribers.append(q)
    return q


def test_activate_broadcasts_workspace_activated(client: TestClient) -> None:
    q = _attach_subscriber(client)
    created = client.post("/v1/workspaces", json={"name": "x", "project_keys": []}).json()
    client.post(f"/v1/workspaces/{created['id']}/activate")
    # Drain the queue and look for the activation frame.
    found = False
    while not q.empty():
        name, payload = q.get_nowait()
        if name == "workspace_activated":
            assert payload["id"] == created["id"]
            assert payload["name"] == "x"
            found = True
    assert found, "workspace_activated must be broadcast"


def test_delete_broadcasts_workspace_deleted(client: TestClient) -> None:
    q = _attach_subscriber(client)
    created = client.post("/v1/workspaces", json={"name": "bye", "project_keys": []}).json()
    client.delete(f"/v1/workspaces/{created['id']}")
    found = False
    while not q.empty():
        name, payload = q.get_nowait()
        if name == "workspace_deleted" and payload["id"] == created["id"]:
            found = True
    assert found, "workspace_deleted must be broadcast"


def test_clear_broadcasts_workspace_cleared(client: TestClient) -> None:
    q = _attach_subscriber(client)
    client.post("/v1/workspaces/clear")
    found = False
    while not q.empty():
        name, _payload = q.get_nowait()
        if name == "workspace_cleared":
            found = True
    assert found


# --- Source proposal endpoint (paired with phase 4) -------------------------


def test_sources_propose_endpoint(client: TestClient, tmp_path) -> None:  # noqa: ANN001
    docs = tmp_path / "docs"
    docs.mkdir()
    for i in range(5):
        (docs / f"d{i}.md").write_text(f"# d{i}")
    src = tmp_path / "src"
    src.mkdir()
    for i in range(12):
        (src / f"m{i}.py").write_text(f"def f{i}(): ...")
    resp = client.post("/v1/sources/propose", json={"path": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()
    kinds = {p["kind"] for p in body["proposals"]}
    assert kinds == {"docs_dir", "code_repo"}
    assert "gitignore_excludes" in body
    assert "gitignore_files_found" in body
    assert "warnings" in body


def test_sources_propose_missing_path_returns_404(client: TestClient, tmp_path) -> None:  # noqa: ANN001
    resp = client.post("/v1/sources/propose", json={"path": str(tmp_path / "ghost")})
    assert resp.status_code == 404
