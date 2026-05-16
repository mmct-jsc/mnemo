"""v3.2 live-review fixes (root-caused via the preview tool, 2026-05-16).

Three user-reported issues, each root-caused before fixing:

  1. The dock USER bubble rendered as a huge dark box wrapping the real
     bubble. Root cause (measured live -- two .mc-user matches, outer
     200px / inner 59px): the turn wrapper ``:class="'mc-' + m.role"``
     makes the .mc-turn wrapper ALSO match ``.mc-user`` for a user
     turn, double-applying the bubble box. Only ``user`` collides
     (mc-assistant / mc-tool_call have no rule). Fix: scope the bubble
     rule to ``.mc-user:not(.mc-turn)``.
  2. "show navigate then stuck": chat.js hard-reloaded the page even
     when already on the target, killing the dock SSE + the in-flight
     agent loop so the follow-up in-page tools never ran. Fix: no-op
     the navigate when already on that path (the run continues); guide
     the model (DEFAULT_SYSTEM) to prefer in-page tools + treat
     navigate as terminal.
  3. Nebula: lovely zoomed out, a hairball zoomed in. ATTEMPTED a link
     softening (alpha/width/scaleLinksOnZoom) -- it RE-TRIGGERED the
     v2.6.8 cosmos converge-and-stop hang (init froze in degenerate
     clumps, the layout never settled, the GPU/CPU pegged the laptop).
     Per feedback_revert_over_perfectionize + reference_cosmos_gl_nebula
     (the documented closed outcome behind the v2.6.8->v2.6.6 revert),
     the fix was REVERTED -- graph.html restored to the P4 known-good.
     The zoomed-in density is an ACCEPTED limitation pending a renderer
     swap; do NOT re-tune cosmos config. The guard test below locks
     that closed outcome so it can't silently recur.
"""

from __future__ import annotations

from pathlib import Path

from mnemo.chat import DEFAULT_SYSTEM

_UI = Path(__file__).resolve().parents[2] / "mnemo" / "ui"
BASE_HTML = (_UI / "templates" / "base.html").read_text(encoding="utf-8")
CHAT_JS = (_UI / "static" / "chat.js").read_text(encoding="utf-8")
CHAT_HTML = (_UI / "templates" / "chat.html").read_text(encoding="utf-8")
GRAPH_HTML = (_UI / "templates" / "graph.html").read_text(encoding="utf-8")


# --- 1. dock user bubble must not double-apply to the wrapper ----------


def test_dock_user_bubble_rule_is_scoped_off_the_turn_wrapper() -> None:
    # the fix: the bubble box is scoped so the role-classed .mc-turn
    # wrapper (which also gets `mc-user`) can NOT match it.
    assert ".mc-user:not(.mc-turn)" in BASE_HTML
    # the old unscoped rule (caught by the wrapper too) is gone
    assert "\n    .mc-user {" not in BASE_HTML


# --- 2. navigate must not nuke the in-flight run ----------------------


def test_navigate_is_a_noop_when_already_on_the_target() -> None:
    # chat.js compares the destination to the current path and does NOT
    # reload when they match (a full load kills this dock's SSE + the
    # running agent loop -> the reported "stuck after navigate").
    assert "location.pathname" in CHAT_JS
    assert "already on" in CHAT_JS
    # same-path = no-op; a DIFFERENT page now opens in a NEW TAB (the
    # self-destroying reload was removed -- see the new-tab test below)
    assert "window.open(a.path, '_blank'" in CHAT_JS


def test_default_system_prefers_in_page_actions_over_navigate() -> None:
    # the model is told it can act IN the current page and that
    # navigate ends the turn (so it stops blindly redirecting).
    assert "mnemo_page_context" in DEFAULT_SYSTEM
    assert "mnemo_navigate" in DEFAULT_SYSTEM
    assert "in the page" in DEFAULT_SYSTEM or "in-page" in DEFAULT_SYSTEM


# --- 3. nebula links: soft but visible, no zoom-in hairball ------------


def test_nebula_is_the_documented_kept_good_v266_renderer() -> None:
    """CLOSED OUTCOME (revert-over-perfectionize, FINAL + root-caused).

    The nebula "stuck" was NEVER my v3.2 graph edits per se -- it was
    that ``main`` itself carries the BROKEN v2.6.8 perfectionize
    renderer: the documented v2.6.6 revert (``a341c89``, restoring
    ``graph.html @ 8caf257``) NEVER merged to main (PR #53 was an empty
    no-op), and release/3.0.0 was cut from that main. So every
    "revert to main / a002b34 / P4" reverted graph.html *to the broken
    v2.6.8*. The fix is `git checkout 8caf257 -- graph.html` --
    the kept-good v2.6.6 (reference_cosmos_gl_nebula.md). Live-verified
    over 15s: all 11010 nodes in perpetual motion, growing deltas,
    healthy spread, NO freeze (the v2.6.8 froze hard at ~8s).

    This guard pins graph.html to the v2.6.6 kept-good: NO v3.2 wiring,
    NO v2.6.8 perfectionize artefacts. A livelier/agentic nebula needs
    a renderer swap, never cosmos config/wiring (the closed ceiling)."""
    # v2.6.6 kept-good anchors (the never-cool perpetual sim)
    assert "NEVER-COOL SIM + CACHED SEED" in GRAPH_HTML
    assert "this.cg.start(useCache ? 0.35 : 1)" in GRAPH_HTML
    assert "simulationDecay: 1000000000" in GRAPH_HTML
    # ZERO v2.6.8 perfectionize artefacts (the regression on main)
    for m in ("_pinAll", "_flyTo", "setPinnedPoints", "SUPERSEDES", "v2.6.8", "_fitToView"):
        assert m not in GRAPH_HTML, f"v2.6.8 perfectionize re-crept onto nebula: {m}"
    # ZERO v3.2 artefacts on the nebula page
    for m in (
        "LINK_ALPHA",
        "scaleLinksOnZoom: false",
        "_wireCompanionActions",
        "mnemo-highlight-nodes",
        "mnemo-select-node",
        "window.mnemoPageContext",
    ):
        assert m not in GRAPH_HTML, f"v3.2 re-crept onto nebula: {m}"


