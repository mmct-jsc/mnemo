"""v5.1.1: themed custom cursor across daemon UI + demo Pages.

Two SVG assets live under ``daemon/mnemo/ui/static/cursors/`` and
are referenced by ``app.css`` via relative URLs (``cursors/...``).
The daemon serves them at ``/static/cursors/`` and the demo build
copies the directory to ``<out>/cursors/``, so the same relative
URL resolves correctly in both surfaces.

Tests pin:

- Both SVGs exist on disk + parse as valid SVG.
- ``app.css`` references both cursors with hot spot ``16 16``.
- ``app.css`` keeps the OS text I-beam on input[type=text] /
  textarea (so caret placement is unaffected).
- ``scripts/build_demo.py`` copies the cursors directory to the
  demo dist.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CURSORS = REPO_ROOT / "daemon" / "mnemo" / "ui" / "static" / "cursors"
APP_CSS = REPO_ROOT / "daemon" / "mnemo" / "ui" / "static" / "app.css"
BUILD_DEMO = REPO_ROOT / "scripts" / "build_demo.py"

DEFAULT = CURSORS / "mnem-cursor.svg"
POINTER = CURSORS / "mnem-cursor-pointer.svg"
# v5.3.0 cursor variant pack — themed variants for every cursor
# type actually used in mnemo's CSS (audit grep over app.css +
# base.html + chat.html before adding).
GRAB = CURSORS / "mnem-cursor-grab.svg"
GRABBING = CURSORS / "mnem-cursor-grabbing.svg"
NOT_ALLOWED = CURSORS / "mnem-cursor-not-allowed.svg"
COL_RESIZE = CURSORS / "mnem-cursor-col-resize.svg"
PROGRESS = CURSORS / "mnem-cursor-progress.svg"

V5_3_0_VARIANTS = {
    "grab": GRAB,
    "grabbing": GRABBING,
    "not-allowed": NOT_ALLOWED,
    "col-resize": COL_RESIZE,
    "progress": PROGRESS,
}


# --- SVG file contract ----------------------------------------------------


def test_default_cursor_svg_exists() -> None:
    assert DEFAULT.is_file(), f"{DEFAULT} not found"


def test_pointer_cursor_svg_exists() -> None:
    assert POINTER.is_file(), f"{POINTER} not found"


def test_default_cursor_is_valid_svg() -> None:
    """Must parse as XML / SVG so the browser can render it as a
    cursor (malformed SVG silently falls back to the platform cursor)."""
    tree = ET.parse(DEFAULT)
    root = tree.getroot()
    # Element tag is namespaced ({http://www.w3.org/2000/svg}svg) -- strip ns
    assert root.tag.endswith("svg")
    assert root.get("width") == "32"
    assert root.get("height") == "32"


def test_pointer_cursor_is_valid_svg() -> None:
    tree = ET.parse(POINTER)
    root = tree.getroot()
    assert root.tag.endswith("svg")
    assert root.get("width") == "32"
    assert root.get("height") == "32"


def test_cursors_use_accent_palette() -> None:
    """The point of the custom cursor is to match the C1 theme.
    Both SVGs must reference the accent color (#7ee7e0) or its
    hover variant (#a5f0eb)."""
    d = DEFAULT.read_text(encoding="utf-8")
    p = POINTER.read_text(encoding="utf-8")
    assert "#7ee7e0" in d, "default cursor should use the accent color"
    assert "#a5f0eb" in p or "#7ee7e0" in p, (
        "pointer cursor should use the accent or accent-hover color"
    )


# --- app.css wiring contract ----------------------------------------------


def test_app_css_references_default_cursor() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    assert "cursors/mnem-cursor.svg" in css, (
        "app.css must reference cursors/mnem-cursor.svg via url(...)"
    )


def test_app_css_references_pointer_cursor() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    assert "cursors/mnem-cursor-pointer.svg" in css


def test_app_css_specifies_center_hot_spot() -> None:
    """Hot spot must be center (16 16) for a 32x32 SVG so the click
    point lands where the user expects (the bright center dot)."""
    css = APP_CSS.read_text(encoding="utf-8")
    # Both url(...)s must be followed by ``16 16``.
    assert re.search(r"url\(.cursors/mnem-cursor\.svg.\)\s+16\s+16", css), (
        "default cursor must specify hot spot 16 16"
    )
    assert re.search(r"url\(.cursors/mnem-cursor-pointer\.svg.\)\s+16\s+16", css), (
        "pointer cursor must specify hot spot 16 16"
    )


def test_app_css_keeps_text_caret_on_inputs() -> None:
    """Custom cursor must NOT override the OS text I-beam on typing
    surfaces -- caret placement would be lost."""
    css = APP_CSS.read_text(encoding="utf-8")
    # Find the rule block after the input selectors.
    m = re.search(r"input\[type=.text.\].*?cursor:\s*text", css, re.DOTALL)
    assert m is not None, "app.css must keep cursor: text on text inputs"


# --- build_demo.py contract ----------------------------------------------


def test_build_demo_copies_cursors_dir() -> None:
    """The static demo at mmct-jsc.github.io/mnemo must include the
    cursors so the same relative URL in app.css resolves there."""
    text = BUILD_DEMO.read_text(encoding="utf-8")
    assert "cursors" in text, "build_demo.py must copy the cursors directory"
    assert "copytree" in text, "use shutil.copytree to copy the whole cursors/ subtree to dist"


# --- v5.3.0 variant-pack contract -----------------------------------------


def test_all_v5_3_0_variants_exist_on_disk() -> None:
    """Every cursor type audited from mnemo's CSS (grab / grabbing /
    not-allowed / col-resize / progress) must have a themed SVG."""
    for name, path in V5_3_0_VARIANTS.items():
        assert path.is_file(), f"missing themed cursor variant: {name} ({path})"


def test_all_v5_3_0_variants_are_valid_svg() -> None:
    """Every variant must parse as SVG so the browser will actually
    render it as a cursor (malformed SVG falls back to the platform
    cursor silently)."""
    for name, path in V5_3_0_VARIANTS.items():
        tree = ET.parse(path)
        root = tree.getroot()
        assert root.tag.endswith("svg"), f"{name}: root not <svg>"
        assert root.get("width") == "32", f"{name}: width must be 32"
        assert root.get("height") == "32", f"{name}: height must be 32"


def test_all_v5_3_0_variants_use_c1_palette() -> None:
    """Variants must reference the C1 accent (#7ee7e0), the
    accent-hover (#a5f0eb), or the warn tone (#d97757 for the
    not-allowed cursor). Theme consistency is the whole point of
    the variant pack."""
    palette = ("#7ee7e0", "#a5f0eb", "#d97757")
    for name, path in V5_3_0_VARIANTS.items():
        text = path.read_text(encoding="utf-8")
        assert any(c in text for c in palette), (
            f"{name}: no C1 palette color found; the variant must use the theme accent"
        )


def test_every_v5_3_0_variant_is_wired_somewhere() -> None:
    """Each new variant must be referenced by SOME CSS in the bundle
    (app.css, base.html's inline <style>, or chat.html's). Otherwise
    the themed SVG ships but the OS cursor still wins on every
    callsite that uses that cursor type.

    grab + grabbing live in base.html (.mnem-dock and friends);
    not-allowed appears across all three; col-resize + progress are
    app.css-side.
    """
    from pathlib import Path

    ui = Path(__file__).resolve().parents[3] / "daemon" / "mnemo" / "ui"
    bundle = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ui / "static" / "app.css",
            ui / "templates" / "base.html",
            ui / "templates" / "chat.html",
        )
    )
    for name in V5_3_0_VARIANTS:
        marker = f"cursors/mnem-cursor-{name}.svg"
        assert marker in bundle, (
            f"no CSS in {{app.css, base.html, chat.html}} references {marker}; "
            f"the OS cursor still wins on every `cursor: {name}` callsite"
        )


def test_base_html_wires_grab_and_grabbing() -> None:
    """The mnem-dock + drag state cursors live in base.html's inline
    <style> (predates app.css consolidation), so they need their own
    cursor: url(...) edits. v5.3.0 wires both."""
    from pathlib import Path

    base = (
        Path(__file__).resolve().parents[3] / "daemon" / "mnemo" / "ui" / "templates" / "base.html"
    )
    text = base.read_text(encoding="utf-8")
    assert "cursors/mnem-cursor-grab.svg" in text
    assert "cursors/mnem-cursor-grabbing.svg" in text
