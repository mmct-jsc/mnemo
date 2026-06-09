"""v5.23.0 Phase 4b -- mnemo_apply_finding MCP tool (the first mutation tool).

The 29th tool (was 28 in v5.22.0). risk=confirm -- the host prompts before
the apply. Two-step: no confirm_node_hash -> preview (read-only); with the
preview's node_hash -> apply (edit body + mark finding resolved). A stale
hash / non-applyable finding returns an error dict (safe_fn never raises).
"""

from __future__ import annotations

import time

import pytest

from mnemo.agent_tools import TOOLS, ToolContext
from mnemo.store import Node, Store, _finding_fingerprint

DEAD = "d" * 32


@pytest.fixture
def ctx(tmp_path):
    store = Store(tmp_path / "mnemo.db")
    yield ToolContext(store=store)
    store.close()


def _mknode(store: Store, *, id: str, body: str) -> None:
    now = int(time.time())
    store.upsert_node(
        Node(
            id=id,
            type="memory_feedback",
            name=id,
            description="",
            body=body,
            source_path=f"/m/{id}.md",
            source_kind="memory",
            project_key=None,
            frontmatter_json=None,
            hash="h-" + id,
            created_at=now,
            updated_at=now,
        )
    )


def _seed_orphan(store: Store, node_id: str, missing: list[str]) -> str:
    finding = {
        "type": "orphan_reference",
        "node_ids": [node_id],
        "description": "cites missing",
        "severity": "high",
        "missing_targets": sorted(missing),
    }
    store.reconcile_audit_queue([finding], ("orphan_reference",))
    return _finding_fingerprint(finding)


def test_registered_and_confirm_risk() -> None:
    assert "mnemo_apply_finding" in TOOLS, (
        "v5.23.0 contract: mnemo_apply_finding must be in TOOLS; the MCP "
        "surface goes 28 -> 29. Update test_mcp_tool_surface_contract.py too."
    )
    assert TOOLS["mnemo_apply_finding"].risk == "confirm", (
        "the first node-mutation tool must be risk=confirm so the host prompts"
    )


def test_advertises_params() -> None:
    props = TOOLS["mnemo_apply_finding"].parameters["properties"]
    assert "fingerprint" in props
    assert "confirm_node_hash" in props


def test_preview_without_hash_is_read_only(ctx) -> None:
    _mknode(ctx.store, id="A", body=f"See [mnemo:{DEAD}] now.")
    fp = _seed_orphan(ctx.store, "A", [DEAD])
    out = TOOLS["mnemo_apply_finding"].fn(ctx, fingerprint=fp)
    assert out["applyable"] is True
    assert out["removed"] == [DEAD]
    assert out["node_hash"]
    assert ctx.store.get_audit_finding(fp).status == "open"


def test_apply_with_hash(ctx) -> None:
    _mknode(ctx.store, id="A", body=f"See [mnemo:{DEAD}] now.")
    fp = _seed_orphan(ctx.store, "A", [DEAD])
    pv = TOOLS["mnemo_apply_finding"].fn(ctx, fingerprint=fp)
    out = TOOLS["mnemo_apply_finding"].fn(ctx, fingerprint=fp, confirm_node_hash=pv["node_hash"])
    assert out.get("applied") is True
    assert ctx.store.get_audit_finding(fp).status == "resolved"
    assert f"[mnemo:{DEAD}]" not in ctx.store.get_node("A").body


def test_apply_stale_hash_returns_error_and_no_edit(ctx) -> None:
    _mknode(ctx.store, id="A", body=f"See [mnemo:{DEAD}] now.")
    fp = _seed_orphan(ctx.store, "A", [DEAD])
    out = TOOLS["mnemo_apply_finding"].fn(ctx, fingerprint=fp, confirm_node_hash="wrong")
    assert "error" in out, "safe_fn must surface the stale-preview error as a dict"
    assert f"[mnemo:{DEAD}]" in ctx.store.get_node("A").body


def test_preview_placeholder_not_applyable(ctx) -> None:
    _mknode(ctx.store, id="B", body="cite [mnemo:<id>] here")
    fp = _seed_orphan(ctx.store, "B", ["<id>"])
    out = TOOLS["mnemo_apply_finding"].fn(ctx, fingerprint=fp)
    assert out["applyable"] is False
