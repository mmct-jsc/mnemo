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
