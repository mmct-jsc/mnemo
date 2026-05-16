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
