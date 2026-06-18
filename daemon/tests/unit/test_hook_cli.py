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


def test_hook_session_start_offers_indexing_for_unindexed_project(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v5.26.0 (user spec): when the IDE's project is not indexed, the
    session banner offers indexing (human-visible) and the model context
    tells Claude it MAY offer `mnemo source add` -- never auto-index."""
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex", "--no-embed"])
    proj = tmp_path / "fresh-proj"
    (proj / ".git").mkdir(parents=True)
    result = runner.invoke(app, ["hook", "session-start"], input=json.dumps({"cwd": str(proj)}))
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "not indexed" in payload["systemMessage"]
    assert "mnemo source add" in payload["systemMessage"]
    ctx_text = payload["hookSpecificOutput"]["additionalContext"]
    assert "mnemo source add" in ctx_text
    assert "user" in ctx_text.lower(), "indexing must stay a user decision"


def test_hook_session_start_no_offer_when_indexed(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mnemo.retrieve.resolve_auto_scope", lambda store, cwd: ("K", True))
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex", "--no-embed"])
    proj = tmp_path / "indexed-proj"
    (proj / ".git").mkdir(parents=True)
    result = runner.invoke(app, ["hook", "session-start"], input=json.dumps({"cwd": str(proj)}))
    payload = json.loads(result.stdout)
    assert "not indexed" not in payload["systemMessage"]


def test_hook_session_start_no_offer_for_non_project_dir(runner: CliRunner, tmp_path: Path) -> None:
    """A cwd without .git is not a project -- no nagging in the banner."""
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex", "--no-embed"])
    plain = tmp_path / "just-a-dir"
    plain.mkdir()
    result = runner.invoke(app, ["hook", "session-start"], input=json.dumps({"cwd": str(plain)}))
    payload = json.loads(result.stdout)
    assert "not indexed" not in payload["systemMessage"]


# --- user-prompt-submit ---------------------------------------------------


def test_hook_user_prompt_submit_emits_citations(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pin the daemon away so this exercises the in-process FALLBACK path
    # (without the pin, a live local daemon would answer the query).
    monkeypatch.setattr("mnemo.cli._daemon_query", lambda *a, **k: None)
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
    monkeypatch.setattr("mnemo.cli._daemon_query", lambda *a, **k: None)
    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex"])
    result = runner.invoke(
        app, ["hook", "user-prompt-submit"], input='{"user_prompt": "the retry rule"}'
    )
    assert result.exit_code == 0, result.stdout
    assert "[mnemo:" in result.stdout


def test_hook_user_prompt_submit_emits_active_rules(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v6.1.0: a binding rule surfaces in its own ``## Active rules`` section,
    above the ranked memory pointer (in-process fallback path)."""
    monkeypatch.setattr("mnemo.cli._daemon_query", lambda *a, **k: None)
    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    src = tmp_path / "mem"
    src.mkdir()
    (src / "rule_no_emoji.md").write_text(
        "---\n"
        "name: no-emoji\n"
        "type: rule\n"
        "base: true\n"
        "description: No emojis in code, docs, or commit messages.\n"
        "rule:\n"
        "  id: rule.style.no-emoji\n"
        "  modality: MUST_NOT\n"
        "  enforcement: warn\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex"])
    result = runner.invoke(
        app, ["hook", "user-prompt-submit"], input='{"prompt": "how should I write this function"}'
    )
    assert result.exit_code == 0, result.stdout
    assert "## Active rules (mnemo)" in result.stdout, "binding rules get their own section"
    assert "MUST_NOT" in result.stdout
    assert "[mnemo:" in result.stdout


def test_bash_exit_code_extraction() -> None:
    from mnemo.cli import _bash_exit_code

    assert _bash_exit_code({"tool_response": {"exit_code": 0}}) == 0
    assert _bash_exit_code({"tool_response": {"exitCode": 2}}) == 2
    assert _bash_exit_code({"tool_response": {"returnCode": 1}}) == 1
    assert _bash_exit_code({"tool_response": {}}) is None
    assert _bash_exit_code({"tool_response": "a plain string output"}) is None
    assert _bash_exit_code({}) is None


