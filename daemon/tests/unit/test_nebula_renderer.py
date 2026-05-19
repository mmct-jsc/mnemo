"""Nebula renderer contract (v4.6: custom WebGL engine, nebula-gl.js).

TOMBSTONE chapter (evolved through v4.5 -> v4.6). The history:

  * cosmos.gl had a documented closed ceiling -- it could not do
    stable layout + smooth camera + node-highlight + full interaction
    at once; every attempt to wire a highlight listener re-triggered
    the v2.6.8 freeze (reference_cosmos_gl_nebula, gotcha 31).
  * v4.5 replaced it with a third-party 2D graph renderer + a
    graph-library data model + a CSS "atmosphere". On live review
    that render was flat/laggy; the transparent-canvas + CSS
    atmosphere coupling was the documented failure source.
  * v4.6 replaces THAT stack with a purpose-built single-file WebGL
    renderer (nebula-gl.js + vendored regl): crisp SDF star points +
    low-alpha density edges + a true opaque dark gl.clearColor +
    render-only-when-dirty. Highlight / select / hover are pure
    handle methods, so "highlight is a pure data change" holds
    structurally (no per-element framework callback hot path).

These assertions are the contract's teeth:

  1. cosmos.gl is GONE (import literal AND API tokens) and the whole
     v4.5 third-party renderer stack (the names of those libraries)
     is GONE too -- neither silently creeps back (the TOMBSTONE).
  2. The page renders via the vendored regl + nebula-gl.js (no CDN
     runtime dep, no Node build) and calls NebulaGL.create.
  3. The reused shell is unchanged: the #cy-nebula legacy node, the
     .nebula-shell 3-panel grid, and the v4.4 C1.R responsive
     mPanel drawer state all survive (minimal blast radius).
  4. The carried-forward contract still holds: the side-panel body
     streams via mnemoRenderBody, the neighbors list via
     mnemoStaggeredReveal, the page still 200s, no cytoscape, no
     degree cap, no ego-fetch-on-tree-click, the layout is computed
     SERVER-SIDE and the client is a pure renderer.
  5. There is no CSS "atmosphere" anymore (the v4.5.x failure
     source) -- .nebula-canvas is a plain opaque dark container.

We can't run WebGL in pytest, but we lock the SURFACE of the new
renderer + the parts of the old contract that still hold. The live
behaviour (render quality, highlight, click/cite, camera, drag, the
community-separation de-blob gate) is verified per
feedback_reproduce_user_exact_scenario + test_graph_layout_server.py.
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


# --- 1. TOMBSTONE: cosmos.gl is gone and stays gone -------------------

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
    must be absent from graph.html -- every renderer chapter replaces,
    it never re-tunes (reference_cosmos_gl_nebula: never re-attempt)."""
    leaked = [t for t in COSMOS_TOKENS if t in graph_html]
    assert not leaked, (
        f"cosmos.gl tokens must be fully removed (TOMBSTONE -- "
        f"reference_cosmos_gl_nebula); found lingering: {leaked}"
    )


def test_base_html_prewarms_only_the_v46_renderer(base_html: str) -> None:
    """base.html prewarms the renderer site-wide. It must NOT preload/
    prefetch cosmos.gl NOR the v4.5 stack (those vendor files are
    deleted -> dead 404 prefetches that also leak the forbidden
    literals into every served page -- caught live in P4). It must
    prefetch the v4.6 vendored bundles instead."""
    assert "@cosmos.gl" not in base_html, "no cosmos.gl modulepreload"
    low = base_html.lower()
    assert "sigma" not in low, (
        "base.html must not reference the v4.5 renderer (dead prefetch "
        "+ leaks the literal into every served page)"
    )
    assert "graphology" not in low, "base.html must not reference graphology"
    assert "/static/vendor/regl.min.js" in base_html, "base.html must prefetch the v4.6 regl bundle"
    assert "/static/vendor/nebula-gl.js" in base_html, (
        "base.html must prefetch the v4.6 nebula-gl renderer"
    )


