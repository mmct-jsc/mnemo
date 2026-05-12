"""UI route smoke tests via TestClient.

We don't render-test the JS layer (HTMX / Cytoscape) - just verify each page
returns 200 with the expected anchor text, and that HTMX fragments render
correctly when their JSON-API counterparts are working.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Store
from tests.conftest import FakeEmbedder


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


def _seed(client: TestClient, tmp_path: Path) -> str:
    """Add a tiny memory dir, reindex, return the first node id."""
    src = tmp_path / "mem"
    src.mkdir()
    (src / "feedback_x.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: rule-x
            description: short rule for testing
            type: feedback
            ---
            Body of rule x.
            """
        ),
        encoding="utf-8",
    )
    client.post("/sources", json={"path": str(src), "kind": "memory_dir"})
    client.post("/reindex")
    return client.get("/nodes").json()[0]["id"]


# --- pages ---------------------------------------------------------------


def test_index_renders(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "mnemo" in r.text.lower()
    assert "search" in r.text.lower()


def test_graph_page_renders(client: TestClient) -> None:
    r = client.get("/graph")
    assert r.status_code == 200
    assert "cytoscape" in r.text.lower()
    assert 'id="cy"' in r.text


def test_sources_page_renders(client: TestClient) -> None:
    r = client.get("/sources-page")
    assert r.status_code == 200
    assert "Sources" in r.text


def test_audit_page_renders_empty(client: TestClient) -> None:
    r = client.get("/audit-page")
    assert r.status_code == 200
    assert "Audit log" in r.text


def test_dashboard_renders(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "Welcome back" in r.text or "memory entries" in r.text


def test_nodes_page_renders_empty(client: TestClient) -> None:
    r = client.get("/nodes-page")
    assert r.status_code == 200
    assert "Nodes" in r.text


def test_nodes_page_pagination_present(client: TestClient, tmp_path: Path) -> None:
    _seed(client, tmp_path)
    r = client.get("/nodes-page")
    assert r.status_code == 200
    # Pagination summary block always renders when there are nodes.
    assert "Showing" in r.text


def test_node_page_highlights_nodes_navbar(client: TestClient, tmp_path: Path) -> None:
    import re

    nid = _seed(client, tmp_path)
    r = client.get(f"/node/{nid}")
    assert r.status_code == 200
    # When on /node/<id>, the navbar 'Nodes' link should be marked active.
    assert re.search(r'href="/nodes-page"\s+class="active"', r.text)


def test_settings_page_renders(client: TestClient) -> None:
    r = client.get("/settings")
    assert r.status_code == 200
    assert "alpha" in r.text
    assert "budget_tokens" in r.text


def test_node_page_404_when_missing(client: TestClient) -> None:
    r = client.get("/node/nonexistent")
    assert r.status_code == 404


def test_node_page_renders_for_real_node(client: TestClient, tmp_path: Path) -> None:
    nid = _seed(client, tmp_path)
    r = client.get(f"/node/{nid}")
    assert r.status_code == 200
    assert nid in r.text
    assert "rule-x" in r.text
    assert "Body of rule x" in r.text


# --- HTMX fragments ------------------------------------------------------


def test_search_fragment_empty_query(client: TestClient) -> None:
    r = client.get("/ui/search", params={"q": ""})
    assert r.status_code == 200
    assert r.text.strip() == ""


def test_search_fragment_returns_hits_html(client: TestClient, tmp_path: Path) -> None:
    _seed(client, tmp_path)
    r = client.get("/ui/search", params={"q": "rule"})
    assert r.status_code == 200
    # Fragment includes the meta line and at least one hit
    assert "intent:" in r.text
    assert "rule-x" in r.text
    assert "[mnemo:" in r.text


def test_search_fragment_renders_thumb_buttons_per_hit(client: TestClient, tmp_path: Path) -> None:
    """v1.2 phase 3: every rendered hit gets a thumb-up and thumb-down
    button. The buttons carry the node_id so the click handler can POST
    /v1/feedback with the right body."""
    nid = _seed(client, tmp_path)
    r = client.get("/ui/search", params={"q": "rule"})
    assert r.status_code == 200
    # Two thumb buttons should appear -- one up, one down.
    assert 'class="thumb-btn thumb-up"' in r.text
    assert 'class="thumb-btn thumb-down"' in r.text
    # The node_id flows into the click handler so the POST body resolves.
    assert nid in r.text


def test_search_fragment_embeds_query_id_for_feedback(client: TestClient, tmp_path: Path) -> None:
    """The Alpine x-data factory needs the query_id passed in at render
    time so it can include it in the POST /v1/feedback body without
    parsing the page. ``hitsFeedback(<query_id>)`` is the canonical call
    site."""
    _seed(client, tmp_path)
    r = client.get("/ui/search", params={"q": "rule"})
    assert r.status_code == 200
    assert "hitsFeedback(" in r.text  # factory invocation
    # The current query_id is rendered into the call. Audit log
    # confirms a query exists post-fetch; grab its id and check.
    audit = client.get("/audit").json()
    assert audit
    latest_qid = audit[0]["id"]
    assert latest_qid in r.text


def test_base_page_defines_hits_feedback_factory(client: TestClient) -> None:
    """The Alpine factory must be declared in base.html (or an asset
    base.html loads) so any page hosting the search-results HTMX swap
    can use it. We assert the factory function name is visible on a
    plain page render."""
    r = client.get("/")
    assert r.status_code == 200
    assert "function hitsFeedback" in r.text


def test_graph_data_returns_elements(client: TestClient, tmp_path: Path) -> None:
    _seed(client, tmp_path)
    r = client.get("/ui/graph-data")
    assert r.status_code == 200
    data = r.json()
    assert "elements" in data
    # At least one node element is present after seeding.
    nodes = [e for e in data["elements"] if "id" in e["data"]]
    assert len(nodes) >= 1


def test_graph_data_empty_store(client: TestClient) -> None:
    r = client.get("/ui/graph-data")
    assert r.status_code == 200
    assert r.json() == {"elements": []}
