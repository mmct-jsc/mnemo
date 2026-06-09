"""Smoke tests for the install scripts (v5.24.0 install model).

We don't execute the scripts (they would mutate the runner's HOME). v5.24.0
drops the obsolete symlink-into-~/.claude/plugins step (modern Claude Code is
marketplace-driven and ignores unregistered directories) and instead:
registers the MCP server, prints the two /plugin commands the user runs inside
Claude Code, and points at `mnemo doctor` to verify the result.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _sh() -> str:
    return (REPO_ROOT / "install.sh").read_text(encoding="utf-8")


def _ps1() -> str:
    return (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")


def test_install_sh_exists_and_strict() -> None:
    text = _sh()
    assert text.startswith("#!"), "install.sh must have a shebang"
    assert "set -euo pipefail" in text


def test_install_ps1_exists_and_strict() -> None:
    text = _ps1()
    # PS 5.1: 'Stop' is hostile to native tools that write progress to stderr
    # (uv, claude), so the installer uses 'Continue' + explicit $LASTEXITCODE
    # checks for native failures (the real robustness signal).
    assert "$ErrorActionPreference" in text
    assert "$LASTEXITCODE" in text


def test_install_scripts_check_python_311() -> None:
    assert "3.11" in _sh()
    assert "3.11" in _ps1()


def test_install_scripts_uv_sync() -> None:
    assert "uv sync" in _sh()
    ps1 = _ps1()
    assert "uv.Source sync" in ps1 or "uv sync" in ps1


def test_install_scripts_no_longer_symlink_into_plugins() -> None:
    """The obsolete model: CC ignores unregistered dirs, so the symlink did
    nothing. Its removal is the heart of the install rewrite."""
    assert ".claude/plugins/mnemo" not in _sh()
    assert ".claude\\plugins\\mnemo" not in _ps1()


def test_install_scripts_register_mcp() -> None:
    assert "claude mcp add mnemo" in _sh()
    assert "claude mcp add mnemo" in _ps1()


def test_install_scripts_print_plugin_commands() -> None:
    for text in (_sh(), _ps1()):
        assert "/plugin marketplace add mmct-jsc/mnemo" in text
        assert "/plugin install mnemo@mnemo" in text


def test_install_scripts_point_at_doctor() -> None:
    assert "mnemo doctor" in _sh()
    assert "mnemo doctor" in _ps1()


def test_install_sh_supports_no_init_flag() -> None:
    assert "--no-init" in _sh()


def test_install_ps1_supports_no_init_flag() -> None:
    assert "$NoInit" in _ps1()
