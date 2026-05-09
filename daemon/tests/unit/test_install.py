"""Smoke tests for install scripts: existence, structure, key invariants.

We don't execute the scripts (they would mutate the runner's HOME). The
real lifecycle test is manual: ``./install.sh`` on a fresh clone.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_install_sh_exists() -> None:
    p = REPO_ROOT / "install.sh"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert text.startswith("#!"), "install.sh must have a shebang"
    assert "set -euo pipefail" in text, "install.sh must error on first failure"


def test_install_ps1_exists() -> None:
    p = REPO_ROOT / "install.ps1"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "$ErrorActionPreference = 'Stop'" in text, "install.ps1 must set ErrorActionPreference"


def test_install_sh_supports_no_init_flag() -> None:
    text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    assert "--no-init" in text


def test_install_ps1_supports_no_init_flag() -> None:
    text = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "$NoInit" in text


def test_install_sh_supports_no_plugin_link_flag() -> None:
    text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    assert "--no-plugin-link" in text


def test_install_ps1_supports_no_plugin_link_flag() -> None:
    text = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "$NoPluginLink" in text


def test_install_scripts_target_uv_sync() -> None:
    sh = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    ps1 = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "uv sync" in sh
    assert "uv.Source sync" in ps1 or "uv sync" in ps1


def test_install_scripts_link_plugin_to_claude_dir() -> None:
    sh = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    ps1 = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    assert ".claude/plugins/mnemo" in sh
    assert ".claude\\plugins\\mnemo" in ps1


def test_install_scripts_check_python_311() -> None:
    sh = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    ps1 = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "3.11" in sh
    assert "3.11" in ps1
