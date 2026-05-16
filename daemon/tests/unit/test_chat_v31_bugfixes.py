"""v3.1 live-review bug fixes (root-caused via the preview tool).

Reproduced live on 2026-05-15:
  1. logo blank -> mark.svg had `--` inside an XML comment => invalid
     XML; <img> strict-parses and fails (naturalWidth 0). Every shipped
     SVG must be well-formed XML.
  2. dock drag dead -> the launcher <img> triggers native HTML image
     drag-and-drop, hijacking the custom pointer drag.
  3/4. chat shows raw `## ...` markdown + cited-node preview stuck on
     the title -> the toy renderText()/stalling mnemoRenderBody stream;
     must render through the real marked+DOMPurify pipeline
     (window.mnemoMd), one-shot.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

_UI = Path(__file__).resolve().parents[2] / "mnemo" / "ui"
MNEM_DIR = _UI / "static" / "mnem"
CHAT_JS = (_UI / "static" / "chat.js").read_text(encoding="utf-8")
CHAT_HTML = (_UI / "templates" / "chat.html").read_text(encoding="utf-8")
BASE_HTML = (_UI / "templates" / "base.html").read_text(encoding="utf-8")


# --- Bug 1: every shipped SVG must be well-formed XML -------------------


def test_all_mnem_svgs_are_well_formed_xml() -> None:
    svgs = sorted(MNEM_DIR.glob("*.svg"))
    assert svgs, "no mnem SVG assets found"
    for svg in svgs:
        text = svg.read_text(encoding="utf-8")
        # would raise ET.ParseError on the `--`-in-comment bug
        ET.fromstring(text)
        # belt-and-suspenders: no `--` inside an XML comment
        for block in text.split("<!--")[1:]:
            body = block.split("-->")[0]
            assert "--" not in body, f"{svg.name}: '--' inside an XML comment"


def test_mark_svg_renders_as_a_glyph() -> None:
    svg = (MNEM_DIR / "mark.svg").read_text(encoding="utf-8")
    root = ET.fromstring(svg)  # must parse
    assert root.tag.endswith("svg")
    assert root.attrib.get("viewBox") == "0 0 64 64"


# --- Bug 2: the dock launcher image must not native-drag ---------------


def test_dock_image_is_not_natively_draggable() -> None:
    # the <img> inside .mnem-dock must opt out of native image DnD,
    # else it hijacks the custom pointer drag
    assert 'draggable="false"' in BASE_HTML
    assert "user-drag" in BASE_HTML  # CSS belt: -webkit-user-drag:none
    # the brand mark MUST be cache-busted -- without ?v= the browser
    # pins the (possibly broken) first response forever (live-review:
    # users kept seeing the old blank logo until a version change)
    assert "/static/mnem/mark.svg?v=" in BASE_HTML
    # dragStart suppresses the default (text/img drag) + tolerates a
    # 0-size viewport (don't snap off-screen)
    assert "preventDefault" in BASE_HTML
    assert "innerWidth || " in BASE_HTML or "vw =" in BASE_HTML


def test_dock_drag_mutates_the_tracked_reactive_component() -> None:
    """Root cause of "drag still failing": the @pointerdown handler's
    `this` is a child-scoped wrapper, NOT Alpine.$data('.mnem-wrap')
    (the proxy the :style="posStyle" effect tracks), so mutating
    this.pos never re-ran the binding -- data moved, DOM didn't. The
    drag MUST mutate the tracked component (Alpine.$data(el)) AND drive
    the root element imperatively for the high-frequency move."""
    assert "Alpine.$data(el)" in BASE_HTML  # mutate the tracked proxy
    # imperative position write on the root during the drag (pipeline
    # 18: direct DOM for high-frequency interaction; don't fight the
    # reactive layer per move)
    assert "el.style.left" in BASE_HTML
    assert "el.style.top" in BASE_HTML


# --- Bug 3/4: real markdown rendering, one-shot ------------------------


def test_chat_uses_the_real_markdown_pipeline() -> None:
    # the shared module renders assistant prose through window.mnemoMd
    # (marked + DOMPurify: headings/lists/tables/code), not the toy
    assert "window.mnemoMd" in CHAT_JS
    assert "renderMarkdown" in CHAT_JS
    # mnemo-draft fences still stripped from prose; [mnemo:id] still
    # rewritten to cite links
    assert "mnemo-draft" in CHAT_JS
    assert "cite-link" in CHAT_JS


def test_chat_templates_bind_render_markdown() -> None:
    # /chat page and the dock both render via renderMarkdown(...)
    assert "renderMarkdown(m.content.text" in CHAT_HTML
    assert "renderMarkdown(m.content.text" in BASE_HTML


def test_citation_preview_is_one_shot_not_the_stalling_stream() -> None:
    # the cited-node preview must render directly (mnemoMd / escaped
    # pre), NOT the v2.x bucket-stream reveal that stalls after the
    # first fragment
    assert "previewNode" in CHAT_JS
    # the stalling streaming CALL is gone (the word may remain in a
    # comment explaining why)
    assert "window.mnemoRenderBody(" not in CHAT_JS
    assert "previewMarkup" in CHAT_JS
    assert "window.mnemoMd" in CHAT_JS


# --- Live-review round 3: layout / viewport bugs -----------------------


def test_chat_thread_is_a_bounded_flex_scroller() -> None:
    """Composer fell off the bottom + the last message was unreachable
    + scroll() no-op'd because .thread-scroll (flex:1; overflow-y:auto)
    and its .chat-thread grid item lacked min-height:0, so the scroller
    expanded to content instead of bounding + scrolling internally."""
    css = CHAT_HTML
    # canonical flexbox-scroll fix on the /chat thread
    assert "min-height: 0" in css
    # the scroller must take remaining space AND be allowed to shrink
    assert "flex: 1 1 0" in css or "flex: 1 1 0%" in css
    # dvh so the mobile URL bar doesn't push the composer off
    assert "100dvh" in css
    # pin-to-bottom must survive post-$nextTick markdown layout:
    # re-pin on a rAF + a short timeout, instant via scrollTop
    assert "requestAnimationFrame" in CHAT_JS
    assert "l.scrollTop = l.scrollHeight" in CHAT_JS
    # .thread-scroll must NOT carry scroll-behavior:smooth -- it
    # animates every programmatic pin (fired nextTick+rAF+timeout) so
    # the smooth scrolls stomp each other and never reach the bottom.
    import re

    m = re.search(r"\.thread-scroll \{[^}]*\}", css, re.S)
    assert m
    assert "scroll-behavior" not in m.group(0)


def test_dock_panel_is_decoupled_and_viewport_bounded() -> None:
    """The 380px panel was flex-stacked above the draggable launcher in
    .mnem-wrap and top-anchored after a drag, so opening it shoved the
    launcher off-screen ("popup push it too hard") and could exceed the
    viewport. It must be its OWN fixed, viewport-clamped element."""
    css = BASE_HTML
    # .mnem-chat decoupled to position:fixed (not in the launcher flow)
    import re

    m = re.search(r"\.mnem-chat \{[^}]*\}", css, re.S)
    assert m, ".mnem-chat rule not found"
    rule = m.group(0)
    assert "position: fixed" in rule
    # never exceeds the viewport (min()/calc against dvh/vw)
    assert "100dvh" in rule
    assert "min(" in rule
    # dock thread also gets the bounded-scroller fix
    assert "min-height: 0" in css


def test_dock_panel_is_anchored_to_the_companion_side_aware() -> None:
    """The panel must STICK to the companion launcher (move with it,
    flip side based on the launcher's position) -- not sit detached in
    a fixed corner. JS computes it from the launcher rect + clamps."""
    js = BASE_HTML
    assert "_positionPanel" in js
    assert "_repositionSoon" in js
    # anchors off the launcher's rect + flips horizontally/vertically
    assert "querySelector('.mnem-dock')" in js
    assert "lr.right - pw" in js  # flip: open leftward on the right side
    assert "lr.bottom + gap" in js or "lr.top - ph" in js  # vertical flip
    # follows while dragging + re-anchors on snap/clamp/open
    assert "cmp._positionPanel()" in js
    assert "this._positionPanel()" in js
    # the always-bottom-right hardcoding is gone (only a pre-JS
    # fallback remains)
    import re

    m = re.search(r"\.mnem-chat \{[^}]*\}", BASE_HTML, re.S)
    assert m
    assert "right: 18px; bottom: 18px" not in m.group(0)


def test_dock_clamps_persisted_position_into_the_viewport() -> None:
    """A stale/oversized mnem.pos (smaller window, other device, or
    bad save) stranded the dock off-screen because init() applied it
    verbatim. It must clamp on load AND on resize."""
    js = BASE_HTML
    assert "_clampPos" in js
    assert "addEventListener('resize'" in js or 'addEventListener("resize"' in js
