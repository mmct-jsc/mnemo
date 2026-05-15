"""Nebula renderer contract (v2.6.2: Cosmograph / @cosmos.gl/graph).

History: v2.2 phase 4 introduced a cytoscape "chunked initial paint"
(``_renderCanvasChunked`` walking nodes by descending degree in
CHUNK_SIZE batches, tagging ``.fade-in``) plus a ``cy.animate(...)``
camera pan on focus. v2.6.1 then proved cytoscape's canvas renderer
physically cannot paint 10 k+ sharp nodes per frame, forcing a 2 k
degree cap that misled the future v3 chat companion + broke tree
navigation.

v2.6.2 replaces cytoscape entirely with Cosmograph's lean GPU engine
(``@cosmos.gl/graph``). The GPU force simulation IS the progressive
reveal -- points fly into their cluster positions in real time, so
the hand-rolled chunked paint, the ``.fade-in`` cadence, and the
``cy.animate`` camera tween are all gone by design (see
docs/plans/2026-05-15-nebula-cosmograph-webgl-design.md).

We can't run WebGL in pytest, but we CAN lock the SURFACE of the new
renderer + the parts of the old contract that still hold (the side-
panel body still streams via ``mnemoRenderBody``; the neighbors list
still uses ``mnemoStaggeredReveal``; the page still 200s).
"""

from __future__ import annotations

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


@pytest.fixture(scope="module")
def graph_html() -> str:
    path = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates" / "graph.html"
    return path.read_text(encoding="utf-8")


# --- v2.6.2 Cosmograph renderer contract --------------------------------


def test_graph_loads_cosmos_gl_engine(graph_html: str) -> None:
    """The page must import the lean ``@cosmos.gl/graph`` GPU engine
    as an ESM module (NOT the heavyweight @cosmograph/cosmograph
    wrapper, which drags in duckdb-wasm + supabase + mosaic)."""
    assert "@cosmos.gl/graph" in graph_html, (
        "graph.html must import @cosmos.gl/graph -- the lean GPU "
        "force-graph engine that replaced cytoscape in v2.6.2."
    )
    assert 'type="module"' in graph_html, (
        'Cosmograph is ESM-only; it must load via a <script type="module"> tag.'
    )


def test_graph_has_no_cytoscape(graph_html: str) -> None:
    """Cytoscape is fully removed -- no <script> include, no
    ``cytoscape(`` factory call, no fcose plugin, no ``cy.`` API.
    (The word may still appear in the head comment that explains
    *why* we switched away from it -- that's intentional history,
    so we assert on USAGE patterns, not the bare substring.)"""
    usage_markers = (
        "unpkg.com/cytoscape",  # the old CDN <script src>
        "cytoscape-fcose",  # the old layout plugin
        "cytoscape({",  # the factory call
        "cytoscape.use(",  # plugin registration
        "this.cy =",  # the old instance handle
        "cy.elements(",  # cytoscape collection API
    )
    hits = [m for m in usage_markers if m in graph_html]
    assert not hits, (
        f"v2.6.2 removed cytoscape entirely; found lingering cytoscape usage in graph.html: {hits}"
    )


def test_graph_has_no_degree_cap(graph_html: str) -> None:
    """The v2.6.1 GRAPH_CANVAS_CAP degree cap is gone -- Cosmograph
    renders the FULL graph so the canvas mirrors exactly what the
    v3 chat companion analyzes."""
    assert "GRAPH_CANVAS_CAP" not in graph_html, (
        "v2.6.2 removed the canvas degree cap; the full graph is always rendered now."
    )


def test_graph_has_force_simulation_config(graph_html: str) -> None:
    """The organic 'nebula' look is a GPU force simulation -- the
    config must set the simulation knobs (gravity / repulsion /
    link spring)."""
    for knob in ("simulationGravity", "simulationRepulsion", "simulationLinkSpring"):
        assert knob in graph_html, (
            f"graph.html must configure {knob} -- the force-simulation "
            "parameters that produce the organic nebula layout."
        )


def test_graph_config_applied_via_setconfig(graph_html: str) -> None:
    """cosmos.gl v3's constructor IGNORES a config argument (verified
    in the preview -- passing it leaves every sim param at default and
    points collapse coincident). Config MUST go through setConfig()
    after construction; lock that so a refactor can't regress it."""
    assert "setConfig(config)" in graph_html or "setConfig(" in graph_html, (
        "renderCanvas must call cg.setConfig(...) after `new Graph()` "
        "-- the constructor-config path is a known cosmos.gl no-op."
    )


def test_graph_no_ego_fetch_on_tree_click(graph_html: str) -> None:
    """The cap-era bug: clicking a capped-out node ran focusNode ->
    reload -> ?node= ego-fetch -> standalone node + collapsed tree +
    no way back. v2.6.2 deletes that path: every node is always in
    the full graph so focusNode just flies the camera."""
    assert "this.contextNodeId = id" not in graph_html, (
        "focusNode must NOT set contextNodeId + reload (the ego-fetch "
        "path that collapsed the tree). It should select within the "
        "full graph instead."
    )
    assert "selectByIndex" in graph_html, (
        "focusNode should resolve the id to a point index and call "
        "selectByIndex (camera fly within the full graph)."
    )


# --- Carried-forward contract (still valid post-swap) -------------------


def test_focus_node_streams_body_via_mnemo_render_body(graph_html: str) -> None:
    """The side-panel body still reveals progressively via the v2.2
    streaming primitive -- now through ``window.mnemoRenderBody``
    (type-aware: code -> Prism, commit -> pre, else -> markdown)."""
    assert "mnemoRenderBody" in graph_html, (
        "graph.html must delegate the detail-panel body to "
        "window.mnemoRenderBody so it reveals progressively + "
        "type-aware."
    )


def test_focus_node_uses_staggered_reveal_for_neighbors(graph_html: str) -> None:
    """The neighbors list still renders via ``mnemoStaggeredReveal``
    so items appear paced instead of popping in together."""
    assert "mnemoStaggeredReveal" in graph_html, (
        "graph.html must call window.mnemoStaggeredReveal for the "
        "neighbors list so items reveal paced."
    )


# --- Live page renders -- regression smoke ------------------------------


def test_graph_page_still_renders(client: TestClient) -> None:
    """The renderer swap is structural; the page must still 200 with
    the shell + canvas mount point intact."""
    r = client.get("/graph")
    assert r.status_code == 200, (
        f"GET /graph should 200 after the Cosmograph swap; got {r.status_code}"
    )
    body = r.text
    assert "nebula-shell" in body
    assert "cy-nebula" in body
