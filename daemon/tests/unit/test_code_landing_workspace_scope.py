"""v2.6.0 polish: /code landing scopes by active workspace.

The /code page was showing every indexed code project regardless of
workspace. When the active workspace doesn't include the code repo's
project_key, the page used to lie ("here are all 506 mnemo-daemon
functions" inside an aibox-only workspace). Now the page filters to
the active workspace's project_keys and shows a helpful empty state
when the workspace has no code projects in scope.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from mnemo import workspaces
from mnemo.server import create_app
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


def _seed_code(store: Store, project_key: str, *, modules: int = 1, funcs: int = 1) -> None:
    for i in range(modules):
        store.upsert_node(
            Node.new(
                type="code_module",
                name=f"{project_key}/mod{i}.py",
                body="...",
                source_path=f"/{project_key}/mod{i}.py",
                source_kind="code_repo",
                project_key=project_key,
            )
        )
    for i in range(funcs):
        store.upsert_node(
            Node.new(
                type="code_function",
                name=f"{project_key}::func{i}",
                body="def f(): ...",
                source_path=f"/{project_key}/mod0.py:{i * 10}-{(i + 1) * 10}",
                source_kind="code_repo",
                project_key=project_key,
            )
        )


def test_code_landing_base_only_when_no_workspace(client: TestClient, store: Store) -> None:
    """v2.6 design: no active workspace -> BASE-only UI mode.
    /code in BASE-only mode is empty (code nodes are never BASE-flagged)
    and shows a "Pick a workspace" empty state."""
    _seed_code(store, "P1")
    _seed_code(store, "P2")
    resp = client.get("/code")
    assert resp.status_code == 200
    body = resp.text
    # P1 + P2 code cards should NOT render; the BASE-only empty state should.
    assert "BASE-only" in body or "Pick a workspace" in body
    # The big code-project card grid should not show non-BASE projects.
    assert "code-project-card-link" not in body or ("P1" not in body and "P2" not in body)


def test_code_landing_filters_to_workspace_keys(client: TestClient, store: Store) -> None:
    """With an active workspace, /code only surfaces projects whose
    project_key matches the workspace's set."""
    _seed_code(store, "P1")
    _seed_code(store, "P2")
    _seed_code(store, "OUT_OF_SCOPE")
    ws = workspaces.create_workspace(store, name="ws", project_keys=["P1", "P2"])
    workspaces.set_active_workspace(store, ws.id)

    resp = client.get("/code")
    body = resp.text
    assert "P1" in body
    assert "P2" in body
    assert "OUT_OF_SCOPE" not in body


def test_code_landing_empty_state_when_workspace_has_no_code(
    client: TestClient, store: Store
) -> None:
    """An aibox-only workspace (memory-only project_keys) hits the
    empty state instead of listing every code project."""
    _seed_code(store, "mnemo-daemon", modules=5)
    # Workspace points at a project_key that has NO code nodes.
    ws = workspaces.create_workspace(store, name="memory-only", project_keys=["AIBOX"])
    workspaces.set_active_workspace(store, ws.id)

    resp = client.get("/code")
    body = resp.text
    assert "mnemo-daemon" not in body
    # The empty state copy should mention the workspace name OR
    # signal "no code in this workspace".
    assert (
        "No code" in body
        or "no code" in body
        or "workspace has no code" in body.lower()
        or "memory-only" in body
    )


def test_code_landing_empty_state_in_base_only_mode(client: TestClient, store: Store) -> None:
    """No workspace -> BASE-only UI mode -> /code shows empty state
    since code nodes are project-scoped, never BASE-flagged."""
    _seed_code(store, "mnemo-daemon", modules=5)
    workspaces.clear_active_workspace(store)
    # A workspace exists but isn't active.
    ws = workspaces.create_workspace(store, name="other", project_keys=["mnemo-daemon"])
    workspaces.set_active_workspace(store, ws.id)
    workspaces.clear_active_workspace(store)

    resp = client.get("/code")
    body = resp.text
    # In BASE-only mode the /code page should not list code projects.
    assert "mnemo-daemon" not in body
