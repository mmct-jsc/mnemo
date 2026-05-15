"""v3 phase 2: the server-side agent loop (safe-tools-only).

Driven by a scripted FakeProvider so it's deterministic and offline.
Pins the design-S2/S4 contract: <=8 iterations, safe-tool dispatch +
result fed back, text accumulates into one persisted assistant message,
[mnemo:ID] citations surface as events + persist, provider errors
become an `error` event without losing the user's message.
"""

from __future__ import annotations

from collections.abc import Iterator

from mnemo.chat import MAX_ITERS, AgentLoop
from mnemo.providers import EV_STOP, EV_TEXT, EV_TOOL_CALL, BaseProvider, ProviderError
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder


class FakeProvider(BaseProvider):
    """Yields a scripted list of event-lists, one per .stream() call."""

    name = "fake"

    def __init__(self, script: list[list[tuple]] | None = None, *, raise_on=None):
        self._script = script or []
        self._i = 0
        self._raise_on = raise_on

    def stream(self, messages, tools, *, model, system=None, max_output_tokens=4096):
        turn = self._i
        self._i += 1
        if self._raise_on is not None and turn == self._raise_on:
            raise ProviderError("boom from provider")
        events = self._script[turn] if turn < len(self._script) else [(EV_STOP, "end_turn")]
        yield from events


def _seed_node(store: Store, nid: str) -> None:
    store.upsert_node(
        Node(
            id=nid,
            type="memory_feedback",
            name=nid,
            description=None,
            body=f"body {nid}",
            source_path=f"/m/{nid}.md",
            source_kind="memory_dir",
            project_key=None,
            frontmatter_json=None,
            hash="h",
            created_at=1,
            updated_at=1,
        )
    )


def _loop(store: Store, provider: BaseProvider) -> AgentLoop:
    return AgentLoop(store, provider, embedder=FakeEmbedder(), model="m", system="You are Mnem.")


def _run(loop: AgentLoop, conv_id: str, text: str) -> list[dict]:
    return list(loop.run(conv_id, text))


def test_text_only_turn_persists_one_assistant_message(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="fake", model="m")
    prov = FakeProvider([[(EV_TEXT, "Hello "), (EV_TEXT, "world"), (EV_STOP, "end_turn")]])
    events = _run(_loop(store, prov), conv.id, "hi")

    assert events[0]["type"] == "thinking"
    assert any(e["type"] == "text_delta" for e in events)
    assert events[-1]["type"] == "done"

    msgs = store.list_messages(conv.id)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content["text"] == "hi"
    assert msgs[1].content["text"] == "Hello world"


def test_tool_call_dispatches_safe_tool_and_feeds_result_back(store: Store) -> None:
    _seed_node(store, "n1")
    conv = store.create_conversation(name="c", provider="fake", model="m")
    prov = FakeProvider(
        [
            [
                (EV_TEXT, "looking"),
                (EV_TOOL_CALL, {"id": "t1", "name": "mnemo_get_node", "args": {"node_id": "n1"}}),
                (EV_STOP, "tool_use"),
            ],
            [(EV_TEXT, "done, see [mnemo:n1]"), (EV_STOP, "end_turn")],
        ]
    )
    events = _run(_loop(store, prov), conv.id, "what is n1")

    assert any(e["type"] == "tool_call" and e["name"] == "mnemo_get_node" for e in events)
    tr = next(e for e in events if e["type"] == "tool_result")
    assert tr["id"] == "t1"
    cite = next(e for e in events if e["type"] == "citation")
    assert cite["node_id"] == "n1"
    assert events[-1]["type"] == "done"

    roles = [m.role for m in store.list_messages(conv.id)]
    assert roles == ["user", "assistant", "tool_call", "tool_result", "assistant"]
    final = store.list_messages(conv.id)[-1]
    assert final.content["citations"] == ["n1"]


def test_iteration_cap_emits_error(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="fake", model="m")
    # never stops -- always asks for another tool call
    loop_turn = [
        (EV_TOOL_CALL, {"id": "t", "name": "mnemo_get_node", "args": {"node_id": "x"}}),
        (EV_STOP, "tool_use"),
    ]
    prov = FakeProvider([loop_turn] * (MAX_ITERS + 3))
    events = _run(_loop(store, prov), conv.id, "loop forever")

    assert sum(1 for e in events if e["type"] == "thinking") == MAX_ITERS
    assert events[-1]["type"] == "error"
    assert "iteration" in events[-1]["message"].lower()


def test_provider_error_becomes_error_event_user_msg_preserved(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="fake", model="m")
    prov = FakeProvider(raise_on=0)
    events = _run(_loop(store, prov), conv.id, "trigger boom")

    err = next(e for e in events if e["type"] == "error")
    assert "boom" in err["message"]
    # the user's message is preserved so they can retry
    msgs = store.list_messages(conv.id)
    assert msgs[0].role == "user"
    assert msgs[0].content["text"] == "trigger boom"


def test_multiple_citations_extracted_in_order(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="fake", model="m")
    prov = FakeProvider([[(EV_TEXT, "see [mnemo:abc] then [mnemo:def]"), (EV_STOP, "end_turn")]])
    events = _run(_loop(store, prov), conv.id, "q")
    cites = [e["node_id"] for e in events if e["type"] == "citation"]
    assert cites == ["abc", "def"]


def test_base_provider_stream_is_abstract() -> None:
    bp = BaseProvider()
    try:
        next(iter(bp.stream([], [], model="m")))
        raised = False
    except NotImplementedError:
        raised = True
    assert raised


def _consume(it: Iterator) -> list:
    return list(it)
