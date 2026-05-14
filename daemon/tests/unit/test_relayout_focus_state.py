"""v2.2.6 bug fix: layout change preserves .hl / .dim focus state.

Reported symptom (2026-05-14): clicking a node selects it (which adds
.hl to the neighborhood + .dim to everything else), THEN clicking a
layout button (rings / tree / grid / force) silently kills the
blur-others effect. The selected node still shows its DOM-overlay
pulse, but the contrast against the dimmed background vanishes and
the focus reads as "gone".

Root cause: the prior ``relayout()`` did
``cy.elements().animate({ style: { opacity: 0.3 } })`` then
``cy.elements().animate({ style: { opacity: 1 } })`` to cross-fade
the position snap. In cytoscape, ``animate({ style: ... })`` writes
an INLINE BYPASS style that persists after the animation completes.
After fadeIn, every node has inline ``opacity: 1`` -- which overrides
the ``.dim`` stylesheet rule ``opacity: 0.12``. The blur-others
effect dies; the highlighted halos lose their contrast.

Fix (pipeline 18 -- DOM overlay over canvas-library effects): move
the cross-fade to a DOM ``<div class="nebula-layout-veil">``
positioned above the canvas. The veil fades opaque -> snap layout ->
fades transparent, all via CSS transitions. Cytoscape elements are
never opacity-touched, so ``.hl`` and ``.dim`` stylesheet rules own
opacity throughout. Same pattern as the v2.2.4 pulse rewrite.

These tests lock the surface so a future refactor cannot
reintroduce the inline-bypass pattern.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

GRAPH_HTML = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates" / "graph.html"
APP_CSS = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static" / "app.css"


@pytest.fixture(scope="module")
def graph_html_src() -> str:
    return GRAPH_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_css_src() -> str:
    return APP_CSS.read_text(encoding="utf-8")


def _relayout_body(src: str) -> str:
    """Return the JS source of the relayout(name) method.

    The method starts at ``    relayout(name) {`` and ends at the
    matching close-brace + comma line. We extract by counting braces
    so the regex doesn't have to be precise about line endings.
    """
    start_pattern = re.search(r"^    relayout\(name\)\s*\{", src, re.MULTILINE)
    assert start_pattern, "relayout(name) method must exist in graph.html"
    start = start_pattern.start()
    depth = 0
    i = start_pattern.end() - 1  # position of '{'
    end = -1
    while i < len(src):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
        i += 1
    assert end > 0, "relayout(name) method body did not parse cleanly (unbalanced braces)"
    return src[start:end]


# --- the bug: no inline opacity bypass on cytoscape elements ---------


def _strip_js_comments(src: str) -> str:
    """Remove ``// ...`` line comments and ``/* ... */`` block comments
    so the bypass-detection regex doesn't trip on historical-context
    notes that describe the OLD pattern verbatim.
    """
    no_block = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    no_line = re.sub(r"//[^\n]*", "", no_block)
    return no_line


def test_relayout_does_not_animate_element_opacity(graph_html_src: str) -> None:
    """The core regression. ``cy.elements().animate({ style: { opacity: ... } })``
    writes an inline bypass that overrides .dim's stylesheet opacity:0.12,
    silently killing the blur-others focus effect after a layout change
    while a node is selected.

    The fixed implementation must NOT match this pattern anywhere inside
    relayout(name) (excluding comments).
    """
    body = _strip_js_comments(_relayout_body(graph_html_src))
    # Permissive pattern: any chain ending in .animate({ style: { opacity ... } })
    bad = re.compile(r"\.animate\(\s*\{\s*style\s*:\s*\{\s*opacity\s*:", re.DOTALL)
    matches = bad.findall(body)
    assert not matches, (
        "relayout() must not call .animate({ style: { opacity: ... } }) -- "
        "doing so creates an inline opacity bypass on every cytoscape element "
        "that overrides the .dim class's stylesheet opacity:0.12 rule, killing "
        "the blur-others focus effect after a layout change. Move the "
        "cross-fade to a DOM overlay (pipeline 18). Found "
        f"{len(matches)} occurrence(s)."
    )


def test_relayout_keeps_position_snap_inside_batch(graph_html_src: str) -> None:
    """The position snap must stay inside cy.batch() so the per-node
    redraws collapse into one render pass -- the original performance
    rationale at line 1400 of the v2.2.5 graph.html. We re-assert this
    so the v2.2.6 fix doesn't accidentally drop the batching when
    moving the fade to a DOM overlay.
    """
    body = _relayout_body(graph_html_src)
    assert re.search(r"this\.cy\.batch\(\s*\(\s*\)\s*=>", body), (
        "relayout() must keep the position snap inside cy.batch(() => ...) "
        "so the per-node updates collapse into a single redraw."
    )


# --- the fix: DOM-overlay veil for the cross-fade --------------------


def test_layout_veil_div_exists(graph_html_src: str) -> None:
    """A ``<div class="nebula-layout-veil">`` (or similar) must live
    inside the canvas host so the layout fade runs above the canvas
    instead of through cytoscape's style cascade.
    """
    assert "nebula-layout-veil" in graph_html_src, (
        "graph.html must contain a ``.nebula-layout-veil`` element above "
        "the canvas. This is the DOM-overlay surface that hides the "
        "position snap during relayout(). Pipeline 18 in "
        "reference_mnemo_pipelines.md."
    )


def test_layout_veil_alpine_state(graph_html_src: str) -> None:
    """relayout() must drive the veil through an Alpine state field
    (e.g. ``layoutFading``) so the CSS transition fires reliably.
    A direct DOM mutation works too, but Alpine is the project's
    convention everywhere else in this template.
    """
    body = _relayout_body(graph_html_src)
    assert re.search(r"this\.(layoutFading|layoutVeilOn|veilOn)\s*=", body), (
        "relayout() must toggle a veil-state field on the Alpine "
        "component (e.g. ``this.layoutFading = true; ... = false``). "
        "Found no such assignment inside relayout(name)."
    )


def test_app_css_defines_layout_veil(app_css_src: str) -> None:
    """The veil needs its CSS rule -- absolute positioning over the
    canvas, transparent default, opaque transition when activated.
    """
    assert ".nebula-layout-veil" in app_css_src, (
        "app.css must define .nebula-layout-veil (pipeline 18 -- "
        "DOM-overlay layout cross-fade). Mirrors the v2.2.4 "
        ".nebula-pulse-anchor pattern."
    )


def test_app_css_layout_veil_has_transition(app_css_src: str) -> None:
    """The veil's opacity change must be CSS-transitioned so the
    fade-in / fade-out is smooth without JS animation."""
    # Pull the .nebula-layout-veil block(s) (base and any modifier).
    blocks = re.findall(r"\.nebula-layout-veil[^{]*\{[^}]+\}", app_css_src, re.DOTALL)
    assert blocks, "expected .nebula-layout-veil CSS block(s) in app.css"
    joined = "\n".join(blocks)
    msg = (
        "the .nebula-layout-veil rules must include an opacity transition "
        "so the cross-fade runs without touching cytoscape's animation queue."
    )
    assert "transition" in joined, msg
    assert "opacity" in joined, msg
