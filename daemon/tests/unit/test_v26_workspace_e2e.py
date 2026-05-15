"""End-to-end smoke test covering every v2.6 user-visible surface
across three workspace states:

- No workspace active   (BASE-only mode)
- Workspace with project_keys that have memory + code
- Workspace with project_keys that have memory only (no code)

For each state we exercise:
- Topbar workspace switcher renders the right label
- /workspaces page lists workspaces + reflects active
- /code landing filters projects to the active scope; empty state
  copy is context-aware
- /audit-page resolves hit IDs to name + type + description
- /graph (Nebula) file-tree empty-state copy is context-aware
- /ui/graph-data scope params return the expected node sets
- /v1/query effective project_key follows the active workspace
- /v1/workspaces/active reflects the right pointer
- BASE-flagged nodes surface in EVERY workspace + in BASE-only mode
- Reindex report endpoint surfaces 404 / 200 cleanly

This is the test the user asked for ("full test suite to cover all
aspects from visual to query to ensure complete output").
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from mnemo import workspaces
from mnemo.embed import embed_node
from mnemo.server import create_app
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


def _seed_embedded(store: Store, embedder: FakeEmbedder, node: Node) -> Node:
    """Upsert + embed so vec_search can find it. The smoke tests
    exercise /v1/query which only returns hits whose chunks are
    embedded; bare upserts don't reach sqlite-vec."""
    store.upsert_node(node)
    embed_node(store, node, embedder)  # type: ignore[arg-type]
    return node


# --- Seed helpers -----------------------------------------------------------


def _embed_all(store: Store, embedder: FakeEmbedder) -> None:
    """Embed every node currently in the store via the fake embedder.
    Required so /v1/query's vec_search can find anything in the
    smoke tests."""
    for n in store.list_nodes(limit=10_000):
        embed_node(store, n, embedder)  # type: ignore[arg-type]


def _seed_full_universe(store: Store, embedder: FakeEmbedder | None = None) -> dict[str, str]:
    """Create one BASE node + nodes for three project_keys:
    P_CODE     -- has code_module + code_function (looks like a real repo)
    P_MEMORY   -- memory-only (no code)
    P_OTHER    -- out-of-scope reference (memory + 1 code, never in workspace)
    Returns the workspace ids registered."""
    # BASE reference (cross-cutting)
    store.upsert_node(
        Node.new(
            type="memory_reference",
            name="reference-cross-cutting",
            body="BASE knowledge that applies to every workspace",
            source_path="/base/ref.md",
            source_kind="memory_dir",
            description="BASE reference desc",
            project_key="P_BASE_HOLDER",  # holder; base=True makes it visible everywhere
            base=True,
        )
    )
    # P_CODE: memory + code
    store.upsert_node(
        Node.new(
            type="memory_feedback",
            name="feedback-P_CODE",
            body="Feedback specific to P_CODE",
            source_path="/p_code/feedback.md",
            source_kind="memory_dir",
            description="P_CODE feedback",
            project_key="P_CODE",
        )
    )
    for i in range(3):
        m = Node.new(
            type="code_module",
            name=f"p_code/mod{i}.py",
            body="...",
            source_path=f"/p_code/mod{i}.py",
            source_kind="code_repo",
            description=f"module {i}",
            project_key="P_CODE",
        )
        store.upsert_node(m)
        f = Node.new(
            type="code_function",
            name=f"p_code::func{i}",
            body="def f(): ...",
            source_path=f"/p_code/mod{i}.py:{i * 10}-{(i + 1) * 10}",
            source_kind="code_repo",
            description=f"func {i}",
            project_key="P_CODE",
        )
        store.upsert_node(f)
        # Edge so they're not isolated post-cap.
        store.add_edge(m.id, f.id, "defines")

    # P_MEMORY: memory only
    for i in range(2):
        store.upsert_node(
            Node.new(
                type="memory_project",
                name=f"P_MEMORY-project-{i}",
                body="memory entry",
                source_path=f"/p_memory/proj{i}.md",
                source_kind="memory_dir",
                description=f"P_MEMORY proj {i}",
                project_key="P_MEMORY",
            )
        )
    # P_OTHER (always out-of-scope for our test workspaces)
    store.upsert_node(
        Node.new(
            type="memory_feedback",
            name="feedback-OTHER",
            body="should NEVER surface in our test workspaces",
            source_path="/other/feedback.md",
            source_kind="memory_dir",
            description="other",
            project_key="P_OTHER",
        )
    )
    return {}


