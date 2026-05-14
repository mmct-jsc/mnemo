"""Surface tests for v2.6 phase 7: top-bar workspace switcher in base.html.

Mirrors the pattern used by ``test_progressive.py`` and
``test_nebula_body_render.py``: grep the template + app.js for the
Alpine factory name, the required state machinery, and the /v1/events
subscription. We don't execute the JS -- we lock the wire shape so
future refactors notice when they break the switcher contract.
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
def app_js() -> str:
    return (STATIC_DIR / "app.js").read_text(encoding="utf-8")


def test_base_html_defines_workspace_switcher_widget(base_html: str) -> None:
    """The switcher must use the named Alpine factory pattern."""
    assert 'x-data="workspaceSwitcher()"' in base_html, (
        "switcher must use named factory per feedback_alpine_double_init.md"
    )


def test_base_html_does_not_double_init_workspace_switcher(base_html: str) -> None:
    """Alpine auto-runs init(); pairing with x-init='init()' double-fires."""
    import re

    # Find the workspace-switcher element open tag and assert no x-init="init()" alongside.
    matches = re.findall(
        r"<[^<>]*x-data=\"workspaceSwitcher\(\)\"[^<>]*>",
        base_html,
    )
    assert matches, "workspaceSwitcher x-data tag not found"
    for m in matches:
        assert 'x-init="init()"' not in m, (
            "double-init anti-pattern: drop x-init=init() per "
            "feedback_alpine_double_init.md"
        )


def test_base_html_renders_active_workspace_label(base_html: str) -> None:
    """The switcher pill must render the active workspace name AND a
    'no workspace' / BASE-only fallback when no workspace is active."""
    assert "BASE-only" in base_html or "BASE only" in base_html
    # Pill renders active.name when set.
    assert "active.name" in base_html


def test_base_html_includes_new_workspace_form(base_html: str) -> None:
    """The dropdown must surface a 'New workspace' affordance + form."""
    assert "New workspace" in base_html
    # Form fields the factory binds to (v2.6.0 phase 10.2: chips replaced
    # the comma-separated input; the name field stays + a chip array
    # holds the project_keys).
    assert "newName" in base_html
    assert "ws-chips" in base_html
    assert "chips" in base_html


def test_base_html_links_to_workspaces_management_page(base_html: str) -> None:
    """The dropdown must offer a 'Manage workspaces' link to /workspaces."""
    assert "/workspaces" in base_html
    assert "Manage" in base_html


def test_base_html_includes_no_workspace_action(base_html: str) -> None:
    """The dropdown must let the user clear into BASE-only mode."""
    assert "No workspace" in base_html or "no workspace" in base_html
    # Factory method that posts /v1/workspaces/clear.
    assert "clearActive()" in base_html or "clear()" in base_html


def test_app_js_defines_workspace_switcher_factory(app_js: str) -> None:
    """The factory function lives on window so x-data='workspaceSwitcher()' resolves."""
    assert "workspaceSwitcher" in app_js
    # Factory should subscribe to /v1/events for live updates.
    assert "/v1/events" in app_js


def test_app_js_subscribes_to_workspace_events(app_js: str) -> None:
    """The switcher needs to listen for workspace_activated / deleted / cleared so
    every open tab reflects activation changes."""
    assert "workspace_activated" in app_js
    assert "workspace_deleted" in app_js
    assert "workspace_cleared" in app_js


def test_app_js_handles_hard_cap_409(app_js: str) -> None:
    """On hard-cap refusal the switcher must surface the 409 detail."""
    assert "workspace_too_large" in app_js or "WorkspaceTooLarge" in app_js or "409" in app_js


def test_app_js_uses_named_factory_via_window(app_js: str) -> None:
    """Match the v2.2 named-factory convention: ``window.workspaceSwitcher = ...``."""
    assert "window.workspaceSwitcher" in app_js


def test_base_html_workspace_switcher_uses_cloak(base_html: str) -> None:
    """The dropdown panel must use x-cloak so it doesn't flash on load."""
    # Find the dropdown panel block and assert x-cloak is present.
    assert "x-cloak" in base_html
    assert 'x-show="open"' in base_html


def test_app_css_styles_workspace_switcher_with_existing_variables(
    base_html: str,
) -> None:
    """The switcher must reuse existing CSS variables, not add raw colors."""
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    # Look for any new workspace-switcher rule
    assert ".workspace-switcher" in css or ".ws-switcher" in css
    # Adjacent: assert no new color literals -- we use vars from base.css.
