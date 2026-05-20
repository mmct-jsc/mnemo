"""Phase 1.6: snapshot the MCP tool_list() wire shape byte-for-byte.

The Phase 0 contract test (test_mcp_tool_surface_contract.py) catches
rename / removal of tools. The Phase 1.5 risk-tag test catches risk
taxonomy drift. This snapshot catches everything else: a description
reword, an inputSchema field rename, a parameter going from required
to optional, a new field added to the descriptor without docs.

External hosts (Cursor, OpenAI Agents SDK, Continue, ...) consume
this JSON verbatim via the MCP ``tools/list`` handshake. Any byte
change is a wire-contract change and deserves explicit review in the
PR.

Updating procedure
------------------

If a wire-shape change is intentional, regenerate the snapshot::

    cd daemon
    MNEMO_UPDATE_SNAPSHOTS=1 uv run pytest \\
        tests/unit/test_mcp_wire_schema_snapshot.py

Then::

    git diff -- daemon/tests/unit/_snapshots/mcp_tool_list.json

Review every byte. Update docs/integrations/wire-schema.md when the
descriptor SHAPE itself changes (a new field, etc.) rather than just
the contents of an existing one.

The snapshot lives in ``_snapshots/`` under this file's parent so
the test is self-contained and movable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mnemo.mcp_server import tool_list

SNAPSHOT = Path(__file__).parent / "_snapshots" / "mcp_tool_list.json"


def _serialize_live_tool_list() -> str:
    """Deterministic JSON of the live wire shape.

    - Sort tools by name so reordering is not a diff signal (the
      MCP protocol does not promise order; Phase 0 contract test
      asserts the SET, not the sequence).
    - sort_keys=True so dict-key ordering is not a diff signal
      either.
    - indent=2 so the file is human-readable in the PR diff.
    - Trailing newline so editors / git tooling don't fight us.
    """
    tools = sorted(tool_list(), key=lambda t: t["name"])
    return json.dumps(tools, indent=2, sort_keys=True) + "\n"


def test_mcp_tool_list_matches_committed_snapshot() -> None:
    """The MCP wire schema must match daemon/tests/unit/_snapshots/
    mcp_tool_list.json byte-for-byte.

    Failure means external hosts mounting via Phase 1's
    docs/integrations/ guides may see a different surface than
    the docs promised.
    """
    live = _serialize_live_tool_list()

    if os.environ.get("MNEMO_UPDATE_SNAPSHOTS") == "1":
        # Regenerate mode. Write the snapshot, return PASS.
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(live, encoding="utf-8")
        return

    assert SNAPSHOT.is_file(), (
        f"Wire-schema snapshot missing at {SNAPSHOT}.\n"
        "Generate it with:\n"
        "  cd daemon\n"
        "  MNEMO_UPDATE_SNAPSHOTS=1 uv run pytest tests/unit/test_mcp_wire_schema_snapshot.py"
    )

    expected = SNAPSHOT.read_text(encoding="utf-8")

    if live != expected:
        # Help the reviewer locate the drift quickly.
        live_lines = live.splitlines()
        expected_lines = expected.splitlines()
        first_diff: tuple[int, str, str] | None = None
        for i, (a, b) in enumerate(zip(expected_lines, live_lines, strict=False)):
            if a != b:
                first_diff = (i + 1, a, b)
                break
        raise AssertionError(
            "MCP wire schema drift detected: live tool_list() JSON no longer "
            "matches daemon/tests/unit/_snapshots/mcp_tool_list.json.\n\n"
            f"snapshot lines: {len(expected_lines)}; live lines: {len(live_lines)}\n"
            + (
                f"first differing line {first_diff[0]}:\n"
                f"  expected: {first_diff[1]!r}\n"
                f"  live:     {first_diff[2]!r}\n"
                if first_diff
                else ""
            )
            + "\nIf the change is INTENTIONAL, regenerate with:\n"
            "  cd daemon\n"
            "  MNEMO_UPDATE_SNAPSHOTS=1 uv run pytest "
            "tests/unit/test_mcp_wire_schema_snapshot.py\n"
            "Then review the diff in git -- ANY change is a wire-contract change.\n"
            "Update docs/integrations/wire-schema.md if the descriptor SHAPE changed."
        )


def test_snapshot_file_is_committed() -> None:
    """Belt-and-braces: the snapshot file itself must exist on disk.
    Catches the regression where someone deletes the snapshot and the
    matches-snapshot test silently regenerates it."""
    assert SNAPSHOT.is_file(), (
        f"Wire-schema snapshot missing on disk: {SNAPSHOT}\n"
        "This file must be committed to git. Regenerate with "
        "MNEMO_UPDATE_SNAPSHOTS=1 if intentionally absent."
    )
