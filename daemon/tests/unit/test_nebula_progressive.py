"""Phase 4 of v2.2: lazy Nebula initial paint + coordinated transitions.

We can't execute Cytoscape in pytest -- those behaviors live behind
the preview tool / live browser. But we CAN lock the SURFACE that
makes the streaming + staggering possible:

  - graph.html ships a ``_renderCanvasChunked`` method that walks
    nodes by descending degree in CHUNK_SIZE batches;
  - it tags each batch with the ``.fade-in`` class so the per-chunk
    opacity transition fires (the class lives in app.css since
    phase 1);
  - the ``focusNode`` orchestrator cross-fades the detail panel,
    pans the camera, AND streams the body via
    ``window.mnemoStreamText`` from phase 1;
  - the neighbors list is rendered via ``window.mnemoStaggeredReveal``
    so items appear paced rather than popping in all at once.

Design: docs/plans/2026-05-14-ux-progressive-design.md § 4.
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


# --- Chunked initial paint ----------------------------------------------


def test_graph_html_defines_render_canvas_chunked(graph_html: str) -> None:
    """The Alpine factory must expose ``_renderCanvasChunked``."""
    assert "_renderCanvasChunked" in graph_html, (
        "graph.html must define _renderCanvasChunked -- the chunked "
        "initial-paint helper. See design § 4."
    )


def test_chunked_paint_sorts_by_degree_desc(graph_html: str) -> None:
    """The chunked paint sorts nodes by degree DESC so the densest
    cluster paints first."""
    # We just look for a sort that references a degree-ish key.
    # Accepts either ``degree()`` (cytoscape collection method) or
    # the pre-computed ``deg`` data attribute the renderCanvas
    # already builds.
    pattern = re.compile(
        r"sort\s*\(\s*\([^)]*\)\s*=>\s*.+?\.(?:degree\s*\(\s*\)|data\(\s*['\"]deg['\"]\s*\)|deg)",
        re.DOTALL,
    )
    assert pattern.search(graph_html), (
        "chunked paint must sort by node degree descending so the highest-degree nodes appear first"
    )


def test_chunked_paint_adds_fade_in_class(graph_html: str) -> None:
    """Each chunk gets the ``.fade-in`` class so the per-chunk opacity
    transition fires. ``.fade-in`` lives in app.css since phase 1."""
    assert "fade-in" in graph_html, (
        "graph.html must tag each chunk with the .fade-in class so "
        "new nodes fade in (class defined in app.css from phase 1)"
    )


def test_chunked_paint_has_chunk_size_constant(graph_html: str) -> None:
    """A named chunk-size constant (50 by default per design) makes
    the cadence obvious and tunable."""
    # We accept either a plain ``CHUNK`` / ``CHUNK_SIZE`` const or an
    # inline literal 50 in the chunked-paint block. The literal is
    # the cheapest pattern.
    has_const = re.search(r"const\s+CHUNK(?:_SIZE)?\s*=\s*\d+", graph_html)
    has_literal_in_chunked = "_renderCanvasChunked" in graph_html and re.search(
        r"_renderCanvasChunked[\s\S]{0,2000}?\b(?:50|chunkSize)\b", graph_html
    )
    assert has_const or has_literal_in_chunked, (
        "chunked paint must reference a chunk-size constant (50 per design); "
        "either a const declaration or a literal in the function body"
    )


# --- Node-to-node coordinated transition ---------------------------------


def test_focus_node_streams_body_via_mnemo_stream_text(graph_html: str) -> None:
    """When the body fetch resolves, the body is revealed progressively.

    v2.2.0-v2.2.6 routed the side-panel body through
    ``window.mnemoStreamText`` directly (via the helper
    ``streamBodyToCode``). v2.2.7 retired that helper and delegated
    to ``window.mnemoRenderBody`` instead -- which itself routes
    through mnemoStreamText for every branch AND adds type-aware
    rendering (markdown bodies render as HTML, not as monospace
    source).

    The INTENT this test guards is "the side-panel body reveals
    progressively via the v2.2 streaming primitives" -- so it
    accepts either surface.
    """
    streaming_surfaces = ("mnemoStreamText", "mnemoRenderBody")
    assert any(s in graph_html for s in streaming_surfaces), (
        "graph.html must call window.mnemoStreamText or "
        "window.mnemoRenderBody (which itself routes through "
        "mnemoStreamText) so the detail-panel body reveals "
        "progressively. See design § 4 + § 5 and the v2.2.7 "
        "regression fix (Nebula side panel now uses mnemoRenderBody "
        "for type-aware markdown / code / commit rendering)."
    )


def test_focus_node_uses_staggered_reveal_for_neighbors(
    graph_html: str,
) -> None:
    """The neighbors list is rendered via ``mnemoStaggeredReveal`` so
    items appear paced instead of popping in together."""
    assert "mnemoStaggeredReveal" in graph_html, (
        "graph.html must call window.mnemoStaggeredReveal for the neighbors "
        "list so items reveal paced (phase 1 primitive). See design § 4."
    )


def test_focus_node_pans_camera_with_animate(graph_html: str) -> None:
    """The orchestrator must call ``cy.animate({ center, zoom })`` so the
    camera pans rather than snapping when a different node is focused."""
    # The pattern from the existing focusNode is preserved -- we just
    # check it's still there. Refactor cleanups must not lose it.
    pattern = re.compile(r"cy\.animate\s*\(\s*\{\s*center\s*:[^}]+\}", re.DOTALL)
    assert pattern.search(graph_html), (
        "focusNode must call cy.animate({ center: ..., zoom: ... }) so "
        "the camera pans on neighbor click. See design § 4."
    )


# --- Live page renders -- regression smoke ------------------------------


def test_graph_page_still_renders(client: TestClient) -> None:
    """Phase 4 is feature-additive; the page must still return 200."""
    r = client.get("/graph")
    assert r.status_code == 200, (
        f"GET /graph should still 200 after phase 4 refactor; got {r.status_code}"
    )
    body = r.text
    # The shell + canvas markers must still be present so the rest
    # of the Nebula UI isn't accidentally torn out.
    assert "nebula-shell" in body
    assert "cy-nebula" in body
