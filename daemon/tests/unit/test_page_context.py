"""v3.2 phase 2: live page-context for the companion.

The companion must see the CURRENT screen, not guess. Two halves:

  * server: a ``mnemo_page_context`` *safe* tool returns the
    conversation's persisted ``page_context`` (the client-supplied live
    page state) plus the server-known view. ``ToolContext`` grows a
    ``conversation_id`` so the tool can resolve it; the agent loop wires
    it through.
  * client: ``window.mnemoPageContext()`` -- a base.html default
    (``{page, path}``) that interactive pages override with their live
    state; ``mnemoChat`` PATCHes it onto the conversation before every
    run so the model always grounds on the real screen.

SSE / Alpine can't run in pytest, so the client half is asserted by
template/JS surface greps (the ``test_chat_v31_bugfixes`` pattern).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from mnemo.agent_tools import TOOLS, ToolContext
from mnemo.chat import AgentLoop
from mnemo.providers import EV_STOP, EV_TEXT, EV_TOOL_CALL, BaseProvider
from mnemo.store import Store
from tests.conftest import FakeEmbedder

_UI = Path(__file__).resolve().parents[2] / "mnemo" / "ui"
BASE_HTML = (_UI / "templates" / "base.html").read_text(encoding="utf-8")
CHAT_JS = (_UI / "static" / "chat.js").read_text(encoding="utf-8")
GRAPH_HTML = (_UI / "templates" / "graph.html").read_text(encoding="utf-8")
SETTINGS_HTML = (_UI / "templates" / "settings.html").read_text(encoding="utf-8")
# /node/<id> is the /nodes-family page where a single node is actually
# "selected" (a listing has no one selection); design S3.1 "/code,
# /nodes: + {selected_node_id}".
NODE_HTML = (_UI / "templates" / "node.html").read_text(encoding="utf-8")


# --- the mnemo_page_context safe tool ----------------------------------


def test_mnemo_page_context_is_registered_safe() -> None:
    assert "mnemo_page_context" in TOOLS
    spec = TOOLS["mnemo_page_context"]
    assert spec.risk == "safe"  # pure read; auto-run, never prompted
    assert spec.parameters.get("type") == "object"
    assert callable(spec.fn)
    assert spec.description.strip()


def test_tool_context_carries_conversation_id() -> None:
    ctx = ToolContext(store=None)  # type: ignore[arg-type]
    assert ctx.conversation_id is None
    ctx2 = ToolContext(store=None, conversation_id="c1")  # type: ignore[arg-type]
    assert ctx2.conversation_id == "c1"


def test_mnemo_page_context_returns_persisted_context(store: Store) -> None:
    pc = {
        "page": "graph",
        "selected_node_id": "n1",
        "visible_node_ids": ["n1", "n2"],
        "query": "mqtt",
    }
    conv = store.create_conversation(
        name="c", provider="fake", model="m", project_key="projX", page_context=pc
    )
    ctx = ToolContext(store=store, conversation_id=conv.id)
    out = TOOLS["mnemo_page_context"].fn(ctx)
    assert out["available"] is True
    assert out["page_context"] == pc
    assert out["conversation_id"] == conv.id
    assert out["project_key"] == "projX"


def test_mnemo_page_context_graceful_without_conversation(store: Store) -> None:
    # MCP builds a ToolContext with no conversation -> must not raise.
    ctx = ToolContext(store=store)
    out = TOOLS["mnemo_page_context"].fn(ctx)
    assert out["available"] is False
    assert out["page_context"] is None
    assert "error" not in out


def test_mnemo_page_context_unknown_conversation(store: Store) -> None:
    ctx = ToolContext(store=store, conversation_id="nope")
    out = TOOLS["mnemo_page_context"].fn(ctx)
    assert out["available"] is False
    assert out["page_context"] is None
    assert "error" not in out


class _PageCtxProvider(BaseProvider):
    """Turn 0 calls mnemo_page_context; turn 1 ends."""

    name = "fake"

    def __init__(self) -> None:
        self._i = 0

    def stream(
        self, messages, tools, *, model, system=None, max_output_tokens=4096
    ) -> Iterator[tuple]:
        i = self._i
        self._i += 1
        if i == 0:
            yield (EV_TOOL_CALL, {"id": "t1", "name": "mnemo_page_context", "args": {}})
            yield (EV_STOP, "tool_use")
        else:
            yield (EV_TEXT, "this page is the graph")
            yield (EV_STOP, "end_turn")


def test_agent_loop_exposes_live_page_context_to_tools(store: Store) -> None:
    """The loop must build ToolContext with the running conversation id
    so mnemo_page_context resolves the live (client-PATCHed) state."""
    pc = {"page": "graph", "selected_node_id": "n1"}
    conv = store.create_conversation(name="c", provider="fake", model="m", page_context=pc)
    loop = AgentLoop(
        store, _PageCtxProvider(), embedder=FakeEmbedder(), model="m", system="You are Mnem."
    )
    list(loop.run(conv.id, "what is on this page?"))
    trs = [m for m in store.list_messages(conv.id) if m.role == "tool_result"]
    assert trs, "expected a tool_result row"
    result = trs[-1].content["result"]
    assert result["available"] is True
    assert result["page_context"] == pc


# --- client surface: window.mnemoPageContext() ------------------------


def test_base_html_defines_default_page_context_provider() -> None:
    # base.html ships the default {page, path}; pages override it.
    assert "window.mnemoPageContext" in BASE_HTML
    assert "location.pathname" in BASE_HTML
    # the nav page is carried (templates pass the `page` Jinja var)
    assert "{{ page" in BASE_HTML


def test_chat_js_attaches_live_page_context_each_run() -> None:
    # a helper resolves the live override (falls back to opts.pageContext)
    assert "livePageContext" in CHAT_JS
    assert "window.mnemoPageContext" in CHAT_JS
    # the conversation's page_context is refreshed (PATCH) before the run
    assert "page_context" in CHAT_JS
    assert "method: 'PATCH'" in CHAT_JS or 'method: "PATCH"' in CHAT_JS


def test_graph_page_overrides_page_context_with_live_state() -> None:
    assert "window.mnemoPageContext" in GRAPH_HTML
    assert "selected_node_id" in GRAPH_HTML
    assert "visible_node_ids" in GRAPH_HTML


def test_settings_page_overrides_page_context_with_weights() -> None:
    assert "window.mnemoPageContext" in SETTINGS_HTML
    # the override exposes the retune-relevant state
    assert "recent_feedback" in SETTINGS_HTML


def test_node_page_overrides_page_context_with_selection() -> None:
    assert "window.mnemoPageContext" in NODE_HTML
    assert "selected_node_id" in NODE_HTML
