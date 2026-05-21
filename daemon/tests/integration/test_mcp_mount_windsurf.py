"""Phase v5.5.0 integration smoke: Windsurf 5-minute MCP mount.

Windsurf's Cascade panel uses the same ``mcpServers`` shape as
Cursor / Claude Desktop.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parents[3] / "docs" / "integrations" / "windsurf.md"


def _extract_first_json_block(text: str) -> dict:
    m = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if m is None:
        raise AssertionError(
            "no fenced ```json block found in docs/integrations/windsurf.md; "
            "the 5-minute-mount flow needs a copy-pasteable mcp_config.json example."
        )
    return json.loads(m.group(1))


def test_windsurf_doc_exists() -> None:
    assert DOC.is_file(), f"docs/integrations/windsurf.md not found at {DOC}"


def test_windsurf_mcp_config_block_parses_and_invokes_mnemo_mcp() -> None:
    cfg = _extract_first_json_block(DOC.read_text(encoding="utf-8"))
    assert "mcpServers" in cfg, (
        f"Windsurf's mcp_config.json requires top-level 'mcpServers'; got {sorted(cfg)}"
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
