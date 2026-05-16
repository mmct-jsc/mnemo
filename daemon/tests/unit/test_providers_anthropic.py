"""v3 phase 2: Anthropic provider -> provider-agnostic event translation.

Offline + deterministic: a fake SDK client is injected (no network, no
key). Live integration is a phase-12 smoke against the real .env key.
We lock: text deltas stream as ('text_delta', str); tool_use blocks in
the final message become ('tool_call', {id,name,args}); the run ends
with ('stop', reason); and the request carries the tools + a
cache_control'd system block (the prompt-caching contract for the
multi-turn agent loop).
"""

from __future__ import annotations

import types

from mnemo.agent_tools import TOOLS
from mnemo.providers import EV_STOP, EV_TEXT, EV_TOOL_CALL
from mnemo.providers.anthropic import AnthropicProvider


def _evt(type_, **kw):
    return types.SimpleNamespace(type=type_, **kw)


class _FakeStream:
    def __init__(self, deltas, final):
        self._deltas = deltas
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for d in self._deltas:
            yield _evt(
                "content_block_delta",
                delta=types.SimpleNamespace(type="text_delta", text=d),
            )

    def get_final_message(self):
        return self._final


class _FakeMessages:
    def __init__(self, outer):
        self._outer = outer

    def stream(self, **kwargs):
        self._outer.captured = kwargs
        tool_block = types.SimpleNamespace(
            type="tool_use", id="toolu_1", name="mnemo_query", input={"prompt": "x"}
        )
        final = types.SimpleNamespace(content=[tool_block], stop_reason="tool_use")
        return _FakeStream(["Hel", "lo"], final)


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages(self)
        self.captured: dict = {}


def _provider():
    fake = _FakeClient()
    p = AnthropicProvider(api_key="unused", client=fake)
    return p, fake


def test_stream_translates_text_tool_and_stop() -> None:
    p, _ = _provider()
    tools = [TOOLS["mnemo_query"], TOOLS["mnemo_get_node"]]
    events = list(
        p.stream(
            [{"role": "user", "content": "hi"}],
            tools,
            model="claude-sonnet-4-5-20250929",
            system="You are Mnem.",
        )
    )
    assert events[0] == (EV_TEXT, "Hel")
    assert events[1] == (EV_TEXT, "lo")
    tool_evt = next(e for e in events if e[0] == EV_TOOL_CALL)
    assert tool_evt[1]["name"] == "mnemo_query"
    assert tool_evt[1]["id"] == "toolu_1"
    assert tool_evt[1]["args"] == {"prompt": "x"}
    assert events[-1] == (EV_STOP, "tool_use")


def test_request_carries_tools_and_cache_controlled_system() -> None:
    p, fake = _provider()
    list(
        p.stream(
            [{"role": "user", "content": "hi"}],
            [TOOLS["mnemo_query"]],
            model="claude-sonnet-4-5-20250929",
            system="frozen system prompt",
        )
    )
    cap = fake.captured
    assert cap["model"] == "claude-sonnet-4-5-20250929"
    assert cap["max_tokens"] >= 1
    # tools mapped to Anthropic shape
    assert cap["tools"][0]["name"] == "mnemo_query"
    assert "input_schema" in cap["tools"][0]
    # prompt-caching: system is a block list with an ephemeral breakpoint
    assert isinstance(cap["system"], list)
    assert cap["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert cap["system"][0]["text"] == "frozen system prompt"


def test_provider_name_and_factory() -> None:
    from mnemo.providers import get_provider

    p, _ = _provider()
    assert p.name == "anthropic"
    got = get_provider("anthropic", api_key="k")
    assert got.name == "anthropic"
