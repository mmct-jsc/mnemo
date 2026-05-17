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

# v4.5 pivot: graphology-library (the FA2/circlepack bundle) is GONE
# -- the layout is server-side now, so the client only needs sigma +
# graphology (sigma's Graph data model). No CDN, no Node build.
VENDOR_FILES = (
    "sigma.min.js",
    "graphology.umd.min.js",
)


def test_renderer_bundles_are_vendored_locally() -> None:
    """Only sigma + graphology are vendored (graphology-library was
    removed with the client-FA2 pivot) -- no CDN runtime dep, no Node
    build (the stack rule)."""
    for name in VENDOR_FILES:
        f = VENDOR / name
        assert f.is_file(), f"vendored renderer bundle missing: static/vendor/{name}"
        assert f.stat().st_size > 50_000, (
            f"static/vendor/{name} looks truncated ({f.stat().st_size} B) "
            f"-- expected the full pinned bundle."
        )
    assert not (VENDOR / "graphology-library.min.js").exists(), (
        "graphology-library.min.js must be removed -- the client does no "
        "layout in v4.5 (server-side), so the FA2/circlepack bundle is dead."
    )


def test_graph_loads_vendored_sigma_and_graphology(graph_html: str) -> None:
    """The page loads only the vendored sigma + graphology (not a CDN,
    not cosmos, and NOT graphology-library -- layout is server-side)."""
    for name in VENDOR_FILES:
        assert f"/static/vendor/{name}" in graph_html, (
            f"graph.html must load the vendored /static/vendor/{name}."
        )
    assert "graphology-library" not in graph_html, (
        "graph.html must NOT load graphology-library (client does no layout)."
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


# (v4.5 pivot: the old "client runs graphology forceatlas2" guard was
# REMOVED -- layout is computed server-side now. The server contract
# is locked by test_server_computes_and_caches_the_layout +
# test_graph_layout_server.py; the client-does-no-layout guard below.)


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


# --- v4.5 ARCHITECTURE PIVOT: layout is computed SERVER-SIDE ----------
#
# The first sigma cuts computed the layout in the browser (sync FA2,
# then a Web Worker, with a circlepack seed). Across 3 measured
# attempts on the real 11026-node / 2298-component scope that proved
# non-deterministic + quality-fragile (sync converged, the worker
# exploded the giant, circlepack alone = structureless confetti) --
# the documented "3 failed fixes => question the architecture" rule.
# With the user's approval the layout moved to the daemon: computed
# ONCE per (scope, fingerprint), deterministic + fully converged,
# cached; the browser is a PURE sigma renderer. These guards lock
# that contract (the layout quality itself is unit-tested in
# test_graph_layout_server.py; the visual is verified live per
# feedback_reproduce_user_exact_scenario).

_ROUTES = (_UI / "routes.py").read_text(encoding="utf-8")
_APP_CSS = (_UI / "static" / "app.css").read_text(encoding="utf-8")


def test_client_does_no_layout_compute(graph_html: str) -> None:
    """The browser must NOT compute layout: no client forceatlas2
    (sync OR the FA2Layout worker), no circlepack, no component
    detection, no PUT-back. That whole class was proven fragile."""
    forbidden = (
        "FA2Layout",
        "layoutForceAtlas2",
        "circlepack",
        "connectedComponents",
        "linLogMode",
        "_runForceLayout",
        "_persistLayout",
        "_killForceLayout",
        "_seedLayout",
        "graphologyLibrary",
        "graphology-library.min.js",
    )
    leaked = [t for t in forbidden if t in graph_html]
    assert not leaked, (
        f"graph.html must NOT do client-side layout (the proven-fragile "
        f"path); found: {leaked}. Layout is server-side in v4.5."
    )
    assert "graphology.umd.min.js" in graph_html, (
        "the graphology bundle is still needed (sigma's data model); "
        "only the LAYOUT moved server-side, not the renderer."
    )
    assert "PUT" not in graph_html or "graph-layout" not in graph_html.split("PUT")[0][-200:], (
        "the client must not PUT layouts back -- the daemon owns the cache."
    )


def test_client_polls_the_server_layout_then_renders(graph_html: str) -> None:
    """The browser GETs /ui/graph-layout and polls while the daemon
    computes (``computing``), then applies the cached positions and
    mounts sigma -- a pure renderer."""
    assert "_awaitServerLayout" in graph_html, (
        "graph.html must have _awaitServerLayout() -- the poll loop that "
        "waits for the daemon's cached layout (the pure-renderer flow)."
    )
    assert "_applyCachedPositions" in graph_html, (
        "graph.html must apply the server positions via _applyCachedPositions."
    )
    assert "/ui/graph-layout?scope_key=" in graph_html, (
        "the client must GET the server layout cache by scope+fingerprint."
    )
    assert "new Sigma(" in graph_html, "sigma is still the renderer."


def test_server_computes_and_caches_the_layout() -> None:
    """The daemon computes the layout (mnemo.ui.graph_layout) and wires
    it into the existing layout cache, kicked (non-blocking) by
    /ui/graph-data, with a ``computing`` status on GET /ui/graph-layout."""
    from mnemo.ui import graph_layout, routes

    assert callable(graph_layout.compute_graph_layout)
    assert "compute_graph_layout" in _ROUTES, "routes.py must use the server layout."
    assert "_ensure_layout_async" in _ROUTES, (
        "routes.py must kick a non-blocking background compute "
        "(_ensure_layout_async) keyed by (scope, fingerprint)."
    )
    assert hasattr(routes, "LAYOUT_VERSION"), "the cache stays algorithm-versioned."
    assert '"computing"' in _ROUTES, (
        "GET /ui/graph-layout must report a 'computing' status so the "
        "client polls instead of computing anything itself."
    )


def test_sigma_render_is_dark_themed(graph_html: str) -> None:
    """RC4/RC6/RC7: density perf kept (hideEdgesOnMove +
    labelRenderedSizeThreshold), labels drawn DARK (not sigma's white
    pill -- the reported "white label background"), and the canvas
    has an opaque dark backdrop (the reported "white background")."""
    assert "hideEdgesOnMove" in graph_html, (
        "sigma must keep hideEdgesOnMove (15k edges; the reported lag)."
    )
    assert "labelRenderedSizeThreshold" in graph_html, (
        "sigma must keep labelRenderedSizeThreshold (label declutter)."
    )
    assert "defaultDrawNodeLabel: nbDrawLabel" in graph_html, (
        "labels must use the DARK nbDrawLabel drawer -- sigma's default "
        "draws a WHITE pill (the reported 'white label background')."
    )
    assert "nbDrawHover" in graph_html, "hover label must also be dark-themed."
    nc = _APP_CSS.index(".nebula-canvas {")
    body = _APP_CSS[nc : _APP_CSS.index("}", nc)]
    assert "background:" in body, (
        ".nebula-canvas must carry an OPAQUE dark backdrop so sigma's "
        "transparent WebGL canvases show the C1 nebula theme, not white."
    )
    assert "#07090f" in body, (
        ".nebula-canvas backdrop must use the C1 dark base (#07090f) -- "
        "the reported 'white background, colors too bright'."
    )


# --- v4.5.2 LIVING NEBULA (user live-review of v4.5.1) ----------------
#
# v4.5.1 fixed the layout (deterministic, server-side) but the render
# was STATIC + flat: "only circle shape and straight edges", "no
# moving like its actually living", "drag make all edge disappear",
# "highlight node only node no edge". v4.5.2 makes it a LIVING nebula
# WITHOUT touching the (correct) server layout: the structure is the
# settled server position; the client adds bounded life + cosmic
# rendering, purely in the cheap reducer hot path (no graph mutation,
# no force sim -- the proven-fragile thing stays gone).


def test_nebula_is_alive_camera_float_plus_star_twinkle(graph_html: str) -> None:
    """The nebula must be ALIVE without a per-frame full-graph
    position mutation. PROVEN constraints: sigma's nodeReducer does
    NOT render an overridden x/y (verified live -- a +400 offset moved
    nothing), and mutating 11k graph positions every frame pegs the
    main thread (the "1fps" jank the user rejected). So the motion is
    the CHEAP, GUARANTEED, smooth-at-any-scale pair: (1) a gentle
    bounded CAMERA float (one GPU transform/frame) so the whole cosmos
    drifts; (2) a per-star SIZE twinkle in the reducer (size IS the
    one motion sigma's reducer honors -- no graph writes). NO
    per-frame graph mutation."""
    assert "cam.setState({" in graph_html, (
        "_startLife must FLOAT the camera (cheap GPU transform) -- the "
        "whole nebula gently drifts = alive at any node count."
    )
    assert "home.x + 0.018" in graph_html, (
        "the camera float must oscillate in a small BOUNDED range "
        "around the auto-fit rest pose ('moving only in a predefined "
        "area'), never a wander-off pan."
    )
    assert "Math.sin(t * 0.21)" in graph_html, (
        "the camera float is a slow Lissajous around the rest pose."
    )
    assert "res.size = base * (1 + 0.16 * Math.sin(rs.t" in graph_html, (
        "nbNodeReduce must apply a per-star SIZE twinkle (size is the "
        "ONLY motion sigma's reducer renders; the cheap no-graph-write "
        "path -- a reducer x/y override is ignored, verified)."
    )
    assert "'ph', (i * 2.39996323)" in graph_html, (
        "_applyCachedPositions must assign a STABLE golden-angle "
        "per-node phase so stars twinkle on their own beat (not in unison)."
    )
    assert "updateEachNodeAttributes" not in graph_html, (
        "there must be NO per-frame full-graph position mutation -- "
        "11k graph writes/frame pegged the main thread (the 1fps jank)."
    )


def test_nebula_has_an_raf_life_loop(graph_html: str) -> None:
    """A single rAF loop advances the clock + refreshes -- the breath.
    It is paused when the tab is hidden and cancelled on reload /
    destroy (token + _stopLife)."""
    assert "_startLife()" in graph_html, "graph.html must have _startLife() -- the breathing loop."
    assert "_stopLife()" in graph_html, (
        "graph.html must have _stopLife() -- cancels the breathing loop."
    )
    assert "requestAnimationFrame(tick)" in graph_html, (
        "the life loop must drive on requestAnimationFrame."
    )
    assert "document.hidden" in graph_html, (
        "the life loop must pause when the tab is hidden (battery)."
    )
    assert "this._stopLife();" in graph_html, (
        "reload (renderCanvas) + destroy must stop a prior life loop."
    )


def test_nebula_renders_stars_and_filaments_not_flat(graph_html: str) -> None:
    """Soft star POINTS + gently CURVED luminous filaments -- not flat
    circles + straight lines (the reported "only circle shape and
    straight edges"). Both programs are bundled in vendored sigma."""
    assert "NodePointProgram" in graph_html, (
        "nodes must render via NodePointProgram (soft stars), not the hard flat default circle."
    )
    assert "EdgeCurveProgram" in graph_html, (
        "edges must render via EdgeCurveProgram (curved filaments), not straight lines."
    )
    assert "curvature:" in graph_html, (
        "edges need a per-edge curvature so the filaments actually arc."
    )
    assert "hideEdgesOnMove: false" in graph_html, (
        "edges must STAY while dragging (the reported 'drag make all edge disappear')."
    )


def test_selected_node_ignites_its_filaments(graph_html: str) -> None:
    """Highlight must read on EDGES too: a selected/hovered node's
    incident filaments glow accent (the reported "highlight node only
    node no edge")."""
    assert "data._s === sel || data._t === sel" in graph_html, (
        "nbEdgeReduce must accent-glow edges incident to the selected "
        "node (ignite the constellation, not just the node)."
    )
    assert "rs.accent" in graph_html
