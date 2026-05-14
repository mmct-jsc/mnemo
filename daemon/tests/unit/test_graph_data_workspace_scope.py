"""v2.6.0 polish: /ui/graph-data respects the active workspace.

The Nebula page used to ignore the active workspace + show every
indexed node (or whatever the URL ?project= deep-link said). With
workspaces driving retrieval scope, the graph view must mirror the
same scope -- otherwise users switch workspaces and Nebula still
paints nodes from the project they just left.

Three new behaviors:

1. ``?project_keys=key1,key2`` filters to a comma-separated set
   (workspace mode -- multi-project). BASE-flagged + project=NULL
   nodes are included.
2. ``?base_only=1`` returns BASE-flagged nodes only (no-workspace UI
   mode).
3. Pre-existing ``?project=<key>`` single-key keeps working as a
   deep-link surface (/code -> Nebula).

The Alpine factory auto-reads the active workspace at init() and
re-fetches whenever /v1/events fires a workspace event.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates"


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


def _seed(store: Store, *, key: str, n: int, base: bool = False, prefix: str = "n") -> None:
    for i in range(n):
        store.upsert_node(
            Node.new(
                type="memory_feedback",
                name=f"{prefix}-{key}-{i}",
                body="b",
                source_path=f"/{key}/{prefix}-{i}.md",
                source_kind="memory_dir",
                project_key=key if key else None,
                base=base,
            )
        )


# --- Server contract --------------------------------------------------------


def test_project_keys_returns_balanced_set_across_projects(
    client: TestClient, store: Store
) -> None:
    """v2.6.0 polish: a 2-project workspace gets nodes from BOTH
    projects -- previously only the most-recent project surfaced
    because list_nodes(limit=2000) capped BEFORE the project
    filter ran (the aibox+edge workspace looked like a
    disconnected grid for one repo + nothing for the other).

    Per-key query (uses the project_key index) returns the full
    union with no truncation.
    """
    _seed(store, key="P1", n=800, prefix="p1n")
    _seed(store, key="P2", n=800, prefix="p2n")
    resp = client.get("/ui/graph-data?project_keys=P1,P2")
    data = resp.json()
    nodes = [e for e in data["elements"] if "id" in e["data"]]
    assert len(nodes) == 1600
    by_proj: dict[str, int] = {}
    for n in nodes:
        by_proj[n["data"]["project"]] = by_proj.get(n["data"]["project"], 0) + 1
    assert by_proj.get("P1") == 800
    assert by_proj.get("P2") == 800
    assert data["truncated"] is False
    assert data["total_in_scope"] == 1600
    assert data["shown_node_count"] == 1600


def test_project_keys_returns_all_in_scope_no_truncation(client: TestClient, store: Store) -> None:
    """v2.6.0 polish: 'always display all'. No GRAPH_NODE_CAP. The
    user wants the full graph so v3 chat can reference any node in
    the flow tree. The canvas + tree both reflect 100% of the scope.

    Seed 4000 code_modules -> response returns ALL of them. The
    tree_modules side-channel mirrors the same count (1:1 since
    every node IS a module here).
    """
    for i in range(4000):
        store.upsert_node(
            Node.new(
                type="code_module",
                name=f"P1/mod{i}.py",
                body="...",
                source_path=f"/P1/mod{i}.py",
                source_kind="code_repo",
                project_key="P1",
            )
        )
    resp = client.get("/ui/graph-data?project_keys=P1")
    data = resp.json()
    nodes = [e for e in data["elements"] if "id" in e["data"]]
    # No truncation -- every in-scope node lands on the canvas.
    assert data["truncated"] is False
    assert len(nodes) == 4000
    # tree_modules carries every module by source_path.
    assert "tree_modules" in data
    assert len(data["tree_modules"]) == 4000
    for m in data["tree_modules"][:5]:
        assert "id" in m
        assert "source_path" in m
        assert m["type"] == "code_module"


def test_project_keys_isolates_kept_in_full_graph(client: TestClient, store: Store) -> None:
    """v2.6.0 polish: isolate-drop was a workaround for the previous
    truncation cap; now that we always return the full graph, isolated
    nodes stay so v3 chat can reference them. The client picks a
    layout strategy that handles disconnected components."""
    _seed(store, key="P1", n=50, prefix="iso")
    resp = client.get("/ui/graph-data?project_keys=P1")
    data = resp.json()
    nodes = [e for e in data["elements"] if "id" in e["data"]]
    assert len(nodes) == 50
    assert data["truncated"] is False


def test_project_keys_filters_to_csv_set(client: TestClient, store: Store) -> None:
    _seed(store, key="P1", n=3)
    _seed(store, key="P2", n=2)
    _seed(store, key="P3", n=4)
    resp = client.get("/ui/graph-data?project_keys=P1,P2")
    assert resp.status_code == 200
    data = resp.json()
    keys_seen = {e["data"]["project"] for e in data["elements"] if "id" in e["data"]}
    # P3 dropped; P1 + P2 kept.
    assert "P3" not in keys_seen
    assert {"P1", "P2"} <= keys_seen


def test_project_keys_includes_base(client: TestClient, store: Store) -> None:
    _seed(store, key="P1", n=2)
    _seed(store, key="P_OTHER", n=2)
    _seed(store, key="P_BASE", n=1, base=True)
    resp = client.get("/ui/graph-data?project_keys=P1")
    data = resp.json()
    names = {e["data"].get("name") for e in data["elements"] if "id" in e["data"]}
    # P1 hits surface; BASE node surfaces; OTHER does not.
    assert any(n and "P1" in n for n in names)
    assert "n-P_BASE-0" in names
    assert not any(n and "P_OTHER" in n for n in names)


def test_project_keys_drops_orphan_null_project_nodes(client: TestClient, store: Store) -> None:
    """v2.6.0 polish: NULL-project nodes WITHOUT an edge to in-scope
    don't leak. The 58 auto-derived code_endpoint nodes are the live
    case -- they have project_key=None and clutter every scoped view
    if surfaced unconditionally."""
    _seed(store, key="P1", n=2)
    store.upsert_node(
        Node.new(
            type="memory_feedback",
            name="orphan-null-node",
            body="b",
            source_path="/null/orphan.md",
            source_kind="memory_dir",
            project_key=None,
        )
    )
    resp = client.get("/ui/graph-data?project_keys=P1")
    data = resp.json()
    names = {e["data"].get("name") for e in data["elements"] if "id" in e["data"]}
    assert "orphan-null-node" not in names


def test_project_keys_keeps_edge_connected_null_nodes(client: TestClient, store: Store) -> None:
    """A NULL-project node WITH an edge to an in-scope node DOES
    surface -- this is the 'boundary' connector contract the
    docstring promised. Common case: a code_endpoint that's
    routes_to-linked to a project's handler function."""
    _seed(store, key="P1", n=1, prefix="anchor")
    p1_nodes = store.list_nodes(project_key="P1", limit=10)
    anchor = p1_nodes[0]
    boundary_node = Node.new(
        type="memory_feedback",
        name="connected-null-node",
        body="b",
        source_path="/null/connected.md",
        source_kind="memory_dir",
        project_key=None,
    )
    store.upsert_node(boundary_node)
    store.add_edge(anchor.id, boundary_node.id, "mentions")
    resp = client.get("/ui/graph-data?project_keys=P1")
    data = resp.json()
    names = {e["data"].get("name") for e in data["elements"] if "id" in e["data"]}
    assert "connected-null-node" in names