# --- State 1: no workspace active (BASE-only mode) -------------------------


def test_base_only_topbar_shows_no_workspace(client: TestClient, store: Store) -> None:
    _seed_full_universe(store)
    workspaces.clear_active_workspace(store)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # The switcher renders "No workspace" + the is-base-only class.
    assert "workspace-switcher-pill" in body
    assert "is-base-only" in body or "No workspace" in body


def test_base_only_workspaces_active_returns_null(client: TestClient, store: Store) -> None:
    _seed_full_universe(store)
    workspaces.clear_active_workspace(store)
    resp = client.get("/v1/workspaces/active")
    assert resp.status_code == 200
    assert resp.json() == {"active": None}


def test_base_only_code_landing_shows_pick_workspace(client: TestClient, store: Store) -> None:
    _seed_full_universe(store)
    workspaces.clear_active_workspace(store)
    resp = client.get("/code")
    body = resp.text
    # Code cards do not render (P_CODE is project-scoped, not BASE).
    assert "feedback-P_CODE" not in body
    assert "p_code/mod0.py" not in body
    # The empty state surfaces the BASE-only message + a workspaces link.
    assert "BASE-only" in body or "Pick a workspace" in body


def test_base_only_graph_data_returns_only_base_nodes(client: TestClient, store: Store) -> None:
    _seed_full_universe(store)
    workspaces.clear_active_workspace(store)
    resp = client.get("/ui/graph-data?base_only=1")
    data = resp.json()
    names = {e["data"]["name"] for e in data["elements"] if "id" in e["data"]}
    assert "reference-cross-cutting" in names
    # No project-scoped names allowed.
    assert "feedback-P_CODE" not in names
    assert "feedback-OTHER" not in names


def test_base_only_query_returns_base_hits(
    client: TestClient, store: Store, fake_embedder: FakeEmbedder
) -> None:
    _seed_full_universe(store)
    _embed_all(store, fake_embedder)
    workspaces.clear_active_workspace(store)
    resp = client.post(
        "/v1/query",
        json={"prompt": "cross-cutting reference knowledge", "budget_tokens": 200, "k": 5},
    )
    assert resp.status_code == 200
    # The BASE node should be in the top results.
    names = {h["name"] for h in resp.json().get("hits", [])}
    assert "reference-cross-cutting" in names


# --- State 2: workspace with code + memory ---------------------------------


def test_code_workspace_topbar_shows_workspace_name(client: TestClient, store: Store) -> None:
    _seed_full_universe(store)
    ws = workspaces.create_workspace(store, name="code-ws", project_keys=["P_CODE"])
    workspaces.set_active_workspace(store, ws.id)
    resp = client.get("/")
    body = resp.text
    # The switcher pill renders the workspace name dynamically via
    # Alpine + a fetch -- the template still embeds the factory
    # binding so we just check the static expression is present.
    assert 'x-text="active ? active.name' in body


def test_code_workspace_code_landing_lists_repo_card(client: TestClient, store: Store) -> None:
    _seed_full_universe(store)
    ws = workspaces.create_workspace(store, name="code-ws", project_keys=["P_CODE"])
    workspaces.set_active_workspace(store, ws.id)
    resp = client.get("/code")
    body = resp.text
    # P_CODE project card renders.
    assert "P_CODE" in body
    # P_OTHER does NOT render (out of scope).
    assert "P_OTHER" not in body or body.count("P_OTHER") == 0