def test_cosmos_and_sigma_and_graphology_all_gone(graph_html: str) -> None:
    """v4.6 removes the v4.5 third-party renderer stack outright (the
    deferred TOMBSTONE chapter). Neither cosmos.gl NOR the v4.5
    library names may appear anywhere in graph.html -- not even in a
    comment (the recurring grep-guard-vs-prose failure: an explanatory
    comment must never contain a literal a guard forbids)."""
    for t in COSMOS_TOKENS:
        assert t not in graph_html
    for t in ("sigma", "graphology", "Sigma(", "graphology-library"):
        assert t not in graph_html, (
            f"v4.6 removed the v4.5 renderer stack; this token must be "
            f"absent from graph.html (incl. comments): {t!r}"
        )


# --- 2. The v4.6 custom renderer is vendored + loaded ----------------

# v4.6: regl (the WebGL micro-framework) + our authored nebula-gl.js.
# The v4.5 bundles were removed with the renderer swap.
VENDOR_FILES = (
    "regl.min.js",
    "nebula-gl.js",
)


def test_renderer_bundles_are_vendored_locally() -> None:
    """Only regl + nebula-gl.js are vendored (the v4.5 bundles were
    removed with the swap) -- no CDN runtime dep, no Node build."""
    f = VENDOR / "regl.min.js"
    assert f.is_file(), "vendored regl.min.js missing"
    assert f.stat().st_size > 50_000, (
        f"static/vendor/regl.min.js looks truncated "
        f"({f.stat().st_size} B) -- expected the full pinned bundle."
    )
    assert (VENDOR / "nebula-gl.js").is_file(), (
        "the authored renderer static/vendor/nebula-gl.js must exist."
    )
    assert not (VENDOR / "sigma.min.js").exists(), (
        "the v4.5 2D renderer bundle must be removed (v4.6 swap)."
    )
    assert not (VENDOR / "graphology.umd.min.js").exists(), (
        "the v4.5 graph-library data-model bundle must be removed."
    )
    assert not (VENDOR / "graphology-library.min.js").exists(), (
        "the v4.5 layout-library bundle must be removed."
    )


def test_renders_via_vendored_nebula_gl(graph_html: str) -> None:
    """The page loads the vendored regl + nebula-gl.js and constructs
    the renderer on the GL canvas via NebulaGL.create()."""
    assert "/static/vendor/regl.min.js" in graph_html, (
        "graph.html must load the vendored /static/vendor/regl.min.js."
    )
    assert "/static/vendor/nebula-gl.js" in graph_html, (
        "graph.html must load the vendored /static/vendor/nebula-gl.js."
    )
    assert "NebulaGL.create(" in graph_html, (
        "graph.html must instantiate the renderer via "
        "window.NebulaGL.create(canvas, {nodes, edges, theme, labels})."
    )
    # the vendor scripts MUST be version-cache-busted like every other
    # client asset (app.css/app.js/chat.js use ?v={{ mnemo_version }}).
    # A bare URL means an upgrading user keeps a stale cached renderer
    # and never receives a fix -- the documented gotcha-29 class.
    assert "/static/vendor/regl.min.js?v=" in graph_html, (
        "regl must be loaded version-busted (?v=...) so a release "
        "actually delivers the new bundle (no stale cache)."
    )
    assert "/static/vendor/nebula-gl.js?v=" in graph_html, (
        "nebula-gl.js must be loaded version-busted (?v=...)."
    )


# --- 3. The reused shell is unchanged (minimal blast radius) ---------


