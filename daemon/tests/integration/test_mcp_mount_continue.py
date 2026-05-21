"""Phase v5.5.0 integration smoke: Continue 5-minute MCP mount.

Continue's config shape is different from Cursor / Claude Desktop:
servers live under ``experimental.modelContextProtocolServers`` as a
LIST, and each entry wraps its command in a ``transport`` object.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parents[3] / "docs" / "integrations" / "continue.md"


def _extract_first_json_block(text: str) -> dict:
    m = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if m is None:
        raise AssertionError(
            "no fenced ```json block found in docs/integrations/continue.md; "
            "the 5-minute-mount flow needs a copy-pasteable config.json example."
        )
    return json.loads(m.group(1))


def test_continue_doc_exists() -> None:
    assert DOC.is_file(), f"docs/integrations/continue.md not found at {DOC}"


def test_continue_mcp_config_block_parses_and_invokes_mnemo_mcp() -> None:
    cfg = _extract_first_json_block(DOC.read_text(encoding="utf-8"))
    assert "experimental" in cfg, (
        f"Continue's config.json puts MCP servers under 'experimental'; got {sorted(cfg)}"
    )
    exp = cfg["experimental"]
    assert isinstance(exp, dict), f"'experimental' must be an object; got {type(exp).__name__}"
    servers = exp.get("modelContextProtocolServers")
    assert isinstance(servers, list), (
        f"'experimental.modelContextProtocolServers' must be a list; got {type(servers).__name__}"
    )
    assert servers, "modelContextProtocolServers list is empty"
    found = False
    for entry in servers:
        transport = entry.get("transport", {})
        if transport.get("command") != "mnemo":
            continue
        args = transport.get("args", [])
        assert "mcp" in args, f"mnemo entry must invoke the 'mcp' subcommand. Got args={args!r}."
        found = True
        break
    assert found, (
        f"no entry with transport.command == 'mnemo' found in "
        f"modelContextProtocolServers: {servers}"
    )


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