def test_response_has_error_flag() -> None:
    from mnemo.cli import _response_has_error

    assert _response_has_error({"tool_response": {"is_error": True}}) is True
    assert _response_has_error({"tool_response": {"interrupted": True}}) is True
    assert _response_has_error({"tool_response": {"exit_code": 0}}) is False
    assert _response_has_error({"tool_response": {}}) is False
    assert _response_has_error({}) is False


def test_post_tool_use_captures_verify_evidence(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v6.1.0 G3: a Bash run matching a rule's verify command, exiting as
    expected, stamps SATISFIED evidence -- captured from the real result."""
    import json

    from mnemo.cli import _open_store

    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    src = tmp_path / "mem"
    src.mkdir()
    (src / "rule_ruff.md").write_text(
        "---\n"
        "name: ruff-before-commit\n"
        "type: rule\n"
        "base: true\n"
        "description: ruff must pass before commit.\n"
        "rule:\n"
        "  id: rule.verify.ruff\n"
        "  modality: MUST\n"
        "  enforcement: block\n"
        "  requires_step: verify\n"
        "  applies_to:\n"
        "    tool: ['Bash']\n"
        "    tool_arg_match: 'git commit'\n"
        "  verify:\n"
        "    command: 'ruff check'\n"
        "    expect_exit: 0\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex"])

    ok = json.dumps(
        {
            "session_id": "S",
            "tool_name": "Bash",
            "tool_input": {"command": "ruff check ."},
            "tool_response": {"exit_code": 0},
            "cwd": str(tmp_path),
        }
    )
    res = runner.invoke(app, ["hook", "post-tool-use"], input=ok)
    assert res.exit_code == 0
    s = _open_store()
    try:
        assert s.gate_satisfied("S", "rule.verify.ruff", "verify") is True
    finally:
        s.close()


def test_post_tool_use_failed_verify_does_not_satisfy(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from mnemo.cli import _open_store

    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    src = tmp_path / "mem"
    src.mkdir()
    (src / "rule_ruff.md").write_text(
        "---\nname: r\ntype: rule\nbase: true\ndescription: d\n"
        "rule:\n  id: rule.verify.ruff\n  verify:\n    command: 'ruff check'\n    expect_exit: 0\n---\nb\n",
        encoding="utf-8",
    )
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex"])
    bad = json.dumps(
        {
            "session_id": "S",
            "tool_name": "Bash",
            "tool_input": {"command": "ruff check ."},
            "tool_response": {"exit_code": 1},
            "cwd": str(tmp_path),
        }
    )
    runner.invoke(app, ["hook", "post-tool-use"], input=bad)
    s = _open_store()
    try:
        assert s.gate_satisfied("S", "rule.verify.ruff", "verify") is False
    finally:
        s.close()


def test_post_tool_use_records_touched_file(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from mnemo.cli import _open_store

    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    edit = json.dumps(
        {
            "session_id": "S",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/repo/app.py"},
            "cwd": str(tmp_path),
        }
    )
    runner.invoke(app, ["hook", "post-tool-use"], input=edit)
    s = _open_store()
    try:
        assert "/repo/app.py" in s.governance_touched_files("S")
    finally:
        s.close()


def test_hook_user_prompt_submit_empty_is_noop(runner: CliRunner) -> None:
    result = runner.invoke(app, ["hook", "user-prompt-submit"], input="{}")
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_hook_user_prompt_submit_bad_json_fails_open(runner: CliRunner) -> None:
    result = runner.invoke(app, ["hook", "user-prompt-submit"], input="not json at all")
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_hook_user_prompt_submit_records_inject_count(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v5.25.0: the hook records the injection size keyed by session_id so
    `mnemo statusline` can show up{N}."""
    from mnemo import statusline

    monkeypatch.setattr("mnemo.cli._daemon_query", lambda *a, **k: None)
    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex"])
    result = runner.invoke(
        app,
        ["hook", "user-prompt-submit"],
        input='{"prompt": "the retry rule", "session_id": "sess-xyz"}',
    )
    assert result.exit_code == 0
    assert (statusline.read_inject_count("sess-xyz") or 0) >= 1


# --- post-tool-use --------------------------------------------------------


def test_hook_post_tool_use_triggers_reindex_for_memory_path(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("mnemo.cli._spawn_background_reindex", lambda: calls.append("reindex"))
    # Daemon down -> the legacy subprocess fallback must still fire.
    monkeypatch.setattr("mnemo.cli._nudge_daemon_reindex", lambda: False)
    payload = json.dumps({"tool_input": {"file_path": "/x/memory/note.md"}})
    result = runner.invoke(app, ["hook", "post-tool-use"], input=payload)
    assert result.exit_code == 0
    assert calls == ["reindex"], "a memory-shaped edit should trigger a background reindex"


def test_hook_post_tool_use_triggers_for_claude_md(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("mnemo.cli._spawn_background_reindex", lambda: calls.append("reindex"))
    monkeypatch.setattr("mnemo.cli._nudge_daemon_reindex", lambda: False)
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


# --- v5.25.0 step 7: hooks delegate to the warm daemon ---------------------
#
# Live diagnosis (2026-06-10): the per-prompt hook loaded MiniLM in a fresh
# process WITH a HuggingFace Hub round-trip (~50s rate-limited), and the
# post-tool-use hook piled up unguarded full-corpus reindex subprocesses
# (4 concurrent observed). Daemon-first fixes both: the daemon has the warm
# model, and POST /v1/reindex has server-side single-flight (409 when busy).


def test_hook_user_prompt_submit_uses_daemon_when_up(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the daemon answers /v1/query the hook must NOT build an
    in-process Embedder (that path cost ~50s/prompt under HF rate limits)."""
    from mnemo import statusline

    rows = [
        {
            "citation": "[mnemo:abc123]",
            "type": "memory_feedback",
            "name": "rule-x",
            "description": "a rule about retries",
        }
    ]
    monkeypatch.setattr(
        "mnemo.cli._daemon_query",
        lambda prompt, **kw: {"hits": rows, "intent_tags": ["recall"]},
    )

    def _no_embedder(*a: object, **kw: object) -> object:
        raise AssertionError("daemon answered; in-process Embedder must not be built")

    monkeypatch.setattr("mnemo.cli.Embedder", _no_embedder)
    result = runner.invoke(
        app,
        ["hook", "user-prompt-submit"],
        input='{"prompt": "the retry rule", "session_id": "sess-daemon"}',
    )
    assert result.exit_code == 0, result.stdout
    assert "[mnemo:abc123]" in result.stdout
    assert "intent: recall" in result.stdout
    assert statusline.read_inject_count("sess-daemon") == 1


def test_hook_post_tool_use_prefers_daemon_nudge(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daemon up -> nudge it (server-side single-flight); spawn NOTHING."""
    calls: list[str] = []
    monkeypatch.setattr("mnemo.cli._spawn_background_reindex", lambda: calls.append("spawn"))
    monkeypatch.setattr("mnemo.cli._nudge_daemon_reindex", lambda: True)
    payload = json.dumps({"tool_input": {"file_path": "/x/memory/note.md"}})
    result = runner.invoke(app, ["hook", "post-tool-use"], input=payload)
    assert result.exit_code == 0
    assert calls == [], "daemon accepted the nudge; no subprocess may spawn"


def test_nudge_daemon_reindex_accepts_fast_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import contextlib

    from mnemo import cli

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: contextlib.nullcontext(object())
    )
    assert cli._nudge_daemon_reindex() is True


def test_nudge_daemon_reindex_409_means_already_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error

    from mnemo import cli

    def _busy(req: object, timeout: float | None = None) -> object:
        raise urllib.error.HTTPError("http://127.0.0.1:7373/v1/reindex", 409, "busy", None, None)

    monkeypatch.setattr("urllib.request.urlopen", _busy)
    assert cli._nudge_daemon_reindex() is True, "409 = a reindex is already running = satisfied"


def test_nudge_daemon_reindex_timeout_means_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mnemo import cli

    def _slow(req: object, timeout: float | None = None) -> object:
        raise TimeoutError("read timed out")

    monkeypatch.setattr("urllib.request.urlopen", _slow)
    # The endpoint is a sync handler in the daemon's threadpool: a client
    # read-timeout abandons the RESPONSE, not the reindex.
    assert cli._nudge_daemon_reindex() is True


def test_nudge_daemon_reindex_refused_means_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error

    from mnemo import cli

    def _down(req: object, timeout: float | None = None) -> object:
        raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))

    monkeypatch.setattr("urllib.request.urlopen", _down)
    assert cli._nudge_daemon_reindex() is False, "daemon down -> caller falls back to subprocess"


def test_hook_user_prompt_submit_tolerates_utf8_bom(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows shells (PowerShell 5.1 on every pipe) prepend a UTF-8 BOM;
    the hook must strip it rather than silently fail open."""
    rows = [
        {"citation": "[mnemo:bom1]", "type": "memory_feedback", "name": "r", "description": "d"}
    ]
    monkeypatch.setattr(
        "mnemo.cli._daemon_query", lambda prompt, **kw: {"hits": rows, "intent_tags": []}
    )
    result = runner.invoke(
        app,
        ["hook", "user-prompt-submit"],
        input='﻿{"prompt": "the retry rule", "session_id": "bom"}',
    )
    assert result.exit_code == 0
    assert "[mnemo:bom1]" in result.stdout


def test_hook_post_tool_use_tolerates_utf8_bom(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    nudges: list[str] = []

    def _nudge() -> bool:
        nudges.append("nudge")
        return True

    monkeypatch.setattr("mnemo.cli._nudge_daemon_reindex", _nudge)
    monkeypatch.setattr("mnemo.cli._spawn_background_reindex", lambda: None)
    result = runner.invoke(
        app,
        ["hook", "post-tool-use"],
        input='﻿{"tool_input": {"file_path": "/x/memory/note.md"}}',
    )
    assert result.exit_code == 0
    assert nudges == ["nudge"], "a BOM-prefixed payload must still parse + nudge"


def test_hook_user_prompt_submit_tolerates_cp1252_mojibake_bom(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The OBSERVED live reality: with a cp1252 console codepage the BOM
    bytes arrive as the three chars \\xef\\xbb\\xbf, not U+FEFF."""
    rows = [
        {"citation": "[mnemo:moj1]", "type": "memory_feedback", "name": "r", "description": "d"}
    ]
    monkeypatch.setattr(
        "mnemo.cli._daemon_query", lambda prompt, **kw: {"hits": rows, "intent_tags": []}
    )
    result = runner.invoke(
        app,
        ["hook", "user-prompt-submit"],
        input='\xef\xbb\xbf{"prompt": "the retry rule", "session_id": "moj"}',
    )
    assert result.exit_code == 0
    assert "[mnemo:moj1]" in result.stdout


def test_spawn_background_reindex_debounced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bursts of memory edits while the daemon is down must not pile up
    subprocesses (4 concurrent full reindexes were observed live)."""
    import subprocess as sp

    from mnemo import cli

    spawned: list = []
    monkeypatch.setattr(sp, "Popen", lambda *a, **k: spawned.append(a))
    cli._spawn_background_reindex()
    cli._spawn_background_reindex()
    assert len(spawned) == 1, "second nudge within the debounce window must be skipped"
