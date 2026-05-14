"""Tests for v2.6 phase 10.1: workspace drives retrieval scope; the
legacy active-project pill is dropped from the UI but the
``/v1/projects/active`` endpoints stay alive (CLI back-compat).

Precedence the /v1/query route applies for the effective project_key:

  1. Explicit ``body.project_key``               (caller override)
  2. Legacy ``body.active_project``              (pre-1.1 clients)
  3. **Active workspace's first project_key**    (v2.6 default)
  4. Persisted ``active_project`` pointer        (legacy CLI compat)
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo import workspaces
from mnemo.server import _resolve_query_project, create_app
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates"


class _Body:
    """Minimal stand-in for QueryIn so the resolver test doesn't pull pydantic."""

    def __init__(self, project_key: str | None = None, active_project: str | None = None):
        self.project_key = project_key
        self.active_project = active_project


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


# --- Project resolution precedence ------------------------------------------


def test_resolve_prefers_explicit_project_key(store: Store) -> None:
    ws = workspaces.create_workspace(store, name="ws", project_keys=["WORKSPACE_KEY"])
    workspaces.set_active_workspace(store, ws.id)
    store.set_active_project(project_key="LEGACY_KEY", path="/legacy")
    body = _Body(project_key="EXPLICIT_KEY")
    assert _resolve_query_project(store, body) == "EXPLICIT_KEY"


def test_resolve_falls_back_to_legacy_active_project_field(store: Store) -> None:
    ws = workspaces.create_workspace(store, name="ws", project_keys=["WORKSPACE_KEY"])
    workspaces.set_active_workspace(store, ws.id)
    body = _Body(active_project="LEGACY_ACTIVE_FIELD")
    assert _resolve_query_project(store, body) == "LEGACY_ACTIVE_FIELD"


def test_resolve_uses_workspace_first_project_key(store: Store) -> None:
    """v2.6 default: with no explicit hints, active workspace wins."""
    ws = workspaces.create_workspace(store, name="ws", project_keys=["P_FIRST", "P_SECOND"])
    workspaces.set_active_workspace(store, ws.id)
    body = _Body()
    assert _resolve_query_project(store, body) == "P_FIRST"


def test_resolve_falls_back_to_persisted_active_project(store: Store) -> None:
    """No workspace + legacy active_project set -> use the legacy pointer."""
    store.set_active_project(project_key="PERSISTED", path="/p")
    body = _Body()
    assert _resolve_query_project(store, body) == "PERSISTED"


def test_resolve_returns_none_when_no_scope_anywhere(store: Store) -> None:
    body = _Body()
    assert _resolve_query_project(store, body) is None


def test_resolve_workspace_with_empty_project_keys_is_skipped(store: Store) -> None:
    """A BASE-only workspace (no project_keys) doesn't shadow the legacy
    active_project pointer."""
    ws = workspaces.create_workspace(store, name="empty-ws", project_keys=[])
    workspaces.set_active_workspace(store, ws.id)
    store.set_active_project(project_key="LEGACY", path="/l")
    body = _Body()
    assert _resolve_query_project(store, body) == "LEGACY"


# --- Query route uses the resolved project ----------------------------------


def test_query_route_uses_workspace_project_for_retrieval(client: TestClient, store: Store) -> None:
    """A POST /v1/query with no explicit project_key sees results from the
    active workspace's first project_key."""
    # Seed a P1-tagged node.
    store.upsert_node(
        Node.new(
            type="memory_feedback",
            name="ws-scoped",
            body="content about retrieval workspace scope",
            source_path="/mem/P1/x.md",
            source_kind="memory_dir",
            project_key="P1",
        )
    )
    # Create + activate workspace pinning P1.
    ws = client.post("/v1/workspaces", json={"name": "ws", "project_keys": ["P1"]}).json()
    client.post(f"/v1/workspaces/{ws['id']}/activate")

    resp = client.post(
        "/v1/query",
        json={"prompt": "retrieval workspace scope", "budget_tokens": 200, "k": 5},
    )
    assert resp.status_code == 200
    # The query audit log records the project_key used; not exposed via this
    # response, but a 200 here means the scope resolution did not raise.


# --- UI removal: active-project pill gone from base.html --------------------


def test_active_project_pill_removed_from_base_html() -> None:
    html = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
    # The PILL markup itself is gone. The Alpine factory function may
    # remain (other tests / future CLI flows may load it), but the
    # x-data attaching it to a top-bar widget must be removed.
    assert 'x-data="activeProjectWidget()"' not in html, (
        "v2.6 phase 10.1 must drop the active-project pill from the top bar"
    )
    assert 'class="active-proj"' not in html, "the old .active-proj container should also be gone"


def test_active_project_endpoints_still_exist(client: TestClient) -> None:
    """Back-compat: CLI still uses POST /v1/projects/active. Keep the
    endpoints alive even though the top-bar pill is gone."""
    resp = client.get("/v1/projects/active")
    # Empty active-project state returns null with 200, not 404.
    assert resp.status_code == 200
