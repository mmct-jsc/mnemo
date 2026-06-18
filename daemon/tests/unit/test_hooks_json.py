"""v5.24.0 workstream A -- hooks/hooks.json must match the REAL Claude Code
plugin hook schema.

The pre-v5.24.0 manifest declared hooks inline in plugin.json with a
FICTIONAL ``platforms`` key and a flat ``{command, platforms}`` shape that
Claude Code does not understand -- so the hooks never fired even when the
plugin was (eventually) registered. v5.24.0 moves hooks to the canonical
``hooks/hooks.json`` file in the nested ``{matcher, hooks:[{type, command}]}``
shape, and routes every event through the cross-platform ``mnemo hook
<event>`` CLI (no ``platforms`` selector, no ``.sh``/``.ps1`` split).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"


@pytest.fixture(scope="module")
def hooks() -> dict:
    return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))


def test_hooks_json_exists() -> None:
    assert HOOKS_JSON.is_file(), "v5.24.0 ships hooks/hooks.json (the CC default path)"


def test_hooks_wrapper_and_events(hooks: dict) -> None:
    # Plugin hooks.json uses the {hooks: {...}} wrapper (vs the bare settings form).
    assert "hooks" in hooks, "plugin hooks.json must use the {hooks: {...}} wrapper"
    events = hooks["hooks"]
    for event in ("SessionStart", "UserPromptSubmit", "PostToolUse"):
        assert event in events, f"missing hook event: {event}"


def test_hooks_use_nested_command_shape(hooks: dict) -> None:
    for event, entries in hooks["hooks"].items():
        assert isinstance(entries, list), f"{event}: must be a list"
        assert entries, f"{event}: must be non-empty"
        for entry in entries:
            assert "hooks" in entry, f"{event}: each entry needs a nested 'hooks' list"
            assert isinstance(entry["hooks"], list), f"{event}: hooks must be a list"
            assert entry["hooks"], f"{event}: hooks must be non-empty"
            for h in entry["hooks"]:
                assert h.get("type") == "command", f"{event}: hooks must be type=command"
                assert h.get("command"), f"{event}: missing command string"


def test_hooks_invoke_mnemo_hook_cli(hooks: dict) -> None:
    want = {
        "SessionStart": "mnemo hook session-start",
        "UserPromptSubmit": "mnemo hook user-prompt-submit",
        "PostToolUse": "mnemo hook post-tool-use",
    }
    for event, expected in want.items():
        cmds = [h["command"] for entry in hooks["hooks"][event] for h in entry["hooks"]]
        assert any(expected in c for c in cmds), f"{event}: expected `{expected}`, got {cmds}"


def test_post_tool_use_matches_edits(hooks: dict) -> None:
    # v6.1.0: PostToolUse also fires on Bash so governance can capture verify
    # evidence (the real exit code), and on MultiEdit/NotebookEdit for touched
    # files. Edit + Write (memory-reindex) must still be covered.
    matchers = [entry.get("matcher", "") for entry in hooks["hooks"]["PostToolUse"]]
    tools = {t for m in matchers for t in m.split("|")}
    assert {"Edit", "Write", "Bash"} <= tools, f"PostToolUse matchers: {matchers}"


def test_no_fictional_platforms_key(hooks: dict) -> None:
    """The pre-v5.24.0 ``platforms`` selector is not a real CC hook field;
    its presence was the bug. It must be gone everywhere in hooks.json."""
    assert "platforms" not in json.dumps(hooks)
