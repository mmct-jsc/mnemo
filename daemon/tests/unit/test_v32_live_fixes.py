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
  3. Nebula: lovely zoomed out, an unreadable hairball zoomed in (15k
     links). Root cause: per-edge alpha 0.78 + linkWidth 1.6 +
     scaleLinksOnZoom:true (links FATTEN on zoom-in). Fix: soft alpha,
     thinner width, scaleLinksOnZoom:false; selection greyout still
     makes the focused web pop.
"""

from __future__ import annotations

from pathlib import Path

from mnemo.chat import DEFAULT_SYSTEM

_UI = Path(__file__).resolve().parents[2] / "mnemo" / "ui"
BASE_HTML = (_UI / "templates" / "base.html").read_text(encoding="utf-8")
CHAT_JS = (_UI / "static" / "chat.js").read_text(encoding="utf-8")
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
    # the reload is now conditional, not unconditional
    assert "window.location.href = a.path" in CHAT_JS


def test_default_system_prefers_in_page_actions_over_navigate() -> None:
    # the model is told it can act IN the current page and that
    # navigate ends the turn (so it stops blindly redirecting).
    assert "mnemo_page_context" in DEFAULT_SYSTEM
    assert "mnemo_navigate" in DEFAULT_SYSTEM
    assert "in the page" in DEFAULT_SYSTEM or "in-page" in DEFAULT_SYSTEM


# --- 3. nebula links: soft but visible, no zoom-in hairball ------------


def test_nebula_links_are_softened_and_dont_scale_on_zoom() -> None:
    # the dominant "mess when zoomed in" cause: links fattened with
    # zoom. Off now.
    assert "scaleLinksOnZoom: false" in GRAPH_HTML
    assert "scaleLinksOnZoom: true" not in GRAPH_HTML
    # the hard-coded over-bright per-edge alpha (0.78) is gone, replaced
    # by a soft named constant
    assert "= 0.78;" not in GRAPH_HTML
    assert "LINK_ALPHA" in GRAPH_HTML
    # thinner base width (was 1.6) so dense regions don't smear
    assert "linkWidth: 1.6" not in GRAPH_HTML
    # the never-fade distance range stays (prior reasoning is sound --
    # don't reintroduce the zoom-in fade)
    assert "linkVisibilityDistanceRange" in GRAPH_HTML