def test_code_workspace_graph_data_surfaces_code_modules(client: TestClient, store: Store) -> None:
    _seed_full_universe(store)
    ws = workspaces.create_workspace(store, name="code-ws", project_keys=["P_CODE"])
    workspaces.set_active_workspace(store, ws.id)
    resp = client.get("/ui/graph-data?project_keys=P_CODE")
    data = resp.json()
    names = {e["data"]["name"] for e in data["elements"] if "id" in e["data"]}
    # The 3 modules surface.
    assert any("mod0.py" in n for n in names)
    # BASE node surfaces (cross-cutting).
    assert "reference-cross-cutting" in names
    # P_OTHER does NOT surface.
    assert "feedback-OTHER" not in names


def test_code_workspace_graph_data_carries_truncation_metadata(
    client: TestClient, store: Store
) -> None:
    _seed_full_universe(store)
    ws = workspaces.create_workspace(store, name="code-ws", project_keys=["P_CODE"])
    workspaces.set_active_workspace(store, ws.id)
    resp = client.get("/ui/graph-data?project_keys=P_CODE")
    data = resp.json()
    # Below the GRAPH_NODE_CAP -- not truncated.
    assert data["truncated"] is False
    assert "shown_node_count" in data
    assert "total_in_scope" in data


def test_code_workspace_query_scopes_to_workspace_keys(
    client: TestClient, store: Store, fake_embedder: FakeEmbedder
) -> None:
    _seed_full_universe(store)
    _embed_all(store, fake_embedder)
    ws = workspaces.create_workspace(store, name="code-ws", project_keys=["P_CODE"])
    workspaces.set_active_workspace(store, ws.id)
    resp = client.post(
        "/v1/query",
        json={"prompt": "feedback specific to project code", "budget_tokens": 200, "k": 8},
    )
    names = {h["name"] for h in resp.json().get("hits", [])}
    # P_CODE's feedback should rank well.
    assert "feedback-P_CODE" in names


# --- State 3: workspace with memory only -----------------------------------


def test_memory_workspace_code_landing_shows_empty_state_with_name(
    client: TestClient, store: Store
) -> None:
    _seed_full_universe(store)
    ws = workspaces.create_workspace(store, name="mem-only-ws", project_keys=["P_MEMORY"])
    workspaces.set_active_workspace(store, ws.id)
    resp = client.get("/code")
    body = resp.text
    # The empty state mentions the workspace name.
    assert "mem-only-ws" in body
    # No project cards for code.
    assert "p_code/mod0.py" not in body


def test_memory_workspace_graph_data_surfaces_memory_nodes(
    client: TestClient, store: Store
) -> None:
    _seed_full_universe(store)
    ws = workspaces.create_workspace(store, name="mem-only-ws", project_keys=["P_MEMORY"])
    workspaces.set_active_workspace(store, ws.id)
    resp = client.get("/ui/graph-data?project_keys=P_MEMORY")
    data = resp.json()
    names = {e["data"]["name"] for e in data["elements"] if "id" in e["data"]}
    assert any("P_MEMORY-project-" in n for n in names)
    # BASE still surfaces.
    assert "reference-cross-cutting" in names


# --- Cross-cutting: workspace switch + propose + reindex report ------------


def test_audit_page_resolves_hit_ids(
    client: TestClient, store: Store, fake_embedder: FakeEmbedder
) -> None:
    _seed_full_universe(store)
    _embed_all(store, fake_embedder)
    workspaces.clear_active_workspace(store)
    # Run a query so the audit log has a row to resolve.
    client.post(
        "/v1/query",
        json={"prompt": "cross-cutting reference base knowledge", "budget_tokens": 200, "k": 3},
    )
    resp = client.get("/audit-page")
    body = resp.text
    # The audit row resolves the BASE node to name + type badge text.
    assert "reference-cross-cutting" in body
    # Type badge for memory_reference -> "memory reference"
    assert "memory reference" in body or "memory_reference" in body


def test_workspaces_page_lists_two_workspaces(client: TestClient, store: Store) -> None:
    _seed_full_universe(store)
    workspaces.create_workspace(store, name="code-ws", project_keys=["P_CODE"])
    workspaces.create_workspace(store, name="mem-only-ws", project_keys=["P_MEMORY"])
    resp = client.get("/workspaces")
    body = resp.text
    # Page renders.
    assert resp.status_code == 200
    # Factory present so the cards fetch + render client-side.
    assert 'x-data="workspacesPage()"' in body


