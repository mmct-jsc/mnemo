"""v2.6.3: server-side Nebula layout cache.

The Cosmograph GPU force simulation is expensive to converge on a
10 k+ node graph. v2.6.3 persists the settled point positions keyed
by (scope_key, fingerprint). The client GETs the cache before
rendering: a hit applies the positions and skips the simulation
entirely (instant settled nebula); a miss / stale fingerprint runs
the sim and PUTs the result back.

The fingerprint is a hash of the in-scope node id set + edge count,
so a reindex / node-write that changes the graph naturally changes
the fingerprint -> the cached layout is invalidated automatically
("recompute only on reindex / impact actions").

These tests lock the server contract:
  - /ui/graph-data exposes ``scope_key`` + ``fingerprint``;
  - the fingerprint is stable for an unchanged graph and changes
    when the node set changes;
  - GET /ui/graph-layout is miss-then-hit-then-stale;
  - PUT upserts (one row per scope, new fingerprint overwrites).
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


def _seed(store: Store, *, key: str, n: int) -> None:
    for i in range(n):
        store.upsert_node(
            Node.new(
                type="memory_feedback",
                name=f"n-{key}-{i}",
                body="b",
                source_path=f"/{key}/n-{i}.md",
                source_kind="memory_dir",
                project_key=key,
            )
        )


# --- graph-data exposes the cache-coordination keys --------------------


def test_graph_data_exposes_scope_key_and_fingerprint(client: TestClient, store: Store) -> None:
    _seed(store, key="P1", n=4)
    data = client.get("/ui/graph-data?project_keys=P1").json()
    assert "scope_key" in data
    assert "fingerprint" in data
    assert data["scope_key"] == "keys:P1"
    assert isinstance(data["fingerprint"], str) and len(data["fingerprint"]) >= 16


def test_scope_key_partitions_by_view(client: TestClient, store: Store) -> None:
    _seed(store, key="P1", n=2)
    assert client.get("/ui/graph-data?project=P1").json()["scope_key"] == "project:P1"
    assert client.get("/ui/graph-data?base_only=1").json()["scope_key"] == "base_only"
    assert client.get("/ui/graph-data").json()["scope_key"] == "global"
    # CSV order doesn't matter -- the key is sorted so the cache row
    # is shared regardless of how the workspace lists its projects.
    a = client.get("/ui/graph-data?project_keys=P1,P2").json()["scope_key"]
    b = client.get("/ui/graph-data?project_keys=P2,P1").json()["scope_key"]
    assert a == b == "keys:P1,P2"


def test_fingerprint_stable_then_changes_on_graph_change(client: TestClient, store: Store) -> None:
    _seed(store, key="P1", n=3)
    fp1 = client.get("/ui/graph-data?project_keys=P1").json()["fingerprint"]
    # Same graph -> identical fingerprint (cache stays valid).
    fp1b = client.get("/ui/graph-data?project_keys=P1").json()["fingerprint"]
    assert fp1 == fp1b
    # A new node in scope == an "impact action" -> fingerprint flips
    # -> the client's cached layout is invalidated + recomputed.
    _seed(store, key="P1", n=1)
    fp2 = client.get("/ui/graph-data?project_keys=P1").json()["fingerprint"]
    assert fp2 != fp1


# --- the layout cache endpoints ----------------------------------------


def test_graph_layout_miss_then_put_then_hit(client: TestClient) -> None:
    miss = client.get(
        "/ui/graph-layout", params={"scope_key": "keys:P1", "fingerprint": "fpA"}
    ).json()
    assert miss == {"hit": False, "reason": "no_layout"}

    put = client.put(
        "/ui/graph-layout",
        json={"scope_key": "keys:P1", "fingerprint": "fpA", "positions": [1, 2, 3, 4]},
    )
    assert put.status_code == 200
    assert put.json() == {"ok": True}

    hit = client.get(
        "/ui/graph-layout", params={"scope_key": "keys:P1", "fingerprint": "fpA"}
    ).json()
    assert hit == {"hit": True, "positions": [1, 2, 3, 4]}


def test_graph_layout_stale_fingerprint_is_a_miss(client: TestClient) -> None:
    client.put(
        "/ui/graph-layout",
        json={"scope_key": "s", "fingerprint": "old", "positions": [0, 0]},
    )
    stale = client.get("/ui/graph-layout", params={"scope_key": "s", "fingerprint": "new"}).json()
    assert stale == {"hit": False, "reason": "stale"}


def test_graph_layout_put_overwrites_in_place(client: TestClient) -> None:
    """One row per scope: a fresh fingerprint replaces the prior
    layout so the cache always reflects the latest converged graph."""
    client.put(
        "/ui/graph-layout",
        json={"scope_key": "s", "fingerprint": "fp1", "positions": [1, 1]},
    )
    client.put(
        "/ui/graph-layout",
        json={"scope_key": "s", "fingerprint": "fp2", "positions": [2, 2, 2, 2]},
    )
    # fp1 is gone (overwritten), fp2 is the live layout.
    assert (
        client.get("/ui/graph-layout", params={"scope_key": "s", "fingerprint": "fp1"}).json()[
            "hit"
        ]
        is False
    )
    assert client.get(
        "/ui/graph-layout", params={"scope_key": "s", "fingerprint": "fp2"}
    ).json() == {"hit": True, "positions": [2, 2, 2, 2]}


def test_graph_layout_put_rejects_bad_body(client: TestClient) -> None:
    bad = client.put("/ui/graph-layout", json={"scope_key": "s"})
    assert bad.status_code == 400


def test_graph_layout_round_trips_via_store(client: TestClient, store: Store) -> None:
    """The PUT actually persists through the Store, not just an
    in-memory dict -- a fresh request reads it back."""
    client.put(
        "/ui/graph-layout",
        json={"scope_key": "persisted", "fingerprint": "fp", "positions": [9, 8, 7, 6]},
    )
    cached = store.get_graph_layout("persisted")
    assert cached is not None
    fp, positions_json = cached
    assert fp == "fp"
    assert "9" in positions_json and "6" in positions_json
