"""Phase 1.3 integration smoke: OpenAI Agents SDK 5-minute MCP mount.

Asserts the documented mount flow for the OpenAI Agents SDK (Python
and TypeScript) is real:

1. The doc exists where Phase 1's integrations index will link to it.
2. The first fenced ``python`` block references ``MCPServerStdio``,
   the ``"mnemo"`` command literal, and the ``"mcp"`` subcommand.
3. The first fenced ``ts`` (or ``typescript``) block does the same
   for the JavaScript SDK.
4. ``python -m mnemo.cli mcp --help`` exits 0 (the subcommand the
   agent will spawn actually exists).

Why no live subprocess of the OpenAI Agents SDK: the daemon does NOT
take ``openai-agents`` as a dependency. Running the SDK in CI would
require installing it on every test box and (more importantly) would
push us to bind a network mock or skip-when-no-OPENAI_API_KEY. The
combination of the Phase 0 contract test, the agent_tools dispatch
tests (``tests/unit/test_mcp_server.py``), and these doc-shape
assertions covers the wire contract well enough that the runtime
binding is verified by users actually mounting the doc.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parents[3] / "docs" / "integrations" / "openai-agents-sdk.md"


def _extract_first_fenced_block(text: str, langs: tuple[str, ...]) -> str:
    """Pull the first fenced ```<lang>``` block matching any of ``langs``."""
    for lang in langs:
        pattern = rf"```{re.escape(lang)}\s*\n(.*?)\n```"
        m = re.search(pattern, text, re.DOTALL)
        if m is not None:
            return m.group(1)
    raise AssertionError(
        f"no fenced block matching any of {langs!r} found in docs/integrations/openai-agents-sdk.md"
    )


def test_openai_agents_sdk_doc_exists() -> None:
    """Phase 1.3 deliverable: the SDK mount guide is in
    docs/integrations/ so Task 1.4 (integrations index) can link to
    it."""
    assert DOC.is_file(), f"docs/integrations/openai-agents-sdk.md not found at {DOC}"


def test_python_snippet_invokes_mnemo_mcp() -> None:
    """The first fenced ``python`` block must wire an MCPServerStdio
    pointed at ``mnemo`` + ``mcp``. Wrong identifier here is the
    silent-failure mode -- the agent spawns nothing and the tools
    list is empty."""
    text = DOC.read_text(encoding="utf-8")
    block = _extract_first_fenced_block(text, ("python", "py"))
    assert "MCPServerStdio" in block, (
        f"Python snippet must import / use MCPServerStdio from the openai-agents SDK. Got:\n{block}"
    )
    assert '"mnemo"' in block or "'mnemo'" in block, (
        f"Python snippet must reference the 'mnemo' command literal. Got:\n{block}"
    )
    assert '"mcp"' in block or "'mcp'" in block, (
        f"Python snippet must reference the 'mcp' subcommand literal. Got:\n{block}"
    )


def test_typescript_snippet_invokes_mnemo_mcp() -> None:
    """Same shape for the TS / JS SDK."""
    text = DOC.read_text(encoding="utf-8")
    block = _extract_first_fenced_block(text, ("ts", "typescript", "javascript", "js"))
    assert "MCPServerStdio" in block, (
        f"TS snippet must import / use MCPServerStdio from @openai/agents. Got:\n{block}"
    )
    assert '"mnemo"' in block or "'mnemo'" in block, (
        f"TS snippet must reference the 'mnemo' command literal. Got:\n{block}"
    )
    assert '"mcp"' in block or "'mcp'" in block, (
        f"TS snippet must reference the 'mcp' subcommand literal. Got:\n{block}"
    )


def test_mnemo_mcp_subcommand_help_runs() -> None:
    """Same belt-and-braces as the Cursor test: prove the subcommand
    the SDK will spawn actually exists on this checkout. (Could be
    deduplicated into a conftest helper later; kept inline so the
    test file is self-contained.)"""
    proc = subprocess.run(
        [sys.executable, "-m", "mnemo.cli", "mcp", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, (
        f"`python -m mnemo.cli mcp --help` failed (rc={proc.returncode}). "
        f"This means the 'mcp' subcommand referenced by "
        f"openai-agents-sdk.md does not exist on the mnemo CLI.\n"
        f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
    )
