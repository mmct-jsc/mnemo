"""Surface tests for v2.6 phase 10.2: workspace add UX with chips.

Replaces the comma-separated text field with a chip-based input
backed by /v1/fs/suggest + /v1/projects/known autocomplete. Picking
a filesystem path resolves to a project_key via /v1/projects/resolve;
picking a known project_key adds it directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static"


@pytest.fixture
def base_html() -> str:
    return (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")


@pytest.fixture
def workspaces_html() -> str:
    return (TEMPLATES_DIR / "workspaces.html").read_text(encoding="utf-8")


@pytest.fixture
def app_js() -> str:
    return (STATIC_DIR / "app.js").read_text(encoding="utf-8")


@pytest.fixture
def app_css() -> str:
    return (STATIC_DIR / "app.css").read_text(encoding="utf-8")


# --- Factory state ----------------------------------------------------------


def test_workspace_switcher_has_chip_state(app_js: str) -> None:
    """The switcher's new form holds an array of project_key chips +
    methods to add/remove them."""
    assert "chips" in app_js
    assert "addChip" in app_js
    assert "removeChip" in app_js


def test_workspaces_page_has_chip_state(app_js: str) -> None:
    """The /workspaces page add form uses the same chip state."""
    # Both factories share the chip methods; we expect each to define
    # addChip + removeChip + chips.
    occurrences = app_js.count("addChip")
    assert occurrences >= 2, (
        "both workspaceSwitcher AND workspacesPage should expose addChip"
    )


def test_app_js_resolves_filesystem_path_to_project_key(app_js: str) -> None:
    """Picking a filesystem path triggers /v1/projects/resolve to derive
    the project_key for the chip."""
    assert "/v1/projects/resolve" in app_js


def test_app_js_fetches_known_project_keys(app_js: str) -> None:
    """The autocomplete pulls from both /v1/fs/suggest AND /v1/projects/known."""
    assert "/v1/fs/suggest" in app_js
    assert "/v1/projects/known" in app_js


# --- Template markup --------------------------------------------------------


def test_switcher_dropdown_renders_chip_input(base_html: str) -> None:
    """The "+ New workspace" form in the top-bar dropdown uses the
    chip-based input, not a comma-separated text field."""
    # The chip-input wrapper carries a distinctive class so the test
    # locks the structure regardless of the exact <template x-for>
    # shape.
    assert "ws-chips" in base_html
    # Chips render with an x-for over the chips array.
    assert "x-for=\"chip" in base_html or "x-for=\"(chip" in base_html


def test_workspaces_page_renders_chip_input(workspaces_html: str) -> None:
    """The /workspaces page's New workspace card uses the same chip input."""
    assert "ws-chips" in workspaces_html


def test_chip_has_remove_x(base_html: str) -> None:
    """Each chip carries an X button calling removeChip(idx)."""
    assert "removeChip(" in base_html
    # Visible X glyph or aria-labeled close button.
    assert "ws-chip-remove" in base_html or "&times;" in base_html


def test_no_longer_uses_comma_separated_input(base_html: str) -> None:
    """The old text field that asked for comma-separated keys is gone."""
    # We removed the placeholder that said "comma-separated"; make sure
    # the workspace switcher new-form section no longer relies on it.
    # (We only check inside the switcher block to avoid false hits in
    # the workspaces-page template, which we test separately.)
    import re

    switcher_block = re.search(
        r"<div class=\"workspace-switcher\".*?</div>\s*</div>\s*</div>",
        base_html,
        re.DOTALL,
    )
    assert switcher_block is not None
    text = switcher_block.group(0)
    # The phrase "comma-separated" should not appear in the switcher block.
    assert "comma-separated" not in text.lower()


# --- CSS hooks --------------------------------------------------------------


def test_app_css_styles_chips(app_css: str) -> None:
    assert ".ws-chip" in app_css
    # The remove-X is its own affordance.
    assert ".ws-chip-remove" in app_css or ".ws-chip button" in app_css


def test_app_css_uses_existing_variables_for_chips(app_css: str) -> None:
    """Chips reuse the existing palette -- no new color literals."""
    # Find the chip rule block and assert it references shared vars.
    import re

    m = re.search(r"\.ws-chip\s*\{[^}]*\}", app_css, re.DOTALL)
    assert m is not None
    block = m.group(0)
    # At least one var(--*) reference inside the rule.
    assert "var(--" in block
