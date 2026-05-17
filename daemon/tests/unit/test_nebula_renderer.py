"""Nebula renderer contract (v4.5: sigma.js v3 + graphology).

TOMBSTONE chapter. This SUPERSEDES test_nebula_progressive.py (the
cosmos.gl / @cosmos.gl/graph contract). cosmos.gl had a documented
closed ceiling -- it could not do stable layout + smooth camera +
node-highlight + full interaction at once; every attempt to wire a
highlight listener re-triggered the v2.6.8 freeze (reference_cosmos_
gl_nebula, gotcha 31 TOMBSTONE). v4.5 *replaces* the renderer with
sigma.js v3 + graphology -- the only sanctioned path past that
ceiling. These assertions are the contract's teeth:

  1. cosmos.gl is GONE (the import literal AND its API tokens) and
     does not silently creep back -- the TOMBSTONE.
  2. sigma.js v3 + graphology + the graphology standard library
     (forceatlas2) are vendored locally (no CDN runtime dep, no Node
     build) and loaded by the page.
  3. The reused shell is unchanged: the #cy-nebula mount, the
     .nebula-shell 3-panel grid, and the v4.4 C1.R responsive
     mPanel drawer state all survive (minimal blast radius).
  4. The carried-forward contract still holds: the side-panel body
     streams via mnemoRenderBody, the neighbors list via
     mnemoStaggeredReveal, the page still 200s, no cytoscape, no
     degree cap, no ego-fetch-on-tree-click.

We can't run WebGL in pytest, but we lock the SURFACE of the new
renderer + the parts of the old contract that still hold. The live
behaviour (render parity, highlight dims+spotlights, click/cite,
camera, drag, >=15s no-freeze) is verified per
feedback_reproduce_user_exact_scenario at an explicit viewport.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Store
from tests.conftest import FakeEmbedder

_UI = Path(__file__).resolve().parents[2] / "mnemo" / "ui"
VENDOR = _UI / "static" / "vendor"


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def graph_html() -> str:
    return (_UI / "templates" / "graph.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def base_html() -> str:
    return (_UI / "templates" / "base.html").read_text(encoding="utf-8")


# --- 1. TOMBSTONE: cosmos.gl is gone and stays gone --------------------

# The cosmos.gl API surface. The renderer swap removes ALL of it; a
# refactor must never reintroduce any token (each one was a documented
# trap in reference_cosmos_gl_nebula -- the converge-and-stop ceiling).
COSMOS_TOKENS = (
    "@cosmos.gl",  # the esm.sh import literal
    "__CosmographAPI",  # the readiness handle
    "cosmograph:ready",  # the readiness event
    "Cosmograph",  # the wrapper name
    "setConfig(",  # constructor-config no-op trap
    "setPointPositions(",  # typed-array position API
    "setPointColors(",
    "setPointSizes(",
    "setLinkColors(",  # the v2.6.6 render()-flush trap
    "getPointPositions(",
    "spaceToScreenPosition",  # the DOM-label-overlay projection
    "simulationDecay",  # the never-cool sim knob
    "simulationRepulsion",
    "pointGreyoutOpacity",  # the cosmos greyout config
)


def test_cosmos_gl_is_gone_tombstone(graph_html: str) -> None:
    """The TOMBSTONE. cosmos.gl's import AND its entire API surface
    must be absent from graph.html -- v4.5 replaces the renderer, it
    does not tune it (reference_cosmos_gl_nebula: never re-attempt)."""
    leaked = [t for t in COSMOS_TOKENS if t in graph_html]
    assert not leaked, (
        f"cosmos.gl tokens must be fully removed (TOMBSTONE -- "
        f"reference_cosmos_gl_nebula); found lingering: {leaked}"
    )


def test_base_html_no_cosmos_modulepreload(base_html: str) -> None:
    """base.html prewarmed the cosmos esm.sh bundle on every page.
    That modulepreload must be gone (the renderer is vendored now)."""
    assert "@cosmos.gl" not in base_html, (
        "base.html must not modulepreload the cosmos.gl CDN bundle "
        "anymore -- the renderer is vendored locally in v4.5."
    )


# --- 2. sigma.js v3 + graphology vendored + loaded --------------------

VENDOR_FILES = (
    "sigma.min.js",
    "graphology.umd.min.js",
    "graphology-library.min.js",
)


def test_renderer_bundles_are_vendored_locally() -> None:
    """sigma + graphology + the graphology standard library (it bundles
    forceatlas2) are vendored under static/vendor/ -- no CDN runtime
    dependency, no Node build (the stack rule)."""
    for name in VENDOR_FILES:
        f = VENDOR / name
        assert f.is_file(), f"vendored renderer bundle missing: static/vendor/{name}"
        # real minified bundles, not stubs/placeholders.
        assert f.stat().st_size > 50_000, (
            f"static/vendor/{name} looks truncated ({f.stat().st_size} B) "
            f"-- expected the full pinned bundle."
        )


def test_graph_loads_vendored_sigma_and_graphology(graph_html: str) -> None:
    """The page loads the vendored bundles (not a CDN, not cosmos)."""
    for name in VENDOR_FILES:
        assert f"/static/vendor/{name}" in graph_html, (
            f"graph.html must load the vendored /static/vendor/{name}."
        )


def test_graph_instantiates_sigma_on_a_graphology_graph(graph_html: str) -> None:
    """sigma.js v3 renders a graphology Graph: the page must construct
    a graphology graph and a Sigma renderer on the #cy-nebula mount."""
    assert "new Sigma(" in graph_html, (
        "graph.html must instantiate `new Sigma(graph, container, ...)` -- the v4.5 renderer."
    )
    assert "graphology" in graph_html, (
        "graph.html must build a graphology Graph (sigma's data model)."
    )