def test_shell_and_mount_unchanged(graph_html: str) -> None:
    """The renderer swap is a layer swap: the #cy-nebula node + the
    .nebula-shell 3-panel grid are reused (the design's 'minimal
    blast radius'). #cy-nebula is now an inert hidden legacy node --
    kept so any external reference can't NPE; the renderer mounts on
    the #nebula-gl canvas."""
    assert 'id="cy-nebula"' in graph_html, (
        "the #cy-nebula node must survive (legacy-reference safety)."
    )
    assert 'id="nebula-gl"' in graph_html, (
        "the #nebula-gl WebGL canvas (the renderer's surface) must exist."
    )
    assert 'id="nebula-labels"' in graph_html, (
        "the #nebula-labels overlay canvas (LabelProvider target) must exist."
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


# --- 4. Carried-forward contract (still valid post-swap) -------------


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


# --- ARCHITECTURE PIVOT: layout is computed SERVER-SIDE --------------
#
# The layout is computed ONCE per (scope, fingerprint) on the daemon,
# deterministic + fully converged, cached; the browser is a PURE
# renderer. v4.6 swaps only the ALGORITHM (FA2/LinLog/Barnes-Hut) +
# the renderer; the server-side + cached pipeline is unchanged. These
# guards lock that contract (the layout quality itself -- the
# community-separation de-blob gate -- is unit-tested in
# test_graph_layout_server.py; the visual is verified live per
# feedback_reproduce_user_exact_scenario).

_ROUTES = (_UI / "routes.py").read_text(encoding="utf-8")
_APP_CSS = (_UI / "static" / "app.css").read_text(encoding="utf-8")


def test_client_does_no_layout_compute(graph_html: str) -> None:
    """The browser must NOT compute layout: no client force layout
    (sync OR a worker), no circlepack, no component detection, no
    PUT-back. That whole class was proven fragile. v4.6 keeps the
    client a pure renderer of plain arrays -- no graph-library data
    model at all."""
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
        f"path); found: {leaked}. Layout is server-side."
    )
    # v4.6 contract evolution: there is no client graph-library data
    # model anymore -- the renderer takes plain {nodes, edges} arrays.
    assert "this._edges" in graph_html, (
        "the client must build a plain render edge list (this._edges) "
        "-- v4.6 has no graph-library data model."
    )
    assert "PUT" not in graph_html or "graph-layout" not in graph_html.split("PUT")[0][-200:], (
        "the client must not PUT layouts back -- the daemon owns the cache."
    )


def test_client_polls_the_server_layout_then_renders(graph_html: str) -> None:
    """The browser GETs /ui/graph-layout and polls while the daemon
    computes (``computing``), then applies the cached positions and
    hands the plain arrays to the renderer -- a pure renderer."""
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
    # v4.6 contract evolution: the renderer is NebulaGL, not the v4.5
    # 2D renderer -- the page renders via NebulaGL.create after the poll.
    assert "NebulaGL.create(" in graph_html, "NebulaGL is the v4.6 renderer."


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


# --- 5. v4.6 surface: no CSS atmosphere + highlight/select preserved -


def test_no_css_atmosphere() -> None:
    """v4.6 deletes the v4.5.x CSS "atmosphere" (drift / twinkle /
    parallax) AND the DOM-overlay pulse. The WebGL canvas is opaque
    and self-contained -- the transparent-canvas + CSS-atmosphere
    coupling was the documented failure source. .nebula-canvas is
    now a plain opaque dark (#07090f) container."""
    for k in (
        "@keyframes nebula-drift",
        "@keyframes nebula-twinkle",
        "@keyframes nebula-parallax",
        ".nebula-canvas::after",
        ".nebula-canvas::before",
        ".nebula-pulse-anchor",
    ):
        assert k not in _APP_CSS, f"v4.6 deleted the atmosphere/pulse: {k}"
    nc = _APP_CSS[_APP_CSS.index(".nebula-canvas {") :]
    assert "#07090f" in nc[: nc.index("}")], (
        ".nebula-canvas must be a plain opaque dark (#07090f) container "
        "-- the renderer clears its own GL canvas, no CSS atmosphere."
    )


