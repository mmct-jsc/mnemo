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


def test_render_tolerates_utf8_bom(monkeypatch: pytest.MonkeyPatch) -> None:
    """PowerShell 5.1 prepends a BOM to piped payloads; the session_id must
    survive so the inject count still resolves."""
    monkeypatch.setattr(statusline, "probe_health", lambda *a, **k: {"node_count": 100})
    statusline.write_inject_count("bom-sess", 3)
    assert statusline.render('﻿{"session_id": "bom-sess"}') == "mnemo 100 up3"
    # cp1252 console codepage: the BOM bytes arrive as three mojibake chars.
    assert statusline.render('\xef\xbb\xbf{"session_id": "bom-sess"}') == "mnemo 100 up3"


# --- CLI wrapper ----------------------------------------------------------


def test_statusline_cli_prints_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mnemo.statusline.probe_health", lambda *a, **k: {"node_count": 7})
    runner = CliRunner()
    result = runner.invoke(app, ["statusline"], input='{"session_id": "z"}')
    assert result.exit_code == 0
    assert result.stdout.strip() == "mnemo 7"


# --- settings.json wiring (ensure_statusline + statusline-setup) ----------


def test_statusline_is_mnemo() -> None:
    assert statusline.statusline_is_mnemo({"statusLine": {"command": "mnemo statusline"}})
    assert not statusline.statusline_is_mnemo({"statusLine": {"command": "/my/bar.sh"}})
    assert not statusline.statusline_is_mnemo({})


def test_ensure_statusline_adds_when_absent() -> None:
    new, action = statusline.ensure_statusline({"model": "x"})
    assert action == "added"
    assert new["statusLine"]["command"] == "mnemo statusline"
    assert new["model"] == "x", "other keys preserved"


def test_ensure_statusline_noclobber_other() -> None:
    settings = {"statusLine": {"type": "command", "command": "/my/bar.sh"}}
    new, action = statusline.ensure_statusline(settings)
    assert action == "exists_other"
    assert new["statusLine"]["command"] == "/my/bar.sh", "left untouched"


def test_ensure_statusline_idempotent_for_mnemo() -> None:
    settings = {"statusLine": {"type": "command", "command": "mnemo statusline"}}
    new, action = statusline.ensure_statusline(settings)
    assert action == "exists_mnemo"
    new2, action2 = statusline.ensure_statusline(new)
    assert action2 == "exists_mnemo"


def test_statusline_setup_cli_writes_settings(tmp_path: Path) -> None:
    import json as _json

    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(app, ["statusline-setup", "--settings", str(settings_path)])
    assert result.exit_code == 0, result.stdout
    data = _json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["statusLine"]["command"] == "mnemo statusline"
    # idempotent second run
    result2 = runner.invoke(app, ["statusline-setup", "--settings", str(settings_path)])
    assert result2.exit_code == 0
    assert _json.loads(settings_path.read_text(encoding="utf-8")) == data


def test_statusline_setup_cli_noclobber(tmp_path: Path) -> None:
    import json as _json

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        _json.dumps({"statusLine": {"command": "/my/bar.sh"}}), encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["statusline-setup", "--settings", str(settings_path)])
    assert result.exit_code == 0
    data = _json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["statusLine"]["command"] == "/my/bar.sh", "must not clobber a user statusline"
