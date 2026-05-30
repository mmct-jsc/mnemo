"""v5.12.0 -- mnemo_analyze MCP tool registration + arg handling.

The tool is the 27th on the MCP stdio surface (was 26 in v5.11.0).
Same backing call as POST /v1/analyze: walks the graph, runs the
3 deterministic detectors, returns the canonical envelope.

Contract this test file locks:
- ``mnemo_analyze`` is registered in ``mnemo.agent_tools.TOOLS``.
- It's tagged ``risk="safe"`` (no side effects).
- Calling it returns the canonical
  ``{ran_at, node_count_scanned, findings, summary}`` envelope.
- The ``types`` and ``project_key`` args are accepted optionally.
"""

from __future__ import annotations

import pytest

from mnemo.agent_tools import TOOLS, ToolContext
from mnemo.store import Node, Store


@pytest.fixture
def ctx(tmp_path):
    class _FakeEmbedder:
        dim = 384

        def embed_text(self, text):
            sig = (text or "")[:32].lower()
            base = [0.0] * 384
            for i, ch in enumerate(sig):
                base[i % 384] += ord(ch) / 1000.0
            norm = sum(x * x for x in base) ** 0.5 or 1.0
            return [x / norm for x in base]

        def embed_batch(self, texts):
            return [self.embed_text(t) for t in texts]

    store = Store(tmp_path / "mnemo.db")
    yield ToolContext(store=store, embedder=_FakeEmbedder())
    store.close()


def _mknode(*, id: str, description: str = "", body: str = "") -> Node:
    import time

    now = int(time.time())
    return Node(
        id=id,
        type="memory_feedback",
        name=id.split("/", 1)[-1],
        description=description,
        body=body,
        source_path=f"/tmp/{id}.md",
        source_kind="memory",
        project_key=None,
        frontmatter_json=None,
        hash="",
        created_at=now,
        updated_at=now,
    )


def test_mnemo_analyze_is_registered() -> None:
    """The tool is part of the published surface."""
    assert "mnemo_analyze" in TOOLS, (
        "v5.12.0 contract: mnemo_analyze must be in TOOLS; the MCP "
        "surface goes from 26 -> 27. If you renamed/removed it, "
        "update test_mcp_tool_surface_contract.py too."
    )


def test_mnemo_analyze_is_safe_risk() -> None:
    """The auditor is read-only; it must be ``safe`` so MCP hosts
    auto-run it without prompting."""
    spec = TOOLS["mnemo_analyze"]
    assert spec.risk == "safe", (
        f"mnemo_analyze risk must be 'safe' (read-only auditor); got {spec.risk!r}"
    )


def test_mnemo_analyze_call_returns_envelope(ctx) -> None:
    """Calling the tool returns the canonical envelope."""
    spec = TOOLS["mnemo_analyze"]
    result = spec.fn(ctx)
    assert isinstance(result, dict), f"tool must return a dict; got {type(result)}"
    assert {"ran_at", "node_count_scanned", "findings", "summary"} <= set(result.keys())


def test_mnemo_analyze_accepts_types_filter(ctx) -> None:
    """Calling with ``types=['stale']`` restricts detectors."""
    ctx.store.upsert_node(
        _mknode(
            id="memory_feedback/x",
            description="SUPERSEDED",
            body="[mnemo:gone-forever]",
        )
    )
    spec = TOOLS["mnemo_analyze"]
    result = spec.fn(ctx, types=["stale"])
    types_seen = {f["type"] for f in result["findings"]}
    assert types_seen == {"stale"}, f"types filter not honored on MCP path; saw {types_seen}"


def test_mnemo_analyze_accepts_project_key_kwarg(ctx) -> None:
    """The ``project_key`` arg is accepted (currently no-op until v5.13.0)."""
    spec = TOOLS["mnemo_analyze"]
    # Should not raise.
    result = spec.fn(ctx, project_key="some-project")
    assert "findings" in result


def test_mnemo_analyze_accepts_propose_actions_kwarg(ctx) -> None:
    """v5.15.0: the ``propose_actions`` arg is accepted. With no env
    opt-in + no proposer the enrichment is a no-op, so findings carry
    no action and the response is byte-stable."""
    ctx.store.upsert_node(
        _mknode(
            id="memory_feedback/x",
            description="canonical",
            body="cites [mnemo:does-not-exist]",
        )
    )
    spec = TOOLS["mnemo_analyze"]
    # propose_actions=True but no env opt-in -> proposer is None ->
    # the pass is a no-op; must not raise.
    result = spec.fn(ctx, types=["orphan_references"], propose_actions=True)
    assert "findings" in result
    orphan = next(f for f in result["findings"] if f["type"] == "orphan_reference")
    assert orphan.get("action") is None, (
        "without the env opt-in there's no proposer; action must stay None"
    )


def test_mnemo_analyze_propose_actions_in_schema() -> None:
    """The tool's input schema advertises the new optional param so
    MCP hosts can surface it."""
    spec = TOOLS["mnemo_analyze"]
    props = spec.parameters["properties"]
    assert "propose_actions" in props, (
        f"mnemo_analyze must advertise propose_actions; got {sorted(props)}"
    )
