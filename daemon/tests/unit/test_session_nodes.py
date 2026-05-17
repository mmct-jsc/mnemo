"""v3.2 phase 4: companion highlights the session's subgraph on Nebula.

Two safe/confirm tools + the live-highlight flow (design S3.4):

  * ``mnemo_session_nodes`` (safe) -- the cited / tool-used node ids of
    the RUNNING conversation + their 1-hop neighbours (reuse
    ``store.get_edges_for_nodes``). Grounds "show me what's related in
    this session" without the model guessing.
  * ``mnemo_highlight_nodes`` (confirm, UI-directive) -- returns a
    ``_ui_action`` the chat.js SSE layer turns into a
    ``mnemo-highlight-nodes`` CustomEvent; graph.html highlights that
    set on the live nebula (selectPointsByIndices -- greys others by
    opacity, never hides).

The agent flow: call mnemo_session_nodes -> mnemo_highlight_nodes.
Alpine / cosmos can't run under pytest, so the client half is asserted
by JS / template surface greps.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from mnemo.agent_tools import TOOLS, ToolContext
from mnemo.chat import AgentLoop
from mnemo.providers import EV_STOP, EV_TEXT, EV_TOOL_CALL, BaseProvider
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder

_UI = Path(__file__).resolve().parents[2] / "mnemo" / "ui"
CHAT_JS = (_UI / "static" / "chat.js").read_text(encoding="utf-8")
GRAPH_HTML = (_UI / "templates" / "graph.html").read_text(encoding="utf-8")


def _node(store: Store, nid: str) -> None:
    store.upsert_node(
        Node(
            id=nid,
            type="memory_feedback",
            name=nid,
            description=None,
            body=f"b{nid}",
            source_path=f"/m/{nid}.md",
            source_kind="memory_dir",
            project_key=None,
            frontmatter_json=None,
            hash="h",
            created_at=1,
            updated_at=1,
        )
    )


# --- mnemo_session_nodes (safe) ----------------------------------------


def test_mnemo_session_nodes_registered_safe() -> None:
    assert "mnemo_session_nodes" in TOOLS
    spec = TOOLS["mnemo_session_nodes"]
    assert spec.risk == "safe"
    assert spec.parameters.get("type") == "object"
    assert spec.description.strip()


def test_mnemo_session_nodes_collects_citations_and_neighbours(store: Store) -> None:
    for n in ("n1", "n2", "n3"):
        _node(store, n)
    store.add_edge("n1", "n2", "mentions")  # n2 = a 1-hop neighbour of n1
    conv = store.create_conversation(name="c", provider="fake", model="m")
    store.append_message(conv.id, role="user", content={"text": "hi"})
    store.append_message(
        conv.id, role="assistant", content={"text": "see [mnemo:n1]", "citations": ["n1"]}
    )
    ctx = ToolContext(store=store, conversation_id=conv.id)
    out = TOOLS["mnemo_session_nodes"].fn(ctx)
    assert "n1" in out["node_ids"]
    assert "n2" in out["neighbor_ids"]
    assert "n3" not in out["node_ids"]
    assert "n3" not in out["neighbor_ids"]
    assert out["count"] == len(out["node_ids"])
    assert any(e["src"] == "n1" and e["dst"] == "n2" for e in out["edges"])


def test_mnemo_session_nodes_uses_tool_refs_and_dedups(store: Store) -> None:
    for n in ("a", "b"):
        _node(store, n)
    conv = store.create_conversation(name="c", provider="fake", model="m")
    store.append_message(
        conv.id, role="assistant", content={"text": "[mnemo:a] again [mnemo:a]", "citations": ["a"]}
    )
    store.append_message(
        conv.id,
        role="tool_call",
        content={"id": "t1", "name": "mnemo_get_node", "args": {"node_id": "b"}},
    )
    ctx = ToolContext(store=store, conversation_id=conv.id)
    out = TOOLS["mnemo_session_nodes"].fn(ctx)
    assert sorted(out["node_ids"]) == ["a", "b"]  # dedup + tool-ref pickup


def test_mnemo_session_nodes_graceful_without_conversation(store: Store) -> None:
    out = TOOLS["mnemo_session_nodes"].fn(ToolContext(store=store))
    assert out["node_ids"] == []
    assert out["neighbor_ids"] == []
    assert "error" not in out


def test_mnemo_session_nodes_skips_unknown_ids(store: Store) -> None:
    # a citation to a node that no longer exists must not crash / leak.
    conv = store.create_conversation(name="c", provider="fake", model="m")
    store.append_message(
        conv.id, role="assistant", content={"text": "[mnemo:ghost]", "citations": ["ghost"]}
    )
    out = TOOLS["mnemo_session_nodes"].fn(ToolContext(store=store, conversation_id=conv.id))
    assert out["node_ids"] == []
    assert "error" not in out


# --- mnemo_highlight_nodes (confirm, UI-directive) ---------------------


def test_mnemo_highlight_nodes_is_ui_directive_confirm(store: Store) -> None:
    assert "mnemo_highlight_nodes" in TOOLS
    spec = TOOLS["mnemo_highlight_nodes"]
    assert spec.risk == "confirm"
    out = spec.fn(ToolContext(store=store), node_ids=["x", "y"])
    assert out["_ui_action"]["action"] == "highlight_nodes"
    assert out["_ui_action"]["args"]["node_ids"] == ["x", "y"]


class _SessProv(BaseProvider):
    name = "fake"

    def __init__(self) -> None:
        self._i = 0

    def stream(
        self, messages, tools, *, model, system=None, max_output_tokens=4096
    ) -> Iterator[tuple]:
        i = self._i
        self._i += 1
        if i == 0:
            yield (EV_TOOL_CALL, {"id": "s1", "name": "mnemo_session_nodes", "args": {}})
            yield (EV_STOP, "tool_use")
        else:
            yield (EV_TEXT, "highlighted")
            yield (EV_STOP, "end_turn")


def test_agent_loop_session_nodes_uses_running_conversation(store: Store) -> None:
    _node(store, "n1")
    conv = store.create_conversation(name="c", provider="fake", model="m")
    store.append_message(
        conv.id, role="assistant", content={"text": "[mnemo:n1]", "citations": ["n1"]}
    )
    loop = AgentLoop(store, _SessProv(), embedder=FakeEmbedder(), model="m", system="You are Mnem.")
    list(loop.run(conv.id, "highlight what we discussed"))
    trs = [m for m in store.list_messages(conv.id) if m.role == "tool_result"]
    assert trs
    assert "n1" in trs[-1].content["result"]["node_ids"]


# --- client surface: live nebula highlight -----------------------------


def test_chat_js_dispatches_highlight_nodes() -> None:
    # the SSE ui_action handler turns highlight_nodes into the
    # mnemo-highlight-nodes CustomEvent (same pattern as select_node)
    assert "highlight_nodes" in CHAT_JS
    assert "mnemo-highlight-nodes" in CHAT_JS


def test_session_nodes_tool_drives_the_real_graph_highlight() -> None:
    """CONTRACT EVOLUTION (v4.5): the tools always stood server-side +
    chat.js always dispatched the highlight CustomEvent. v3.2 stopped
    SHORT of listening on the graph (cosmos froze when wired). v4.5
    swapped the renderer to sigma.js, so graph.html NOW listens for
    mnemo-highlight-nodes and drives a real sigma highlight -- the
    visual session-highlight the renderer swap unlocked."""
    assert "mnemo_session_nodes" in TOOLS
    assert "mnemo_highlight_nodes" in TOOLS
    assert "mnemo-highlight-nodes" in CHAT_JS  # dispatch still emitted
    # the loop is now CLOSED end to end: graph.html listens + highlights.
    assert "addEventListener('mnemo-highlight-nodes'" in GRAPH_HTML
    assert "this.highlight(" in GRAPH_HTML
    # still no bespoke wiring shim (direct document listener -- YAGNI).
    assert "_wireCompanionActions" not in GRAPH_HTML