def test_graph_has_reducer_based_highlight_scaffold(graph_html: str) -> None:
    """Highlight is a sigma reducer (the exact capability cosmos
    lacked): the renderer config must declare nodeReducer + edgeReducer
    so a highlight is a pure data change, no renderer-state trap."""
    assert "nodeReducer" in graph_html, (
        "sigma config must set a nodeReducer (per-node display recompute "
        "-- the real-highlight foundation that closes the gotcha-31 loop)."
    )
    assert "edgeReducer" in graph_html, (
        "sigma config must set an edgeReducer (dim edges outside the "
        "highlighted/selected set, same 'grey don't hide' intent)."
    )


def test_graph_uses_forceatlas2_from_the_standard_library(graph_html: str) -> None:
    """Layout-on-cache-miss runs graphology's forceatlas2 (bundled in
    graphology-library) for a BOUNDED iteration count then freezes --
    no perpetual sim (the cosmos converge-and-stop lesson, kept)."""
    assert "layoutForceAtlas2" in graph_html, (
        "graph.html must use graphologyLibrary.layoutForceAtlas2 for the "
        "cache-miss layout (bounded iterations, then freeze)."
    )


# --- 3. The reused shell is unchanged (minimal blast radius) ----------


def test_shell_and_mount_unchanged(graph_html: str) -> None:
    """The renderer swap is a layer swap: the #cy-nebula mount + the
    .nebula-shell 3-panel grid are reused verbatim (the design's
    'minimal blast radius')."""
    assert 'id="cy-nebula"' in graph_html, (
        "the #cy-nebula mount must survive (sigma renders into it)."
    )
    assert "nebula-shell" in graph_html, ".nebula-shell 3-panel grid must survive."
    assert 'x-data="nebula()"' in graph_html, (
        "the Alpine nebula() factory must remain the page component "
        "(feedback_mnemo_alpine_gotchas: named factory)."
    )


def test_v44_responsive_mpanel_survives(graph_html: str) -> None:
    """The v4.4 C1.R responsive drawer (the < --bp-md mobile panel
    toggle) must NOT regress through the renderer swap."""
    for tok in ("mpanel-bar", "mpanel-toggle", "toggleMPanel(", "mPanel"):
        assert tok in graph_html, (
            f"v4.4 responsive shell token {tok!r} must survive the swap "
            f"(no regression of the shipped C1.R drawer)."
        )


# --- 4. Carried-forward contract (still valid post-swap) --------------


def test_dom_label_overlay_is_removed(graph_html: str) -> None:
    """sigma renders labels natively; the cosmos-era DOM label overlay
    (#nebula-labels + the per-frame spaceToScreenPosition pump) is
    deleted -- a net simplification, not a feature loss."""
    assert "nebula-labels" not in graph_html, (
        "the #nebula-labels DOM overlay must be removed (sigma renders "
        "labels natively -- the overlay is obsolete)."
    )
    assert "_scheduleLabels" not in graph_html, (
        "the DOM-label rAF pump (_scheduleLabels) must be removed."
    )


def test_side_panel_body_streams_via_mnemo_render_body(graph_html: str) -> None:
    assert "mnemoRenderBody" in graph_html, (
        "the detail-panel body must still delegate to "
        "window.mnemoRenderBody (type-aware streaming -- carried fwd)."
    )


def test_neighbors_use_staggered_reveal(graph_html: str) -> None:
    assert "mnemoStaggeredReveal" in graph_html, (
        "the neighbors list must still reveal via mnemoStaggeredReveal."
    )


def test_no_cytoscape_no_degree_cap(graph_html: str) -> None:
    usage = ("unpkg.com/cytoscape", "cytoscape-fcose", "cytoscape({", "this.cy =")
    hits = [m for m in usage if m in graph_html]
    assert not hits, f"no cytoscape may return: {hits}"
    assert "GRAPH_CANVAS_CAP" not in graph_html, (
        "the full graph is always rendered (no degree cap -- the user "
        "rejects any on-canvas node reduction; reference_cosmos_gl_nebula)."
    )


def test_no_ego_fetch_on_tree_click(graph_html: str) -> None:
    """Clicking a tree/neighbour node selects within the full graph
    (camera fly) -- it must never ego-fetch + collapse the tree."""
    assert "this.contextNodeId = id" not in graph_html
    assert "selectNode" in graph_html, (
        "focusNode must resolve to selectNode (select within the full graph), not an ego reload."
    )


def test_graph_page_still_renders(client: TestClient) -> None:
    r = client.get("/graph")
    assert r.status_code == 200, f"GET /graph should 200; got {r.status_code}"
    body = r.text
    assert "nebula-shell" in body
    assert "cy-nebula" in body
