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

import re
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


def _method_body(src: str, name: str) -> str:
    """Return the brace-balanced body of an Alpine factory method
    ``name() {`` so a contract assertion can scope to just it."""
    m = re.search(rf"^    {re.escape(name)}\(\)\s*\{{", src, re.MULTILINE)
    assert m, f"{name}() method must exist in graph.html"
    depth, i = 0, m.end() - 1
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start() : i + 1]
        i += 1
    raise AssertionError(f"{name}() body did not parse (unbalanced braces)")


def test_toggle_edges_flushes_with_render(graph_html: str) -> None:
    """v2.6.6 regression lock. cosmos.gl's setLinkColors only SETS
    the isLinkColorUpdateNeeded dirty flag; the perpetual sim's
    per-frame frame() loop never consumes it (verified: flag stayed
    true for 2 s / hundreds of frames). Only render() flushes the
    link-colour buffer to the GPU. Without a render() call after
    setLinkColors the edges toggle did NOTHING until a full page
    refresh re-ran renderCanvas. toggleEdges MUST call render()
    after setLinkColors so the toggle is real-time."""
    body = _method_body(graph_html, "toggleEdges")
    assert "setLinkColors(" in body, (
        "toggleEdges must swap the link-colour buffer via setLinkColors "
        "(pure paint, no setConfig sim-restart)."
    )
    si = body.index("setLinkColors(")
    assert ".render(" in body[si:], (
        "toggleEdges must call cg.render() AFTER setLinkColors -- "
        "cosmos only flushes the link-colour buffer on render(); the "
        "per-frame sim loop ignores isLinkColorUpdateNeeded, so "
        "without it the toggle only takes effect on page refresh."
    )


def test_fitview_is_render_flushed_via_helper(graph_html: str) -> None:
    """v2.6.8 regression lock. Generalises the v2.6.6 lesson: cosmos
    camera ops (fitView) are a NO-OP when the sim/render loop is idle
    -- verified live, a bare fitView() left the cloud off-centre
    (centroid 670,223 vs viewport centre 436,365) until a render()
    flush snapped it (435,363). The cache-hit layout is displayed
    STATIC (no sim energy -> idle loop), so fitView MUST be render-
    flushed. The contract is centralised in a `_fitToView` helper
    (DRY); lock that the helper renders AND that no caller invokes a
    raw cosmos fitView() bypassing it."""
    fit_body = _method_body(graph_html, "_fitToView")
    flush_body = _method_body(graph_html, "_flush")
    assert "fitView(" in fit_body, "_fitToView must call cosmos fitView()"
    # The flush is either inline in _fitToView or via this._flush().
    assert "_flush(" in fit_body or ".render(" in fit_body, (
        "_fitToView must render-flush the fit (cosmos no-ops camera ops on an idle loop)."
    )
    assert ".render(" in flush_body, "_flush() must call cg.render()"
    # No raw cosmos fitView call may bypass the helper: every
    # `.fitView(` in real code is either the helper's own call or a
    # `.fitView)` capability guard -- never `this.cg.fitView(<n>)`
    # outside _fitToView.
    bad = re.findall(r"this\.cg\.fitView\(\d", graph_html)
    assert bad == [] or graph_html.count("this.cg.fitView(") <= 1, (
        "all fits must go through _fitToView (render-flushed); found a "
        f"raw this.cg.fitView(...) bypass: {bad}"
    )


def _method_body_any(src: str, sig: str) -> str:
    """Like _method_body but for an arbitrary method signature
    (e.g. selectByIndex(index))."""
    m = re.search(rf"^    {re.escape(sig)}\s*\{{", src, re.MULTILINE)
    assert m, f"{sig} must exist in graph.html"
    depth, i = 0, m.end() - 1
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start() : i + 1]
        i += 1
    raise AssertionError(f"{sig} body unbalanced")


