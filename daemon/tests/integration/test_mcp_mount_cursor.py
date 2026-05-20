"""Phase 1.2 integration smoke: Cursor 5-minute MCP mount.

Asserts the documented 5-minute-mount flow for Cursor is real:

1. The doc exists where Phase 1's integrations-index will link to it.
2. The first fenced ``json`` block in the doc parses as JSON, has
   Cursor's ``mcpServers`` shape, names ``mnemo``, and invokes the
   ``mcp`` subcommand (the actual mnemo CLI entry-point that spawns
   the stdio MCP server -- ``python -m mnemo.cli mcp`` /
   ``mnemo mcp``).
3. The ``mnemo mcp`` subcommand actually exists (``mnemo mcp --help``
   exits 0). Without this, Cursor spawns the documented command and
   gets nothing back.

What this test deliberately does NOT do:

- It does NOT run the full MCP stdio JSON-RPC handshake. That path
  is exercised by ``daemon/tests/unit/test_mcp_server.py`` against
  the SDK-independent dispatch core (``tool_list`` / ``call_tool``
  / ``build_server``) and live-verified end-to-end in phase 12.
  Running a subprocess + anyio stdio loop here adds Windows
  fragility (the same kind that disabled
  ``test_daemon_lifecycle.py``) for coverage we already have.

The Phase 0 contract test (``test_mcp_tool_surface_contract.py``)
guarantees the tool surface stays stable across versions; this test
guarantees the Cursor-mount doc stays in sync with that surface.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parents[3] / "docs" / "integrations" / "cursor.md"


def _extract_first_json_block(text: str) -> dict:
    """Pull the first fenced ``json`` block out of a markdown doc.

    Cursor's mcp.json schema example is the one block users will copy
    verbatim; if it doesn't parse, the mount doesn't work.
    """
    m = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if m is None:
        raise AssertionError(
            "no fenced ```json block found in docs/integrations/cursor.md; "
            "the 5-minute-mount flow needs at least one copy-pasteable "
            "Cursor mcp.json example."
        )
    return json.loads(m.group(1))


def test_cursor_doc_exists() -> None:
    """The Phase 1.2 deliverable: the cursor.md mount guide is in
    docs/integrations/ so Task 1.4 (integrations index) can link to
    it."""
    assert DOC.is_file(), f"docs/integrations/cursor.md not found at {DOC}"


def test_cursor_mcp_config_block_parses_and_invokes_mnemo_mcp() -> None:
    """The first fenced ``json`` block in cursor.md must be Cursor's
    ``mcp.json`` shape, name the ``mnemo`` server, and invoke the
    ``mcp`` subcommand of the mnemo CLI.

    Wrong subcommand here is the silent-failure mode for the whole
    integration: Cursor spawns whatever the doc says and discovers
    the surface is empty.
    """
    cfg = _extract_first_json_block(DOC.read_text(encoding="utf-8"))
    assert "mcpServers" in cfg, (
        f"Cursor's mcp.json requires top-level 'mcpServers' key; got {sorted(cfg)}"
    )
    servers = cfg["mcpServers"]
    assert isinstance(servers, dict), (
        f"'mcpServers' must be an object; got {type(servers).__name__}"
    )
    assert "mnemo" in servers, f"missing 'mnemo' entry under mcpServers: {sorted(servers)}"
    entry = servers["mnemo"]
    assert "command" in entry, f"mnemo entry must define 'command': {entry}"
    args = entry.get("args", [])
    assert "mcp" in args, (
        f"mnemo entry must invoke the 'mcp' subcommand (this is what spawns "
        f"the stdio MCP server). Got args={args!r}."
    )


def test_mnemo_mcp_subcommand_help_runs() -> None:
    """``mnemo mcp --help`` exits 0 -- proves the subcommand Cursor
    spawns actually exists. Catches the silent-failure mode where the
    doc references a typo'd or renamed subcommand."""
    proc = subprocess.run(
        [sys.executable, "-m", "mnemo.cli", "mcp", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, (
        f"`python -m mnemo.cli mcp --help` failed (rc={proc.returncode}). "
        f"This means the 'mcp' subcommand referenced by cursor.md does not "
        f"exist on the mnemo CLI.\nstdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
    )