# --- chat-UX completion (live review #N: copy / scroll / error+retry) --


def test_chat_js_has_the_missing_common_utils() -> None:
    # the shared brain gained: clean error banner + one-click retry
    # (NOT a raw provider-JSON dump), copy-message, scroll-to-latest.
    for sym in (
        "copyText:",
        "retry:",
        "dismissError:",
        "onScroll:",
        "jumpToLatest:",
        "_humanError:",
        "lastUserText",
        "showJump",
    ):
        assert sym in CHAT_JS, f"chat.js missing util: {sym}"
    # the SSE error handler sets a clean banner string, not a toast dump
    assert "self.error = self._humanError(" in CHAT_JS
    # the raw provider JSON is summarised, never shown whole
    assert "invalid_request_error" in CHAT_JS  # mapped to a human line


def test_chat_page_wires_error_copy_jump() -> None:
    assert 'class="chat-error"' in CHAT_HTML
    assert 'class="chat-jump"' in CHAT_HTML
    assert 'class="msg-copy"' in CHAT_HTML
    assert '@scroll.passive="onScroll()"' in CHAT_HTML
    assert "retry()" in CHAT_HTML
    assert "copyText(" in CHAT_HTML


def test_dock_wires_error_copy_jump_and_real_send_button() -> None:
    assert 'class="mc-error"' in BASE_HTML
    assert 'class="mc-jump"' in BASE_HTML
    assert 'class="mc-copy"' in BASE_HTML
    assert '@scroll.passive="onScroll()"' in BASE_HTML
    assert "retry()" in BASE_HTML
    # the "dumb" bare text-arrow send button is gone -- it's a proper
    # icon button now (aria-label Send + an inline svg, circular).
    assert ">→</button>" not in BASE_HTML
    assert 'class="mc-send"' in BASE_HTML
    assert 'aria-label="Send"' in BASE_HTML
    assert ".mc-send svg" in BASE_HTML  # styled icon, not a glyph
    # long-text no longer smears horizontally in the narrow dock
    assert "overflow-wrap: anywhere" in BASE_HTML


# --- chat-UX refine round (Claude-quality polish + 2 features) ---------

APP_CSS = (_UI / "static" / "app.css").read_text(encoding="utf-8")


def test_navigate_opens_a_new_tab_not_a_self_destroying_reload() -> None:
    # cross-page mnemo_navigate must NOT tear down the dock SSE +
    # abandon the running loop ("show me in nebula" -> connection
    # dropped). New tab keeps the conversation alive.
    assert "window.open(a.path, '_blank'" in CHAT_JS
    # the self-destroying reload of the target path is gone
    assert "window.location.href = a.path" not in CHAT_JS


def test_chat_has_delete_and_dock_has_new_session() -> None:
    assert "deleteConversation:" in CHAT_JS
    assert "deleteConversation(c.id" in CHAT_HTML  # rail delete button
    assert 'class="cv-del"' in CHAT_HTML
    assert 'class="mc-new"' in BASE_HTML  # new-chat in the dock header
    assert '@click="newConversation()"' in BASE_HTML


def test_cite_on_chat_page_uses_side_panel_not_overlapping_popover() -> None:
    # window.mnemoCite routes to the existing side panel when present
    assert "querySelector('.cite-preview')" in BASE_HTML
    assert "new CustomEvent('mnemo-cite'" in BASE_HTML
    assert "onCiteEvent:" in CHAT_JS
    assert "@mnemo-cite.window=" in CHAT_HTML


def test_jitter_send_thinking_polish() -> None:
    # scrollbar-gutter:stable kills the "Latest" pill + content jitter
    assert "scrollbar-gutter: stable" in CHAT_HTML
    assert "scrollbar-gutter: stable" in BASE_HTML
    # the off-centre send-icon nudges are gone (place-items centers it)
    assert ".send .send-ic { transform: translateY" not in CHAT_HTML
    assert ".send .send-ic { display: block; }" in CHAT_HTML
    assert ".mc-send svg { transform: translateY" not in BASE_HTML
    assert ".mc-send svg { display: block; }" in BASE_HTML
    # the thinking indicator fades in (was a sudden pop)
    assert "ce-fade" in CHAT_HTML
    assert "ce-fade" in BASE_HTML
    assert 'x-transition:enter="ce-fade"' in CHAT_HTML
    # nebula close button optical-centering hardened in app.css
    # (NOT graph.html -- that file is byte-pinned to v2.6.6)
    assert "aspect-ratio: 1;" in APP_CSS
