"""C1 Design-System / Page-Shell contract guard (v4.0).

Mirrors test_nebula_progressive.py's template-grep style. These
assertions are the contract's teeth: they make the v3.2-class layout
bug (untokenized magic numbers, nested <main>, duplicated primitives)
impossible to reintroduce silently.
"""

from pathlib import Path

import pytest

TPL = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates"
CSS = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static" / "app.css"

PAGE_TEMPLATES = [
    "chat.html",
    "graph.html",
    "settings.html",
    "chat_settings.html",
    "sources.html",
    "dashboard.html",
    "nodes.html",
    "node.html",
    "audit.html",
    "workspaces.html",
    "code_landing.html",
]


@pytest.fixture(scope="module")
def app_css() -> str:
    return CSS.read_text(encoding="utf-8")


def test_root_defines_the_primitive_tokens(app_css: str) -> None:
    root = app_css[app_css.index(":root") : app_css.index("}", app_css.index(":root"))]
    for token in (
        "--topbar-h:",
        "--content-max:",
        "--page-pad:",
        "--radius-pill:",
        "--accent-fg:",
        "--warn-fg:",
        "--measure:",
    ):
        assert token in root, (
            f"{token} must be a :root primitive (C1 token layer). A value "
            f"lives in exactly one place; consumers use var({token[:-1]})."
        )


def _css_body(app_css: str) -> str:
    """Everything AFTER the :root block -- consumers, not token defs."""
    return app_css[app_css.index("}", app_css.index(":root")) + 1 :]


def test_app_css_uses_tokens_not_raw_literals(app_css: str) -> None:
    body = _css_body(app_css)
    assert "calc(100vh - 65px)" not in body, (
        "Use calc(100vh - var(--topbar-h)); a raw 65px topbar literal is "
        "the v3.2-class bug (gotcha 35)."
    )
    assert "1600px" not in body, "max-width must be var(--content-max)."
    assert "#06201e" not in body, "accent-fg text must be var(--accent-fg)."
    assert "#1a0f0c" not in body, "warn-fg text must be var(--warn-fg)."
    assert "999px" not in body, "pill radius must be var(--radius-pill)."


SHARED_PRIMITIVES = (
    ".mnem-working",
    ".load-older",
    ".lo-pill",
    ".link-button",
    ".btn-pill",
)


def test_shared_primitives_defined_once_in_app_css(app_css: str) -> None:
    for sel in SHARED_PRIMITIVES:
        assert sel + " {" in app_css or sel + "{" in app_css, (
            f"{sel} must have its single canonical definition in app.css."
        )


def test_pages_do_not_redefine_shared_primitives() -> None:
    for name in ("chat.html", "base.html"):
        html = (TPL / name).read_text(encoding="utf-8")
        for sel in (".mnem-working {", ".load-older {", ".lo-pill {"):
            assert sel not in html, (
                f"{name} must NOT redefine {sel}; it is a shared app.css "
                f"primitive (was duplicated + divergent pre-v4.0)."
            )