def test_labels_toggle_has_a_default_set_when_nothing_selected(
    graph_html: str,
) -> None:
    """labels.setLabels() was only ever called on selection / highlight
    / search, so the LabelProvider (which early-returns when items is
    empty) had nothing to draw at idle and the global 'labels' toggle
    did nothing -- the reported 'the label toggle is not working'.

    Fix: a bounded default global label set (top nodes by degree)
    shown whenever labels are on and nothing is selected/highlighted,
    refreshed on render, toggle and deselect via one _refreshLabels()
    state router."""
    assert "_defaultLabelItems" in graph_html, (
        "there must be a bounded default (top-by-degree) global label "
        "set so the labels toggle is meaningful with no selection."
    )
    assert "_refreshLabels" in graph_html, (
        "a single _refreshLabels() must route labels by current state "
        "(off -> clear, selection -> focused set, else -> default)."
    )
    assert ".deg || 0" in graph_html, (
        "the default label set must rank by node degree (the most "
        "connected nodes orient the overview)."
    )
    # the toggle must NOT be a no-op when nothing is selected.
    assert "this.labelsVisible && this._selId && this.labels" not in graph_html, (
        "toggleLabels() must not require an active selection to show "
        "any labels (that is exactly why the toggle looked dead)."
    )
    # deselect returns to the default set, not a blank overlay.
    # (anchor on the JS method def "deselect() {" -- bare "deselect()"
    # also appears as the @click close-button markup.)
    ds = graph_html[graph_html.index("deselect() {") :][:420]
    assert "_refreshLabels" in ds, (
        "deselect() must restore the default label set (when labels "
        "are on), not bare labels.clear()."
    )


def test_default_labels_cover_all_nodes_frame_budgeted(
    graph_html: str,
) -> None:
    """The default label set was capped at 64, so the labels toggle
    never showed all node names. The cap must be gone -- the default
    set is EVERY node (degree-sorted so the most-connected win z-order
    / the per-frame budget) -- and the LabelProvider must bound how
    many pills it actually draws per frame (LABEL_BUDGET) so an 11k
    set stays smooth (the cardinal no-jank rule) while zooming into
    any region still labels all nodes there."""
    assert "Math.min(64" not in graph_html, (
        "the default label set must NOT be capped (the toggle has to "
        "be able to show every node's name)."
    )
    assert "_defaultLabelItems" in graph_html
    nebula = (VENDOR / "nebula-gl.js").read_text(encoding="utf-8")
    assert "LABEL_BUDGET" in nebula, (
        "the LabelProvider must cap pills DRAWN per frame "
        "(LABEL_BUDGET) so an uncapped (all-nodes) label set cannot "
        "rejank the perpetual loop."
    )


def test_highlight_select_contract_preserved(graph_html: str) -> None:
    """The companion-driven highlight loop (the gotcha-31 / C3-honesty
    arc) survives the swap: the document listeners still exist and now
    drive the renderer handle (gl.setHighlight / gl.select) -- the
    'highlight is a pure data change' property, structurally."""
    for t in (
        "mnemo-highlight-nodes",
        "mnemo-select-node",
        "setHighlight",
        ".select(",
        "_onHighlight",
        "_onSelectNode",
    ):
        assert t in graph_html, (
            f"the highlight/select contract token {t!r} must be present "
            f"(the C3-honesty loop must survive the renderer swap)."
        )


def test_labels_default_off(graph_html: str) -> None:
    """v4.6.4: the label overlay is auto-off by default. graph.html
    defaults labelsVisible to false (a prior explicit choice is still
    restored from localStorage in _restoreState)."""
    assert "labelsVisible: false" in graph_html, (
        "labels must default OFF (auto-off the label display)"
    )
    assert "labelsVisible: true" not in graph_html, "no stray labelsVisible:true default may remain"
