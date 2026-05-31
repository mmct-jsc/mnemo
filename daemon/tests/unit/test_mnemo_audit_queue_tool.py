"""v5.22.0 Phase 4a -- mnemo_audit_queue MCP tool (read-only).

The 28th tool on the MCP surface (was 27 in v5.21.0). Read-only: lists
the persisted proactive-audit findings from ``audit_queue`` so Mnem can
answer "what's wrong with my corpus?" from the queue. NO apply tool --
mutation is Phase 4b, a deliberately separate later release.
"""

from __future__ import annotations

import pytest

from mnemo.agent_tools import TOOLS, ToolContext
from mnemo.store import Store, _finding_fingerprint


@pytest.fixture
def ctx(tmp_path):
    store = Store(tmp_path / "mnemo.db")
    yield ToolContext(store=store)
    store.close()


def _stale(node_id: str) -> dict:
    return {
        "type": "stale",
        "node_ids": [node_id],
        "description": f"{node_id} SUPERSEDED",
        "severity": "low",
    }


def test_mnemo_audit_queue_is_registered() -> None:
    assert "mnemo_audit_queue" in TOOLS, (
        "v5.22.0 contract: mnemo_audit_queue must be in TOOLS; the MCP "
        "surface goes 27 -> 28. Update test_mcp_tool_surface_contract.py too."
    )


def test_mnemo_audit_queue_is_safe_risk() -> None:
    # Read-only -> safe, so MCP hosts auto-run it without prompting.
    assert TOOLS["mnemo_audit_queue"].risk == "safe"


def test_mnemo_audit_queue_returns_open_findings(ctx) -> None:
    ctx.store.reconcile_audit_queue([_stale("a"), _stale("b")], ("stale",))
    result = TOOLS["mnemo_audit_queue"].fn(ctx)
    assert {"findings", "total", "counts"} <= set(result)
    assert result["total"] == 2
    assert result["counts"]["open"] == 2
    assert {f["type"] for f in result["findings"]} == {"stale"}
    assert all("fingerprint" in f for f in result["findings"]), (
        "each finding must carry its fingerprint so the user can dismiss it"
    )


def test_mnemo_audit_queue_respects_status(ctx) -> None:
    ctx.store.reconcile_audit_queue([_stale("a")], ("stale",))
    ctx.store.set_audit_finding_status(_finding_fingerprint(_stale("a")), "dismissed")
    assert TOOLS["mnemo_audit_queue"].fn(ctx, status="open")["total"] == 0
    assert TOOLS["mnemo_audit_queue"].fn(ctx, status="all")["total"] == 1


def test_mnemo_audit_queue_respects_limit(ctx) -> None:
    ctx.store.reconcile_audit_queue([_stale(f"n{i}") for i in range(30)], ("stale",))
    result = TOOLS["mnemo_audit_queue"].fn(ctx, limit=5)
    assert len(result["findings"]) == 5
    assert result["total"] == 30, "total reflects the full open set, not the page"


def test_mnemo_audit_queue_advertises_params() -> None:
    props = TOOLS["mnemo_audit_queue"].parameters["properties"]
    assert "status" in props
    assert "limit" in props
