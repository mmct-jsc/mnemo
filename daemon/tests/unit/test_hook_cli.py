"""v5.24.0 workstream A -- the `mnemo hook <event>` CLI subcommands.

These replace the 6 hooks/*.sh + *.ps1 scripts with cross-platform,
unit-testable Python entrypoints invoked from hooks/hooks.json. The
output contract (verified against the CC plugin hook-development
reference: examples/load-context.sh injects context via raw stdout +
exit 0): each hook prints to stdout and exits 0, and FAILS OPEN (exit 0,
no output) when the store or retrieval is unavailable so a missing daemon
never blocks a session.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnemo.cli import app
from tests.conftest import FakeEmbedder


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _sandbox(isolated_mnemo_home: Path) -> Path:
    return isolated_mnemo_home


def _seed_memory(parent: Path) -> Path:
    src = parent / "memory"
    src.mkdir(parents=True, exist_ok=True)
    (src / "feedback_x.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: rule-x
            description: a rule about retries
            type: feedback
            ---
            Always retry three times.
            """
        ),
        encoding="utf-8",
    )
    return src


# --- session-start --------------------------------------------------------


def test_hook_session_start_emits_memory_map(runner: CliRunner, tmp_path: Path) -> None:
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex", "--no-embed"])
    result = runner.invoke(app, ["hook", "session-start"])
    assert result.exit_code == 0, result.stdout
    assert "mnemo" in result.stdout.lower()
    # Names the recall entrypoint so the model/user knows how to use it.
    assert "mnemo-query" in result.stdout or "mnemo_query" in result.stdout


def test_hook_session_start_fails_open_on_store_error(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> None:
        raise RuntimeError("db gone")

    monkeypatch.setattr("mnemo.cli._open_store", boom)
    result = runner.invoke(app, ["hook", "session-start"])
    assert result.exit_code == 0, "a broken store must never block the session"
    assert result.stdout.strip() == "", "fail open: emit nothing on error"


def test_hook_session_start_emits_json_with_user_visible_banner(
    runner: CliRunner, tmp_path: Path
) -> None:
    """v5.25.0: emit a JSON object so the HUMAN sees a one-line banner
    (top-level ``systemMessage``) while the MODEL still gets the memory
    map (``hookSpecificOutput.additionalContext``)."""
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex", "--no-embed"])
    result = runner.invoke(app, ["hook", "session-start"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)  # must be valid JSON
    # user-visible banner
    assert "mnemo" in payload["systemMessage"].lower()
    assert "/mnemo-query" in payload["systemMessage"]
    # model-only context, unchanged
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart"
    assert "memory map" in hso["additionalContext"].lower()


def test_hook_session_start_banner_opt_out(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MNEMO_NO_SESSION_BANNER=1 suppresses the user-visible banner but
    keeps the model context (opt-out, never spammy)."""
    monkeypatch.setenv("MNEMO_NO_SESSION_BANNER", "1")
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex", "--no-embed"])
    result = runner.invoke(app, ["hook", "session-start"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert not payload.get("systemMessage"), "banner must be suppressed"
    assert payload["hookSpecificOutput"]["additionalContext"], "context stays"


# --- user-prompt-submit ---------------------------------------------------


def test_hook_user_prompt_submit_emits_citations(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex"])
    result = runner.invoke(
        app, ["hook", "user-prompt-submit"], input='{"prompt": "the retry rule"}'
    )
    assert result.exit_code == 0, result.stdout
    assert "[mnemo:" in result.stdout, "must inject cited memory as context"


def test_hook_user_prompt_submit_accepts_user_prompt_field(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CC hook-development reference names the field ``user_prompt``;
    older builds send ``prompt``. Accept both so the hook is robust."""
    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex"])
    result = runner.invoke(
        app, ["hook", "user-prompt-submit"], input='{"user_prompt": "the retry rule"}'
    )
    assert result.exit_code == 0, result.stdout
    assert "[mnemo:" in result.stdout


def test_hook_user_prompt_submit_empty_is_noop(runner: CliRunner) -> None:
    result = runner.invoke(app, ["hook", "user-prompt-submit"], input="{}")
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_hook_user_prompt_submit_bad_json_fails_open(runner: CliRunner) -> None:
    result = runner.invoke(app, ["hook", "user-prompt-submit"], input="not json at all")
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


# --- post-tool-use --------------------------------------------------------


def test_hook_post_tool_use_triggers_reindex_for_memory_path(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("mnemo.cli._spawn_background_reindex", lambda: calls.append("reindex"))
    payload = json.dumps({"tool_input": {"file_path": "/x/memory/note.md"}})
    result = runner.invoke(app, ["hook", "post-tool-use"], input=payload)
    assert result.exit_code == 0
    assert calls == ["reindex"], "a memory-shaped edit should trigger a background reindex"


def test_hook_post_tool_use_triggers_for_claude_md(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("mnemo.cli._spawn_background_reindex", lambda: calls.append("reindex"))
    payload = json.dumps({"tool_input": {"file_path": "/repo/CLAUDE.md"}})
    result = runner.invoke(app, ["hook", "post-tool-use"], input=payload)
    assert result.exit_code == 0
    assert calls == ["reindex"]


def test_hook_post_tool_use_skips_non_memory_path(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("mnemo.cli._spawn_background_reindex", lambda: calls.append("reindex"))
    payload = json.dumps({"tool_input": {"file_path": "/x/src/main.py"}})
    result = runner.invoke(app, ["hook", "post-tool-use"], input=payload)
    assert result.exit_code == 0
    assert calls == [], "a non-memory edit must NOT trigger a reindex"


def test_hook_post_tool_use_bad_json_fails_open(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("mnemo.cli._spawn_background_reindex", lambda: calls.append("reindex"))
    result = runner.invoke(app, ["hook", "post-tool-use"], input="not json")
    assert result.exit_code == 0
    assert calls == []
