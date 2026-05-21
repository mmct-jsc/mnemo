"""Phase v5.5.0 integration smoke: Zed 5-minute MCP mount.

Zed's settings.json uses ``context_servers`` (not ``mcpServers``)
and each entry wraps its command in a ``command`` object with
``path`` + ``args``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parents[3] / "docs" / "integrations" / "zed.md"


def _extract_first_json_block(text: str) -> dict:
    m = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if m is None:
        raise AssertionError(
            "no fenced ```json block found in docs/integrations/zed.md; "
            "the 5-minute-mount flow needs a copy-pasteable settings.json example."
        )
    return json.loads(m.group(1))


def test_zed_doc_exists() -> None:
    assert DOC.is_file(), f"docs/integrations/zed.md not found at {DOC}"


def test_zed_mcp_config_block_parses_and_invokes_mnemo_mcp() -> None:
    cfg = _extract_first_json_block(DOC.read_text(encoding="utf-8"))
    assert "context_servers" in cfg, (
        f"Zed's settings.json puts MCP servers under 'context_servers'; got {sorted(cfg)}"
    )
    servers = cfg["context_servers"]
    assert isinstance(servers, dict), (
        f"'context_servers' must be an object; got {type(servers).__name__}"
    )
    assert "mnemo" in servers, f"missing 'mnemo' entry under context_servers: {sorted(servers)}"
    entry = servers["mnemo"]
    cmd = entry.get("command", {})
    assert isinstance(cmd, dict), (
        f"Zed wraps each entry's command in a 'command' object with path/args; "
        f"got {type(cmd).__name__}"
    )
    assert cmd.get("path"), f"mnemo entry must define command.path: {entry}"
    args = cmd.get("args", [])
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
