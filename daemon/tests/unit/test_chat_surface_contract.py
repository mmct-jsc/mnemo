"""C3 Chat Surface contract (v4.3).

The capability matrix is declarative single-source; the guard test
makes "a surface silently misses a capability" impossible -- a surface
must DECLARE it (True/False), not omit it -- and that both surfaces
include the shared partials (parity by inclusion, not duplication).
"""

from pathlib import Path

TPL = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates"

CAPS = ("rail", "bookmarks", "examples", "composer", "rename", "switch")
PARTIALS = (
    "_chat_rail.html",
    "_chat_bookmarks.html",
    "_chat_examples.html",
    "_chat_composer.html",
)


def test_matrix_declares_both_surfaces_for_every_capability() -> None:
    from mnemo.ui.chat_surface import CHAT_SURFACES

    assert set(CHAT_SURFACES) == {"page", "dock"}
    for surface, caps in CHAT_SURFACES.items():
        for cap in CAPS:
            assert cap in caps, f"{surface} must DECLARE {cap} (True/False), not omit it"
    # page is the full surface:
    assert all(CHAT_SURFACES["page"][c] for c in CAPS)


def test_both_surfaces_include_the_shared_partials() -> None:
    page = (TPL / "chat.html").read_text(encoding="utf-8")
    base = (TPL / "base.html").read_text(encoding="utf-8")
    for p in PARTIALS:
        assert p in page, f"chat.html must include {p} (no re-implementation)"
        assert p in base, f"dock (base.html) must include {p} (parity by inclusion)"


def test_dock_renders_conversation_list_via_shared_rail() -> None:
    """The dock gains switch/back/new by INCLUDING the shared rail
    (the factory already shares the logic; only rendering was missing)
    -- not by re-implementing it inline."""
    base = (TPL / "base.html").read_text(encoding="utf-8")
    page = (TPL / "chat.html").read_text(encoding="utf-8")
    rail = (TPL / "_chat_rail.html").read_text(encoding="utf-8")
    import re

    assert "_chat_rail.html" in base, "dock must INCLUDE the shared rail"
    assert "_chat_rail.html" in page, "page must INCLUDE the shared rail (single-source)"
    # surface passed to the include (whitespace-tolerant -- Jinja
    # `{% with surface = 'dock' %}`):
    assert re.search(r"surface\s*=\s*'dock'", base), "dock include must pass surface='dock'"
    assert re.search(r"surface\s*=\s*'page'", page), "page include must pass surface='page'"
    # the shared rail binds the ALREADY-shared factory logic:
    assert "groupedConversations" in rail
    assert "openConversation(" in rail
    assert "newConversation()" in rail
    assert "deleteConversation(" in rail
    # and is matrix-gated, not unconditional:
    assert "chat_surfaces[surface].rail" in rail


def test_dock_renders_bookmarks_via_shared_partial() -> None:
    """The dock gains bookmark parity (strip + per-turn star) by
    INCLUDING the shared partial. The factory already shares
    bookmarks[]/isBookmarked/toggleBookmark/jumpTo (loadBookmarks even
    already ran in the dock scope) -- only rendering was missing."""
    base = (TPL / "base.html").read_text(encoding="utf-8")
    page = (TPL / "chat.html").read_text(encoding="utf-8")
    bm = (TPL / "_chat_bookmarks.html").read_text(encoding="utf-8")
    assert "_chat_bookmarks.html" in base, "dock must INCLUDE the shared bookmarks"
    assert "_chat_bookmarks.html" in page, "page must INCLUDE it (single-source)"
    # both the strip and the star are wired in the partial:
    assert "toggleBookmark(" in bm
    assert "isBookmarked(" in bm
    assert "jumpTo(" in bm
    assert "bm-tick" in bm
    assert "bm-star" in bm
    # matrix-gated, not unconditional:
    assert "chat_surfaces[surface].bookmarks" in bm
    # dock wires BOTH parts (strip + star):
    assert "bm = 'strip'" in base
    assert "bm = 'star'" in base


def test_send_icon_is_single_sourced_and_optically_rebalanced() -> None:
    """The send arrow is single-sourced in _chat_composer.html and the
    optically bottom-heavy path is gone (Task 6 -- a PATH/geometry fix,
    not CSS; the buttons already grid-center correctly)."""
    comp = (TPL / "_chat_composer.html").read_text(encoding="utf-8")
    page = (TPL / "chat.html").read_text(encoding="utf-8")
    base = (TPL / "base.html").read_text(encoding="utf-8")
    old_path = "M7 11l5-5 5 5M12 6v13"
    assert old_path not in comp, "the optically bottom-heavy path must be gone"
    assert old_path not in page, "send icon moved to the shared composer"
    assert old_path not in base, "send icon moved to the shared composer"
    # the rebalanced path is the single source: it lives ONLY in the
    # composer partial, never inline in a page (CSS that styles
    # .send-ic legitimately stays in chat.html -- assert on the PATH).
    new_path = "M12 18V6M6 12l6-6 6 6"
    assert new_path in comp
    assert new_path not in page, "send-icon path must NOT be inline in chat.html"
    assert new_path not in base, "send-icon path must NOT be inline in base.html"
    assert "send-ic" in comp  # the icon markup lives in the composer
    assert "_chat_composer.html" in page
    assert "_chat_composer.html" in base
