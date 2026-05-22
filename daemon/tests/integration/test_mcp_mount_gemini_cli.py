"""Phase v5.7.0 integration smoke: Gemini CLI 5-minute MCP mount.

Gemini CLI uses the same ``mcpServers`` shape in ``settings.json`` as
Cursor / Claude Desktop / Windsurf. The test asserts the documented
config block parses + invokes the ``mcp`` subcommand, and that the
``mnemo mcp`` subcommand actually exists.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parents[3] / "docs" / "integrations" / "gemini-cli.md"


def _extract_first_json_block(text: str) -> dict:
    m = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if m is None:
        raise AssertionError(
            "no fenced ```json block found in docs/integrations/gemini-cli.md; "
            "the 5-minute-mount flow needs a copy-pasteable settings.json example."
        )
    return json.loads(m.group(1))


def test_gemini_cli_doc_exists() -> None:
    assert DOC.is_file(), f"docs/integrations/gemini-cli.md not found at {DOC}"


def test_gemini_cli_mcp_config_block_parses_and_invokes_mnemo_mcp() -> None:
    cfg = _extract_first_json_block(DOC.read_text(encoding="utf-8"))
    assert "mcpServers" in cfg, (
        f"Gemini CLI's settings.json puts MCP servers under 'mcpServers'; got {sorted(cfg)}"
    )
    servers = cfg["mcpServers"]
    assert isinstance(servers, dict), (
        f"'mcpServers' must be an object; got {type(servers).__name__}"
    )
    assert "mnemo" in servers, f"missing 'mnemo' entry under mcpServers: {sorted(servers)}"
    entry = servers["mnemo"]
    assert "command" in entry, f"mnemo entry must define 'command': {entry}"
    args = entry.get("args", [])
    assert "mcp" in args, f"mnemo entry must invoke the 'mcp' subcommand. Got args={args!r}."


def test_mnemo_mcp_subcommand_help_runs() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "mnemo.cli", "mcp", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, (
        f"`python -m mnemo.cli mcp --help` failed (rc={proc.returncode}).\n"
        f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
    )