def test_base_only_returns_only_base_flagged(client: TestClient, store: Store) -> None:
    _seed(store, key="P1", n=3)
    _seed(store, key="P_BASE", n=2, base=True)
    resp = client.get("/ui/graph-data?base_only=1")
    data = resp.json()
    elements = [e for e in data["elements"] if "id" in e["data"]]
    assert len(elements) == 2
    for e in elements:
        # All surfaced nodes must be BASE-flagged; the easiest signal is name.
        assert "P_BASE" in e["data"]["name"]


def test_legacy_single_project_param_still_works(client: TestClient, store: Store) -> None:
    """Deep links from /code etc. use ?project= -- must keep working."""
    _seed(store, key="P1", n=2)
    _seed(store, key="P2", n=3)
    resp = client.get("/ui/graph-data?project=P1")
    data = resp.json()
    names = {e["data"].get("name") for e in data["elements"] if "id" in e["data"]}
    assert any(n and "P1" in n for n in names)
    assert not any(n and "P2" in n for n in names)


def test_no_params_returns_everything(client: TestClient, store: Store) -> None:
    """Backwards compatibility for the global-view case (no workspace)."""
    _seed(store, key="P1", n=3)
    _seed(store, key="P2", n=2)
    resp = client.get("/ui/graph-data")
    data = resp.json()
    elements = [e for e in data["elements"] if "id" in e["data"]]
    assert len(elements) == 5  # 3 + 2


# --- Client wiring ----------------------------------------------------------


def test_graph_html_reads_active_workspace(client: TestClient) -> None:
    """The factory's init() must consult /v1/workspaces/active and
    use its project_keys to scope /ui/graph-data."""
    html = (TEMPLATES_DIR / "graph.html").read_text(encoding="utf-8")
    assert "/v1/workspaces/active" in html
    # Either project_keys CSV or base_only must show up in the query
    # builder.
    assert "project_keys" in html or "base_only" in html


def test_graph_html_subscribes_to_workspace_events(client: TestClient) -> None:
    """When the user switches workspaces from any tab the graph
    re-fetches via the /v1/events SSE channel."""
    html = (TEMPLATES_DIR / "graph.html").read_text(encoding="utf-8")
    assert "workspace_activated" in html or "/v1/events" in html
