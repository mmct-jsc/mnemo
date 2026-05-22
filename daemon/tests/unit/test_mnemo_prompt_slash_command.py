"""v5.8.0 -- ``/mnemo-prompt`` slash command + ``mnemo query --exclude-local-only``.

The v5 prompt-architect skill is wired into the dock + the MCP
``mnemo_run_skill`` path. v5.8.0 adds a third surface: the Claude
Code slash command ``/mnemo-prompt`` so users can architect a
paste-ready prompt from inside Claude Code without leaving for the
mnemo UI.

Contract this test file locks:

1. ``commands/mnemo-prompt.md`` exists with valid frontmatter
   (description + argument-hint).
2. The slash command body references the ``mnemo:prompt-architect``
   skill so future readers (and Claude Code) know the workflow it
   triggers.
3. The slash command invokes ``mnemo query`` with
   ``--exclude-local-only`` (the prompt is paste-bound to a
   foreign LLM; local-only nodes must never reach the output).
4. ``mnemo query --exclude-local-only`` is accepted by the CLI
   (the slash command can't reference a flag the CLI doesn't have).
5. The plugin-manifest tests' ``expected_stems`` set includes
   ``mnemo-prompt`` so the manifest check stays in sync.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMANDS_DIR = REPO_ROOT / "commands"
COMMAND_FILE = COMMANDS_DIR / "mnemo-prompt.md"

# Strips ANSI color escape sequences. Typer's --help on Linux / macOS
# colorizes individual tokens (renders ``--exclude-local-only`` as
# ``\x1b[1;36m--\x1b[0mexclude\x1b[0m-local-only\x1b[0m``), so a naive
# ``"--exclude-local-only" in stdout`` check fails there even though
# the flag IS in the help output. Windows didn't see this in CI
# because typer detects non-TTY differently on win32 and skips color.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def test_mnemo_prompt_command_exists() -> None:
    """The new slash command's markdown file is in commands/."""
    assert COMMAND_FILE.is_file(), (
        f"commands/mnemo-prompt.md not found at {COMMAND_FILE}. "
        f"v5.8.0 ships this as a Claude Code slash command that "
        f"invokes the v5 prompt-architect skill."
    )


def test_mnemo_prompt_command_has_frontmatter() -> None:
    """Same frontmatter contract as every other slash command."""
    text = COMMAND_FILE.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "missing frontmatter open"
    header = text.split("---\n")[1]
    assert "description:" in header, "frontmatter missing description"
    assert "argument-hint:" in header, "frontmatter missing argument-hint"


def test_mnemo_prompt_command_references_prompt_architect_skill() -> None:
    """The command body must name the underlying skill so future
    readers can find the workflow definition + so the skill's
    contract changes ripple through with a docs grep."""
    text = COMMAND_FILE.read_text(encoding="utf-8")
    assert "mnemo-prompt-architect" in text or "mnemo:prompt-architect" in text, (
        "commands/mnemo-prompt.md must reference the "
        "'mnemo-prompt-architect' skill so the workflow definition "
        "is discoverable from the command."
    )


def test_mnemo_prompt_command_excludes_local_only() -> None:
    """The architected prompt is paste-bound to a foreign LLM; the
    slash command MUST invoke retrieval with the local-only filter
    so confidential nodes never reach the paste-clipboard."""
    text = COMMAND_FILE.read_text(encoding="utf-8")
    assert "--exclude-local-only" in text or "exclude_local_only" in text, (
        "commands/mnemo-prompt.md must invoke retrieval with "
        "--exclude-local-only (or exclude_local_only=true via "
        "MCP). Without this, local_only-flagged nodes can leak into "
        "the paste-ready output."
    )


def test_mnemo_query_cli_accepts_exclude_local_only_flag() -> None:
    """``mnemo query --exclude-local-only`` must be a real CLI flag.
    The slash command references it; if the flag doesn't exist the
    slash command's invocation fails silently with a typer
    error message instead of returning hits."""
    # NO_COLOR=1 + TERM=dumb suppresses typer's per-token colorization,
    # but we strip ANSI defensively too in case the subprocess shell
    # injects color anyway (Git-Bash on Windows ignores NO_COLOR in some
    # configs).
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    proc = subprocess.run(
        [sys.executable, "-m", "mnemo.cli", "query", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, (
        f"`mnemo query --help` failed (rc={proc.returncode}).\nstderr: {proc.stderr!r}"
    )
    plain = _ANSI_RE.sub("", proc.stdout)
    assert "--exclude-local-only" in plain, (
        "mnemo query CLI missing --exclude-local-only flag. The "
        "v5.8.0 /mnemo-prompt slash command references this flag; "
        "without it, the retrieval call leaks local_only nodes into "
        "the paste-bound output.\n"
        f"stdout (ANSI-stripped):\n{plain}"
    )
