"""v3.2 phase 3: the companion acts IN the page; citations in-context.

The ui_action CustomEvents (mnemo-select-node / -set-filter /
-open-panel) were already dispatched by chat.js -- but NO page listened
(v3.2 design S2 "THE GAP"). And inline ``[mnemo:id]`` cite-links were a
blind redirect to a plain node page. P3:

  * graph.html (and settings) now LISTEN for those events and drive the
    live view (focus a node, drive the real filter pipeline);
  * ``[mnemo:id]`` becomes context-aware: on Nebula it dispatches an
    in-page select; elsewhere a shared inline popover (reusing the same
    window.mnemoMd / Prism globals previewMarkup uses); a full-page
    ``/node/<id>`` redirect is ONLY the final fallback.

Alpine / DOM / CustomEvent can't run under pytest, so the contract is
asserted by template / JS surface greps (the test_chat_v31_bugfixes /
test_nebula_progressive pattern).
"""

from __future__ import annotations

from pathlib import Path

_UI = Path(__file__).resolve().parents[2] / "mnemo" / "ui"
BASE_HTML = (_UI / "templates" / "base.html").read_text(encoding="utf-8")
CHAT_JS = (_UI / "static" / "chat.js").read_text(encoding="utf-8")
GRAPH_HTML = (_UI / "templates" / "graph.html").read_text(encoding="utf-8")
SETTINGS_HTML = (_UI / "templates" / "settings.html").read_text(encoding="utf-8")


# --- context-aware [mnemo:id] ------------------------------------------


def test_cite_links_route_through_context_aware_handler() -> None:
    # _citeLinks is no longer a blind redirect: it tags the link with
    # data-cite and routes the click through window.mnemoCite, which
    # decides in-page action / inline popover / redirect-last.
    assert "_citeLinks" in CHAT_JS
    assert "data-cite" in CHAT_JS
    assert "window.mnemoCite" in CHAT_JS
    # the class is kept (test_chat_v31_bugfixes still asserts it)
    assert "cite-link" in CHAT_JS


def test_base_defines_context_aware_cite_handler_and_popover() -> None:
    assert "window.mnemoCite" in BASE_HTML
    assert "window.mnemoCitePopover" in BASE_HTML
    # on Nebula/code -> dispatch an in-page select (NOT a redirect)
    assert "mnemo-select-node" in BASE_HTML
    # the popover reuses the shared markdown renderer (DRY with the
    # chat.js previewMarkup path -- same global)
    assert "window.mnemoMd" in BASE_HTML
    # a full-page node redirect remains, but only as the LAST fallback
    assert "/node/" in BASE_HTML
    # the popover has its own styling (it's a real inline surface)
    assert ".mnemo-cite-pop" in BASE_HTML


# --- in-page ui_action listeners ---------------------------------------


def test_graph_page_wires_the_companion_into_the_sigma_renderer() -> None:
    """CONTRACT EVOLUTION (v4.5): the v3.2 closed outcome was 'do NOT
    wire the companion into the renderer -- cosmos froze, a live
    nebula needs a renderer SWAP, not wiring'. v4.5 IS that swap
    (sigma.js v3 + graphology). The companion's highlight/select _ui
    sentinels are now wired DIRECTLY into nebula() and drive a real
    sigma highlight -- closing the gotcha-31 / C3-honesty loop."""
    assert "addEventListener('mnemo-highlight-nodes'" in GRAPH_HTML, (
        "graph.html must listen for the companion's highlight event "
        "(v4.5 closed loop -- the graph itself lights up now)."
    )
    assert "addEventListener('mnemo-select-node'" in GRAPH_HTML, (
        "graph.html must listen for the companion's select-node event."
    )
    # but still NO bespoke wiring shim + NO page-context override on
    # the renderer (YAGNI -- direct document listeners only).
    assert "_wireCompanionActions" not in GRAPH_HTML
    assert "window.mnemoPageContext" not in GRAPH_HTML
    # the context-aware cite handler still lives in base.html.
    assert "window.mnemoCite" in BASE_HTML


def test_settings_listens_for_companion_panel_action() -> None:
    assert "addEventListener(" in SETTINGS_HTML
    assert ("mnemo-open-panel" in SETTINGS_HTML) or ("mnemo-retune" in SETTINGS_HTML)
    # it focuses the real retune card (not a no-op)
    assert "retune-card" in SETTINGS_HTML
