"""v5.24.0 workstream A -- `mnemo doctor`, the loud end-to-end install verifier.

Replaces the silent fail-open of the old install path with an actionable
[ok]/[FAIL]/[?] checklist. Each check is a PURE function (deps injected) so it
is unit-testable against a synthetic environment; the CLI command renders the
results and exits nonzero if any REQUIRED check fails.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnemo import doctor
from mnemo.cli import app
from mnemo.doctor import CheckResult


def test_check_mnemo_on_path_found() -> None:
    r = doctor.check_mnemo_on_path(which=lambda _: "/usr/bin/mnemo")
    assert r.ok is True
    assert "mnemo" in r.detail


def test_check_mnemo_on_path_missing() -> None:
    r = doctor.check_mnemo_on_path(which=lambda _: None)
    assert r.ok is False
    assert r.hint


def test_check_index_empty_vs_populated() -> None:
    assert doctor.check_index(0).ok is False
    assert doctor.check_index(5).ok is True


def test_check_daemon_up_and_down() -> None:
    up = doctor.check_daemon(probe=lambda: (True, "5.24.0"))
    assert up.ok is True
    assert "5.24.0" in up.detail
    down = doctor.check_daemon(probe=lambda: (False, None))
    assert down.ok is False
    assert down.required is False  # hooks + MCP work without the daemon


def test_check_mcp_registered_states() -> None:
    assert doctor.check_mcp_registered("mnemo: mnemo mcp").ok is True
    assert doctor.check_mcp_registered("other-server: x").ok is False
    unknown = doctor.check_mcp_registered(None)
    assert unknown.ok is None
    assert unknown.required is False


def test_check_plugin_registered_true(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"mnemo@mnemo": True}}), encoding="utf-8"
    )
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"mnemo@mnemo": [{"scope": "user"}]}}),
        encoding="utf-8",
    )
    assert doctor.check_plugin_registered(tmp_path).ok is True


def test_check_plugin_registered_false_when_absent(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"superpowers@superpowers-dev": True}}),
        encoding="utf-8",
    )
    r = doctor.check_plugin_registered(tmp_path)
    assert r.ok is False
    assert "/plugin" in r.hint


def test_check_plugin_registered_partial_is_not_ok(tmp_path: Path) -> None:
    # enabled in settings but absent from installed_plugins -> not fully wired.
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"mnemo@mnemo": True}}), encoding="utf-8"
    )
    assert doctor.check_plugin_registered(tmp_path).ok is False


def test_render_exit_code_required_failure() -> None:
    results = [
        CheckResult("a", True, "fine"),
        CheckResult("b", False, "broken", hint="fix b", required=True),
    ]
    text, code = doctor.render(results)
    assert code == 1
    assert "fix b" in text


def test_render_nonrequired_failure_is_zero() -> None:
    results = [
        CheckResult("a", True, "fine"),
        CheckResult("b", False, "warn", required=False),
    ]
    _, code = doctor.render(results)
    assert code == 0


def test_render_all_ok_zero() -> None:
    _, code = doctor.render([CheckResult("a", True, "fine")])
    assert code == 0


def test_doctor_command_exits_nonzero_on_required_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        doctor,
        "gather",
        lambda: [
            CheckResult(
                "plugin registered", False, "not registered", hint="run /plugin ...", required=True
            )
        ],
    )
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1, result.stdout
    assert "FAIL" in result.stdout
    assert "/plugin" in result.stdout


def test_doctor_command_exits_zero_when_all_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "gather", lambda: [CheckResult("x", True, "fine")])
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
