"""v3 phase 11: client-side UI-directive tools via the SSE channel.

The daemon does NOT execute these (design S3) -- the tool fn returns a
``_ui_action`` sentinel; the agent loop emits a ``ui_action`` event for
the chat UI to dispatch, and feeds the model a 'dispatched' ack so it
keeps going.
"""

from __future__ import annotations

from pathlib import Path

from mnemo.agent_tools import TOOLS, ToolContext
from mnemo.chat import AgentLoop
from mnemo.providers import EV_STOP, EV_TOOL_CALL, BaseProvider
from mnemo.store import Store
from tests.conftest import FakeEmbedder

UI_TOOLS = {
    "mnemo_navigate",
    "mnemo_select_node",
    "mnemo_set_filter",
    "mnemo_scroll_to",
    "mnemo_open_panel",
}


def test_ui_tools_registered_confirm_and_return_sentinel(store: Store) -> None:
    for n in UI_TOOLS:
        assert n in TOOLS, n
        assert TOOLS[n].risk == "confirm", n
    ctx = ToolContext(store=store, embedder=None)
    out = TOOLS["mnemo_navigate"].fn(ctx, path="/graph")
    assert out["_ui_action"]["action"] == "navigate"
    assert out["_ui_action"]["args"] == {"path": "/graph"}


class _Prov(BaseProvider):
    name = "fake"

    def __init__(self, script):
        self._s = script
        self._i = 0

    def stream(self, messages, tools, *, model, system=None, max_output_tokens=4096):
        i = self._i
        self._i += 1
        yield from (self._s[i] if i < len(self._s) else [(EV_STOP, "end_turn")])


def test_loop_emits_ui_action_and_acks(store: Store) -> None:
    store.grant_permission(project_key=None, tool_name="mnemo_navigate")
    conv = store.create_conversation(name="c", provider="fake", model="m")
    prov = _Prov(
        [
            [
                (EV_TOOL_CALL, {"id": "u1", "name": "mnemo_navigate", "args": {"path": "/code"}}),
                (EV_STOP, "tool_use"),
            ],
            [(EV_STOP, "end_turn")],
        ]
    )
    loop = AgentLoop(store, prov, embedder=FakeEmbedder(), model="m", system="S")
    events = list(loop.run(conv.id, "go to code"))
    ua = next(e for e in events if e["type"] == "ui_action")
    assert ua["action"] == "navigate"
    assert ua["args"] == {"path": "/code"}
    # the model gets a 'dispatched' tool_result so it can continue
    tr = next(e for e in events if e["type"] == "tool_result")
    assert tr["result"]["ui_action_dispatched"] == "navigate"


def test_chat_page_dispatches_ui_action() -> None:
    # v3.1 phase 6: the SSE/ui_action handling moved to the shared
    # static module (one impl for /chat + the dock).
    js = (Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static" / "chat.js").read_text(
        encoding="utf-8"
    )
    assert "ui_action" in js
    assert "navigate" in js
    assert "select_node" in js
