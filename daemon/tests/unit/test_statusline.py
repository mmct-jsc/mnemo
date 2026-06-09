"""v5.25.0 step 2: the ``mnemo statusline`` presence line for Claude Code.

Pure formatting + the best-effort health probe / inject-count read are
unit-tested without a live daemon; the CLI wrapper and the per-session
inject-count round trip are covered too. The render() entrypoint must
NEVER raise (a thrown statusline would break the user's status bar).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnemo import statusline
from mnemo.cli import app


@pytest.fixture(autouse=True)
def _sandbox(isolated_mnemo_home: Path) -> Path:
    return isolated_mnemo_home


# --- pure formatting ------------------------------------------------------


def test_compact_count() -> None:
    assert statusline._compact_count(42) == "42"
    assert statusline._compact_count(999) == "999"
    assert statusline._compact_count(17919) == "17.9k"
    assert statusline._compact_count(1_500_000) == "1.5M"


def test_format_statusline_offline() -> None:
    assert statusline.format_statusline(None) == "mnemo offline"
    assert statusline.format_statusline({}) == "mnemo offline"


def test_format_statusline_healthy() -> None:
    assert statusline.format_statusline({"node_count": 17919}) == "mnemo 17.9k"


def test_format_statusline_with_inject() -> None:
    assert statusline.format_statusline({"node_count": 17919}, 3) == "mnemo 17.9k up3"


def test_format_statusline_inject_zero_is_omitted() -> None:
    assert statusline.format_statusline({"node_count": 5}, 0) == "mnemo 5"


# --- inject-count round trip ----------------------------------------------


def test_write_then_read_inject_count() -> None:
    statusline.write_inject_count("sess-abc", 4)
    assert statusline.read_inject_count("sess-abc") == 4


def test_read_inject_count_missing_is_none() -> None:
    assert statusline.read_inject_count("never-written") is None
    assert statusline.read_inject_count(None) is None


def test_inject_count_session_id_is_sanitized() -> None:
    """A path-traversal session id must stay inside the sessions dir; the
    same sanitization on write + read makes it round-trip."""
    statusline.write_inject_count("../../evil", 9)
    assert statusline.read_inject_count("../../evil") == 9


# --- render (top-level, never raises) -------------------------------------


def test_render_offline_when_daemon_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(statusline, "probe_health", lambda *a, **k: None)
    assert statusline.render('{"session_id": "x"}') == "mnemo offline"


def test_render_healthy_with_inject(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(statusline, "probe_health", lambda *a, **k: {"node_count": 100})
    statusline.write_inject_count("sess-1", 2)
    assert statusline.render('{"session_id": "sess-1"}') == "mnemo 100 up2"


def test_render_never_raises_on_garbage_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(statusline, "probe_health", lambda *a, **k: None)
    assert statusline.render("not json at all") == "mnemo offline"
    assert statusline.render("") == "mnemo offline"


# --- CLI wrapper ----------------------------------------------------------


def test_statusline_cli_prints_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mnemo.statusline.probe_health", lambda *a, **k: {"node_count": 7})
    runner = CliRunner()
    result = runner.invoke(app, ["statusline"], input='{"session_id": "z"}')
    assert result.exit_code == 0
    assert result.stdout.strip() == "mnemo 7"
