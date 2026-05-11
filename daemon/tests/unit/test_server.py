"""HTTP API tests using FastAPI's TestClient."""

from __future__ import annotations

import textwrap
from collections.abc import Iterator
from pathlib import Path

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


# --- /health --------------------------------------------------------------


def test_health_empty(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["node_count"] == 0
    assert data["source_count"] == 0
    assert data["version"]
    assert data["embedding_loaded"] is False


def test_health_with_nodes(client: TestClient, store: Store) -> None:
    n = Node.new(
        type="memory_feedback",
        name="x",
        body="b",
        source_path="/x.md",
        source_kind="memory_dir",
    )
    store.upsert_node(n)
    resp = client.get("/health")
    data = resp.json()
    assert data["node_count"] == 1
    assert data["counts_by_type"]["memory_feedback"] == 1


# --- /sources -------------------------------------------------------------


def test_add_source_then_list(client: TestClient) -> None:
    resp = client.post("/sources", json={"path": "/p", "kind": "memory_dir", "project_key": "P1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == "/p"
    assert data["project_key"] == "P1"

    resp = client.get("/sources")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_add_source_invalid_kind(client: TestClient) -> None:
    resp = client.post("/sources", json={"path": "/p", "kind": "bogus"})
    assert resp.status_code == 400


def test_remove_source(client: TestClient) -> None:
    client.post("/sources", json={"path": "/p", "kind": "memory_dir"})
    resp = client.delete("/sources", params={"path": "/p"})
    assert resp.status_code == 200
    # v1.1.1: DELETE now returns the cascade count alongside `ok`.
    assert resp.json() == {"ok": True, "removed": 0}
    assert client.get("/sources").json() == []


# --- /reindex + /nodes ----------------------------------------------------


def _seed(tmp_path: Path) -> Path:
    src = tmp_path / "mem"
    src.mkdir()
    (src / "feedback_a.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: a
            description: short rule
            type: feedback
            ---
            Body of a.
            """
        ),
        encoding="utf-8",
    )
    return src


def test_reindex_then_list_nodes(client: TestClient, tmp_path: Path) -> None:
    src = _seed(tmp_path)
    client.post("/sources", json={"path": str(src), "kind": "memory_dir"})
    resp = client.post("/reindex", params={"embed": "false"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == 1

    resp = client.get("/nodes")
    nodes = resp.json()
    assert len(nodes) == 1
    assert nodes[0]["type"] == "memory_feedback"
    assert nodes[0]["name"] == "a"


def test_get_node_not_found(client: TestClient) -> None:
    resp = client.get("/nodes/does-not-exist")
    assert resp.status_code == 404


def test_update_node(client: TestClient, tmp_path: Path) -> None:
    src = _seed(tmp_path)
    client.post("/sources", json={"path": str(src), "kind": "memory_dir"})
    client.post("/reindex", params={"embed": "false"})
    nid = client.get("/nodes").json()[0]["id"]
    resp = client.put(f"/nodes/{nid}", json={"description": "patched"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "patched"


def test_delete_node(client: TestClient, tmp_path: Path) -> None:
    src = _seed(tmp_path)
    client.post("/sources", json={"path": str(src), "kind": "memory_dir"})
    client.post("/reindex", params={"embed": "false"})
    nid = client.get("/nodes").json()[0]["id"]
    resp = client.delete(f"/nodes/{nid}")
    assert resp.status_code == 200
    assert client.get("/nodes").json() == []


# --- /query --------------------------------------------------------------


def test_query_endpoint(client: TestClient, tmp_path: Path) -> None:
    src = _seed(tmp_path)
    client.post("/sources", json={"path": str(src), "kind": "memory_dir"})
    client.post("/reindex")  # with embed
    resp = client.post("/query", json={"prompt": "the rule", "k": 3, "budget_tokens": 200})
    assert resp.status_code == 200
    data = resp.json()
    assert "hits" in data
    assert "intent_tags" in data
    assert data["tokens_used"] <= 200
    assert "query_id" in data


def test_query_validation(client: TestClient) -> None:
    resp = client.post("/query", json={"prompt": "x", "budget_tokens": 0})
    assert resp.status_code == 422


# --- /audit --------------------------------------------------------------


def test_audit_returns_recent_queries(client: TestClient, tmp_path: Path) -> None:
    src = _seed(tmp_path)
    client.post("/sources", json={"path": str(src), "kind": "memory_dir"})
    client.post("/reindex")
    client.post("/query", json={"prompt": "the rule"})
    resp = client.get("/audit")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["prompt"] == "the rule"
