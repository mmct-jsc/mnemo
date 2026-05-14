"""Surface tests for v2.6 phase 9: reindex report modal in sources.html.

The sources page progress UI grows a "View report" button after a
reindex completes; the button opens a modal with three sections
(auto_skipped / malformed / suspicious) plus per-file decision
buttons (always_skip / always_keep / retry) that POST the user's
choices to /v1/source_overrides.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static"


@pytest.fixture
def sources_html() -> str:
    return (TEMPLATES_DIR / "sources.html").read_text(encoding="utf-8")


@pytest.fixture
def app_js() -> str:
    return (STATIC_DIR / "app.js").read_text(encoding="utf-8")


def test_sources_html_includes_view_report_button(sources_html: str) -> None:
    """A 'View report' button appears in the post-reindex summary."""
    assert "View report" in sources_html or "view report" in sources_html


def test_sources_html_renders_report_modal(sources_html: str) -> None:
    """The modal element exists with x-show + x-cloak guards."""
    assert "reindex-report-modal" in sources_html
    assert 'x-show="showReport"' in sources_html or 'x-show="showReportModal"' in sources_html


def test_sources_html_modal_has_three_sections(sources_html: str) -> None:
    """All three category sections render."""
    assert "auto_skipped" in sources_html or "Auto-skipped" in sources_html
    assert "malformed" in sources_html.lower() or "Malformed" in sources_html
    assert "suspicious" in sources_html.lower() or "Suspicious" in sources_html


def test_sources_html_per_file_decision_buttons(sources_html: str) -> None:
    """Each malformed / suspicious file gets always_skip / always_keep / retry buttons."""
    assert "always_skip" in sources_html
    assert "always_keep" in sources_html
    assert "retry" in sources_html


def test_sources_factory_handles_report_state(sources_html: str) -> None:
    """sourcesPage gains report-modal state + an applyDecisions method.

    The factory lives inline in sources.html (not app.js) so the tests
    look there.
    """
    assert "setDecision" in sources_html
    assert "applyDecisions" in sources_html


def test_sources_factory_posts_overrides_to_api(sources_html: str) -> None:
    """The apply path POSTs to /v1/source_overrides."""
    assert "/v1/source_overrides" in sources_html


def test_sources_factory_listens_for_report_event(sources_html: str) -> None:
    """The SSE stream parser surfaces the 'report' event into sourcesPage state."""
    assert "'report'" in sources_html or '"report"' in sources_html
