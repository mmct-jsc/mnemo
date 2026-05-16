"""v3.1 phase 8: token budget bar + bookmark navigation UI.

Surface tests (test_nebula_progressive pattern). Design
2026-05-15-mnemo-v3.1 S3.6 fork 4:

  * a running token chip / thin budget bar (tokens_total vs the
    per-provider budget, warns near the cap) + per-turn token counts
    on assistant turns.
  * a star affordance per turn -> POST/DELETE .../bookmarks, and a
    slim jump strip; server-persisted (already wired phase 5) so it
    survives reload + device.
"""

from __future__ import annotations

from pathlib import Path

_UI = Path(__file__).resolve().parents[2] / "mnemo" / "ui"
CHAT_JS = (_UI / "static" / "chat.js").read_text(encoding="utf-8")
CHAT_HTML = (_UI / "templates" / "chat.html").read_text(encoding="utf-8")
BASE_HTML = (_UI / "templates" / "base.html").read_text(encoding="utf-8")
# v4.3 (C3): bookmark UI moved into a SHARED partial that chat.html +
# the dock both include (single-source; the dock GAINED bookmarks).
CHAT_BM = (_UI / "templates" / "_chat_bookmarks.html").read_text(encoding="utf-8")


# --- token budget -------------------------------------------------------


def test_shared_module_computes_a_token_budget() -> None:
    # a per-provider budget map + a fraction the bar binds to
    assert "tokenBudget" in CHAT_JS
    assert "budgetFrac" in CHAT_JS or "budgetPct" in CHAT_JS
    # warn state near the cap
    assert "budgetWarn" in CHAT_JS or "warn" in CHAT_JS


def test_chat_page_renders_a_budget_bar_and_per_turn_tokens() -> None:
    assert "tok-bar" in CHAT_HTML  # the thin running budget bar
    assert "tok-fill" in CHAT_HTML  # the fill element bound to the frac
    assert "tokensTotal" in CHAT_HTML  # running counter shown
    # per-turn usage on assistant turns (dim / on hover)
    assert "turn-tok" in CHAT_HTML
    assert "token_out" in CHAT_HTML


# --- bookmarks ----------------------------------------------------------


def test_shared_module_has_bookmark_methods() -> None:
    for token in ("loadBookmarks", "toggleBookmark", "isBookmarked", "jumpTo"):
        assert token in CHAT_JS, token
    # hits the phase-5 server API
    assert "/bookmarks" in CHAT_JS
    assert "method: 'DELETE'" in CHAT_JS or 'method: "DELETE"' in CHAT_JS
    # bookmarks are (re)loaded with the conversation
    assert "loadBookmarks(" in CHAT_JS


def test_chat_page_has_bookmark_star_and_jump_strip() -> None:
    # v4.3 (C3): single-sourced in _chat_bookmarks.html (chat.html
    # includes it; the dock now does too). Same contract-evolution as
    # v4.0 moved tokenized literals -- capability preserved + on both.
    assert "_chat_bookmarks.html" in CHAT_HTML  # page includes it
    assert "bm-star" in CHAT_BM  # per-turn star affordance
    assert "toggleBookmark(" in CHAT_BM
    assert "bm-strip" in CHAT_BM  # the slim jump strip / minimap
    assert "jumpTo(" in CHAT_BM


def test_dock_keeps_the_token_chip() -> None:
    # phase 7 added the compact token chip; phase 8 keeps it wired
    assert "mc-tok" in BASE_HTML
    assert "tokensTotal" in BASE_HTML