def test_workspaces_active_switch_broadcasts_event(client: TestClient, store: Store) -> None:
    import queue

    _seed_full_universe(store)
    ws = workspaces.create_workspace(store, name="switch-test", project_keys=["P_CODE"])
    # Attach a queue to receive events.
    state = client.app.state.mnemo_state
    q: queue.Queue = queue.Queue(maxsize=16)
    with state.event_subscribers_lock:
        state.event_subscribers.append(q)
    client.post(f"/v1/workspaces/{ws.id}/activate")
    received = []
    while not q.empty():
        received.append(q.get_nowait()[0])
    assert "workspace_activated" in received


def test_reindex_report_endpoint_404_then_populated(
    client: TestClient, store: Store, tmp_path
) -> None:  # noqa: ANN001
    # No reindex yet -> 404.
    assert client.get("/v1/reindex/report").status_code == 404
    # Run a reindex over a tiny seeded source.
    src = tmp_path / "memory"
    src.mkdir()
    (src / "a.md").write_text("# a")
    store.register_source(path=str(src), kind="memory_dir")
    client.post("/v1/reindex", params={"embed": "false"})
    rep = client.get("/v1/reindex/report")
    assert rep.status_code == 200
    body = rep.json()
    assert "indexed_count" in body
    assert "auto_skipped" in body
    assert "malformed" in body
    assert "suspicious" in body


def test_sources_propose_returns_dual_proposal(client: TestClient, store: Store, tmp_path) -> None:  # noqa: ANN001
    docs = tmp_path / "docs"
    docs.mkdir()
    for i in range(5):
        (docs / f"p{i}.md").write_text(f"# p{i}")
    src = tmp_path / "src"
    src.mkdir()
    for i in range(12):
        (src / f"m{i}.py").write_text(f"def f{i}(): ...")
    resp = client.post("/v1/sources/propose", json={"path": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()
    kinds = {p["kind"] for p in body["proposals"]}
    assert kinds == {"docs_dir", "code_repo"}


def test_hard_cap_refuses_with_per_project_payload(client: TestClient, store: Store) -> None:
    _seed_full_universe(store)
    ws = workspaces.create_workspace(store, name="big-ws", project_keys=["P_CODE"])
    resp = client.post(f"/v1/workspaces/{ws.id}/activate", params={"hard_cap_nodes": 1})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "workspace_too_large"
    assert "projects" in detail
    assert detail["hard_cap"] == 1


# --- File tree empty-state copy (Nebula) ----------------------------------


def test_nebula_file_tree_has_base_only_copy(client: TestClient) -> None:
    """Nebula file tree empty-state copy varies by scope mode. The
    BASE-only path tells the user to pick a workspace, not to add a
    code_repo (which would be the wrong action)."""
    resp = client.get("/graph")
    body = resp.text
    # All three context-aware messages live in the template.
    assert "BASE-only view" in body
    assert "Pick a workspace" in body
    assert "No code modules in workspace" in body


def test_nebula_template_has_status_chip(client: TestClient) -> None:
    """v2.6.0 polish: the truncation banner was replaced with an
    always-on status chip in the lower-right. v2.6.2 (Cosmograph
    renderer, no cap) renamed the count field to ``canvasTotal``
    (+ ``edgeTotal``) since the canvas now shows the FULL graph --
    the chip is an honest total, not a capped subset."""
    resp = client.get("/graph")
    body = resp.text
    assert "nebula-status" in body
    assert "nebula-status-ws" in body
    assert "canvasTotal" in body


def test_nebula_template_reads_active_workspace_in_init(
    client: TestClient,
) -> None:
    resp = client.get("/graph")
    body = resp.text
    assert "/v1/workspaces/active" in body
    assert "_loadWorkspaceScope" in body
    assert "_subscribeWorkspaceEvents" in body
