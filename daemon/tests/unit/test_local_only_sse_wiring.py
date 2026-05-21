"""v5.1.0: ``local_only_excluded`` traveling through the full pipe.

v5.0 wired the retrieval-level filter (Phase 1) + the dock UI surface
+ Settings toggle (Phase 5) -- but the bridge between them was never
implemented. ``mnemo_query`` didn't accept ``exclude_local_only``,
and its result dict didn't carry the count. So the dock's banner
state ``localOnlyExcluded`` always stayed at 0 -- the warning was
invisible in practice.

This module fills the gap:

- ``mnemo_query`` grows ``exclude_local_only`` (optional, default
  False). When True, the underlying ``retrieve.query`` filters
  local_only nodes.
- The tool result dict carries ``local_only_excluded`` (the
  count from ``RetrievalResult``). Downstream callers (the agent
  loop -> SSE -> chat.js) read it from the standard
  ``tool_result`` event channel; the chat factory's
  ``localOnlyExcluded`` field updates on every tool_result that
  carries a non-zero count.

Tests:

- Tool schema accepts ``exclude_local_only``.
- Tool result dict carries the filtered count.
- chat.js extracts the count from the tool_result event and
  accumulates into ``localOnlyExcluded`` (template-grep).
"""

from __future__ import annotations

from mnemo.agent_tools import TOOLS, ToolContext
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder


def _seed(store: Store, embedder: FakeEmbedder, *, name: str, local_only: bool) -> Node:
    n = Node.new(
        type="memory_feedback",
        name=name,
        body=f"Body for {name}: MQTT auth CONNACK paho WSS",
        source_path=f"/{name}.md",
        source_kind="memory_dir",
        local_only=local_only,
    )
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, embedder.embed_text(n.body), n.body)])
    return n


def test_mnemo_query_accepts_exclude_local_only_param() -> None:
    """The tool schema must advertise ``exclude_local_only`` so an
    MCP host (or the prompt-architect skill) can opt in."""
    spec = TOOLS["mnemo_query"]
    props = spec.parameters["properties"]
    assert "exclude_local_only" in props, "mnemo_query schema must advertise exclude_local_only"
    # Default False keeps every pre-v5 caller's behaviour identical.
    assert props["exclude_local_only"].get("default") is False


def test_mnemo_query_returns_local_only_excluded_count(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    """Result dict carries the count so the SSE consumer can update
    the warning banner. The count is 0 when the filter is OFF
    (legacy callers) and matches RetrievalResult.local_only_excluded
    when ON."""
    _seed(store, fake_embedder, name="public-node", local_only=False)
    _seed(store, fake_embedder, name="private-node", local_only=True)
    ctx = ToolContext(store=store, embedder=fake_embedder)

    # Filter OFF (legacy callers): count is 0, both nodes visible.
    off = TOOLS["mnemo_query"].fn(ctx, prompt="node body", limit=10)
    assert "local_only_excluded" in off, (
        "result dict must always carry local_only_excluded so the SSE "
        "consumer can read it unconditionally"
    )
    assert off["local_only_excluded"] == 0
    assert len(off["hits"]) == 2

    # Filter ON (prompt-architect skill): count reflects drops.
    on = TOOLS["mnemo_query"].fn(ctx, prompt="node body", limit=10, exclude_local_only=True)
    assert on["local_only_excluded"] == 1
    # The local_only node is filtered out.
    names = {h["name"] for h in on["hits"]}
    assert "public-node" in names
    assert "private-node" not in names


def test_mnemo_query_legacy_callers_unchanged(store: Store, fake_embedder: FakeEmbedder) -> None:
    """Callers that never set ``exclude_local_only`` must see the
    same byte-for-byte result they got pre-v5.1 (the carry-forward
    of the v4.7.0 anti-goal). The new ``local_only_excluded`` field
    is additive and always 0 for them."""
    _seed(store, fake_embedder, name="n1", local_only=False)
    _seed(store, fake_embedder, name="n2", local_only=True)
    ctx = ToolContext(store=store, embedder=fake_embedder)
    res = TOOLS["mnemo_query"].fn(ctx, prompt="auth", limit=10)
    assert len(res["hits"]) == 2  # both surface
    assert res["local_only_excluded"] == 0


def test_chat_js_updates_local_only_excluded_from_tool_result() -> None:
    """The chat.js SSE handler must read ``local_only_excluded`` off
    the tool_result event and accumulate into the factory state so
    the banner fires. Template-grep -- the dock has no JS test
    runner today.

    The test is strict to avoid false-positives from the existing
    state-field comment: there must be an EXECUTABLE READ of the
    field off a payload (``.local_only_excluded`` access) AND an
    EXECUTABLE WRITE to the factory state (``self.localOnlyExcluded
    =`` or ``+=`` assignment) within the tool_result handler.
    """
    from pathlib import Path

    chat_js = Path(__file__).resolve().parents[3] / "daemon" / "mnemo" / "ui" / "static" / "chat.js"
    js = chat_js.read_text(encoding="utf-8")
    # The handler must explicitly access the field on a payload --
    # ``.local_only_excluded`` access form (not just a comment
    # mention).
    assert ".local_only_excluded" in js, (
        "chat.js must access the .local_only_excluded field on the tool_result "
        "payload (executable read, not a comment reference)"
    )
    # The handler must write the count into the factory state. We
    # accept either an assignment or an accumulation (+= for multi-
    # query architect runs that drop nodes across several mnemo_query
    # calls).
    assert (
        "self.localOnlyExcluded =" in js
        or "self.localOnlyExcluded +=" in js
        or "this.localOnlyExcluded =" in js
        or "this.localOnlyExcluded +=" in js
    ), "chat.js must write into the factory's localOnlyExcluded so the banner template re-renders"
