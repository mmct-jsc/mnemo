"""Plugin scaffold tests: manifest validity, referenced files exist."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = REPO_ROOT / ".claude-plugin"
HOOKS_DIR = REPO_ROOT / "hooks"
COMMANDS_DIR = REPO_ROOT / "commands"


@pytest.fixture(scope="module")
def manifest() -> dict:
    p = PLUGIN_DIR / "plugin.json"
    return json.loads(p.read_text(encoding="utf-8"))


def test_manifest_exists() -> None:
    assert (PLUGIN_DIR / "plugin.json").is_file()


def test_manifest_required_keys(manifest: dict) -> None:
    for key in ("name", "version", "description", "license", "hooks"):
        assert key in manifest, f"missing key: {key}"
    assert manifest["name"] == "mnemo"
    assert manifest["license"] == "MIT"


def test_manifest_hooks_reference_existing_files(manifest: dict) -> None:
    for event, entries in manifest["hooks"].items():
        for entry in entries:
            cmd_path = REPO_ROOT / entry["command"]
            assert cmd_path.is_file(), f"{event}: {cmd_path} missing"


def test_manifest_hooks_have_platform_field(manifest: dict) -> None:
    for entries in manifest["hooks"].values():
        for entry in entries:
            assert "platforms" in entry, f"hook entry missing platforms: {entry}"
            assert isinstance(entry["platforms"], list)
            assert all(p in {"linux", "darwin", "win32"} for p in entry["platforms"])


def test_each_event_has_unix_and_windows(manifest: dict) -> None:
    for event, entries in manifest["hooks"].items():
        platforms = set()
        for entry in entries:
            platforms.update(entry["platforms"])
        assert {"linux", "darwin"}.issubset(platforms), f"{event}: missing unix"
        assert "win32" in platforms, f"{event}: missing windows"


def test_post_tool_use_has_matcher(manifest: dict) -> None:
    for entry in manifest["hooks"]["PostToolUse"]:
        assert "matcher" in entry
        assert entry["matcher"] == "Edit|Write"


def test_all_hook_scripts_present() -> None:
    expected = {
        "session-start.sh",
        "session-start.ps1",
        "user-prompt-submit.sh",
        "user-prompt-submit.ps1",
        "post-tool-use.sh",
        "post-tool-use.ps1",
    }
    actual = {p.name for p in HOOKS_DIR.iterdir() if p.is_file()}
    assert expected.issubset(actual), f"missing hook scripts: {expected - actual}"


def test_all_slash_commands_present() -> None:
    expected_stems = {
        "mnemo-query",
        "mnemo-add",
        "mnemo-reindex",
        "mnemo-ui",
        "mnemo-status",
        "mnemo-hooks",
        "mnemo-show",
    }
    actual = {p.stem for p in COMMANDS_DIR.glob("*.md")}
    assert expected_stems.issubset(actual), f"missing commands: {expected_stems - actual}"


def test_command_files_have_frontmatter() -> None:
    for cmd_file in COMMANDS_DIR.glob("*.md"):
        text = cmd_file.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{cmd_file.name}: no frontmatter"
        assert "description:" in text.split("---\n")[1], f"{cmd_file.name}: missing description"


def test_unix_hook_scripts_have_shebang() -> None:
    for sh in HOOKS_DIR.glob("*.sh"):
        first_line = sh.read_text(encoding="utf-8").splitlines()[0]
        assert first_line.startswith("#!"), f"{sh.name}: missing shebang"