def test_selection_camera_flyto_is_self_terminating_and_flushed(graph_html: str) -> None:
    """v2.6.8: the camera fly-to is a SELF-TERMINATING ~650 ms ease
    in `_flyTo` (NOT a perpetual pump -- that hung the tab, and NOT
    cosmos's own animated zoom -- that no-ops on the cooled loop).
    selectByIndex must delegate to _flyTo; _flyTo must drive its own
    rAF + render() per frame AND end the rAF chain when the ease
    completes (p>=1 -> _flyRAF = null) so it can never hang."""
    sel = _method_body_any(graph_html, "selectByIndex(index)")
    assert "_flyTo(" in sel, (
        "selectByIndex must call this._flyTo(index) for the camera "
        "fly-to (loop-independent, self-terminating)."
    )
    fly = _method_body_any(graph_html, "_flyTo(index)")
    assert "zoomToPointByIndex(" in fly, "_flyTo must recentre via zoomToPointByIndex"
    assert ".render(" in fly, (
        "_flyTo must render() each frame -- cosmos camera ops no-op "
        "without a flush on the (cooled) static-layout loop."
    )
    assert "requestAnimationFrame(" in fly, "_flyTo must drive its own rAF"
    # Self-terminating: there must be a branch that does NOT
    # re-schedule (sets _flyRAF = null) so the burst ends. The
    # perpetual pump that hung had no such terminal branch.
    assert "_flyRAF = null" in fly, (
        "_flyTo must END the rAF chain at p>=1 (_flyRAF = null) so "
        "the fly-to is a finite burst and can never hang the tab."
    )


def test_no_perpetual_render_pump(graph_html: str) -> None:
    """The v2.6.8 perpetual render pump (cg.render() every frame
    forever) HUNG the tab on 10 k nodes -- it must stay removed.
    Only finite, self-terminating animations are allowed."""
    msg = (
        "a perpetual external render pump saturates the main thread "
        "on a 10 k-node scene and hangs the tab -- removed in v2.6.8, "
        "must not return. Use finite self-terminating tweens (_flyTo)."
    )
    assert "_startRenderPump" not in graph_html, msg
    assert "_pumpRAF" not in graph_html, msg


def test_cache_hit_layout_is_not_re_energized(graph_html: str) -> None:
    """v2.6.8 root-cause lock. The cached layout is a converged,
    good layout -- it must be displayed AS-IS. The v2.6.4 cache-hit
    ``start(0.35)`` re-energised it off-equilibrium, causing ~8 s of
    violent dense-cluster collision + a centroid drift toward
    space-centre before the sim froze (measured: dense core moved
    5-18 px/1.6 s for 6 s, then 0 forever). Displaying the cache
    statically (no energy injection) removes both the chaos and the
    drift. We assert on the CALL form (a receiver dot) so the
    history comments that mention the old behaviour as prose don't
    trip it -- same approach as test_graph_has_no_cytoscape."""
    # Real re-energise calls had a receiver: `this.cg.start(0.35..)`
    # / `cg.start(useCache ? ...)`. Comments write "start(0.35)"
    # without the dot.
    bad_calls = re.findall(r"\bcg\.start\(\s*(?:0\.\d|useCache)", graph_html)
    assert bad_calls == [], (
        "cache-hit must NOT re-energise with a fractional-alpha / "
        f"useCache-conditional cg.start(). Found: {bad_calls}"
    )
    assert "cg.start(1)" in graph_html, (
        "the loop must be kept alive with cg.start(1) so cosmos's "
        "render pipeline runs (fitView / camera ops no-op on a loop "
        "that never started -- verified live)."
    )
    # v2.6.8 KEY: start(1) on cache-hit is only safe because EVERY
    # point is pinned -> the sim runs (loop alive for fitView) but
    # nothing moves (zero chaos, zero drift). Lock that pin-all is
    # wired -- without it start(1) would re-relax the cached layout.
    assert "setPinnedPoints(" in graph_html, (
        "cache-hit must pin ALL points (setPinnedPoints) so the live "
        "render loop (needed for fitView) cannot move the converged "
        "cached layout -- the pin is what makes start(1) chaos-free."
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
