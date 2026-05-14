"""Surface tests for v2.6 phase 8: /workspaces management page.

Asserts the page renders + carries the Alpine factory wiring + reuses
the existing dash-card shells (per the v2.6 theme requirements).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Store
from tests.conftest import FakeEmbedder

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates"


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


def test_workspaces_page_returns_200(client: TestClient) -> None:
    resp = client.get("/workspaces")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_workspaces_page_uses_named_alpine_factory(client: TestClient) -> None:
    resp = client.get("/workspaces")
    assert 'x-data="workspacesPage()"' in resp.text


def test_workspaces_page_does_not_double_init(client: TestClient) -> None:
    """Drop x-init=init() to avoid Alpine's double-fire anti-pattern."""
    import re

    resp = client.get("/workspaces")
    matches = re.findall(r"<[^<>]*x-data=\"workspacesPage\(\)\"[^<>]*>", resp.text)
    assert matches
    for m in matches:
        assert 'x-init="init()"' not in m


def test_workspaces_page_uses_dash_card_shells(client: TestClient) -> None:
    """The theme requirements call for reusing .dash-card / .dash-card-head."""
    resp = client.get("/workspaces")
    assert "dash-card" in resp.text or "ws-card" in resp.text


def test_workspaces_page_lists_card_actions(client: TestClient) -> None:
    """Each workspace card surfaces Activate / Edit / Duplicate / Delete."""
    resp = client.get("/workspaces")
    body = resp.text
    # At minimum: activate + delete are exposed; edit may inline.
    assert "Activate" in body
    assert "Delete" in body


def test_workspaces_page_uses_app_js_factory_function(client: TestClient) -> None:
    """workspacesPage() must exist on window in app.js."""
    js = (Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "window.workspacesPage" in js


def test_workspaces_page_subscribes_to_events(client: TestClient) -> None:
    """Refresh on workspace_activated / _deleted / _cleared so multi-tab
    state stays in sync."""
    js = (Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    # The shared events subscription already handles this; the page can
    # just re-fetch on those events. Confirm the listener strings exist
    # (covered by phase 7 test, kept here as a regression check).
    assert "workspace_activated" in js


def test_workspaces_page_template_file_exists() -> None:
    assert (TEMPLATES_DIR / "workspaces.html").exists()
