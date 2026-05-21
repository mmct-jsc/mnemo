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
