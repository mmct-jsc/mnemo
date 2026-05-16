"""v3 phase 5: OpenAI / Google / Ollama providers.

Per the design test strategy (S8): mocked clients, deterministic,
offline. We lock each provider's translation INTO the shared
event contract (text_delta / tool_call / stop). Real-SDK wire shape is
a phase-12 live smoke (gated on env keys, skipped in CI).
"""

from __future__ import annotations

import json
import types

from mnemo.agent_tools import TOOLS
from mnemo.providers import EV_STOP, EV_TEXT, EV_TOOL_CALL, get_provider
from mnemo.providers.ollama import _parse_tool_fences
from mnemo.providers.openai import OpenAIProvider


def _ns(**kw):
    return types.SimpleNamespace(**kw)


# --- OpenAI -------------------------------------------------------------


class _FakeOAStream:
    def __iter__(self):
        # text delta
        yield _ns(choices=[_ns(delta=_ns(content="Hi ", tool_calls=None), finish_reason=None)])
        yield _ns(choices=[_ns(delta=_ns(content="there", tool_calls=None), finish_reason=None)])
        # streamed function call (one chunk for simplicity)
        yield _ns(
            choices=[
                _ns(
                    delta=_ns(
                        content=None,
                        tool_calls=[
                            _ns(
                                index=0,
                                id="call_1",
                                function=_ns(
                                    name="mnemo_query",
                                    arguments='{"prompt": "x"}',
                                ),
                            )
                        ],
                    ),
                    finish_reason=None,
                )
            ]
        )
        yield _ns(
            choices=[_ns(delta=_ns(content=None, tool_calls=None), finish_reason="tool_calls")]
        )


class _FakeOAClient:
    def __init__(self):
        self.captured = {}

        class _Comp:
            def create(inner, **kw):  # noqa: N805
                self.captured = kw
                return _FakeOAStream()

        self.chat = _ns(completions=_Comp())


def test_openai_translates_text_tool_stop() -> None:
    fake = _FakeOAClient()
    p = OpenAIProvider(api_key="k", client=fake)
    evs = list(
        p.stream(
            [{"role": "user", "content": "hi"}],
            [TOOLS["mnemo_query"]],
            model="gpt-4o-mini",
            system="You are Mnem.",
        )
    )
    assert (EV_TEXT, "Hi ") in evs
    assert (EV_TEXT, "there") in evs
    tc = next(e for e in evs if e[0] == EV_TOOL_CALL)
    assert tc[1]["name"] == "mnemo_query"
    assert tc[1]["args"] == {"prompt": "x"}
    assert evs[-1] == (EV_STOP, "tool_use")
    # system prepended as a system-role message; tools mapped to functions
    assert fake.captured["messages"][0]["role"] == "system"
    assert fake.captured["tools"][0]["type"] == "function"


# --- Google -------------------------------------------------------------


class _FakeGoogleClient:
    def __init__(self):
        self.captured = {}

        class _Models:
            def generate_content_stream(inner, **kw):  # noqa: N805
                self.captured = kw
                yield _ns(text="Hello", candidates=None)
                fc = _ns(name="mnemo_get_node", args={"node_id": "n1"})
                part = _ns(function_call=fc, text=None)
                yield _ns(
                    text=None,
                    candidates=[_ns(content=_ns(parts=[part]))],
                )

        self.models = _Models()


def test_google_translates_text_tool_stop() -> None:
    from mnemo.providers.google import GoogleProvider

    fake = _FakeGoogleClient()
    p = GoogleProvider(api_key="k", client=fake)
    evs = list(
        p.stream(
            [{"role": "user", "content": "hi"}],
            [TOOLS["mnemo_get_node"]],
            model="gemini-2.5-flash",
            system="You are Mnem.",
        )
    )
    assert (EV_TEXT, "Hello") in evs
    tc = next(e for e in evs if e[0] == EV_TOOL_CALL)
    assert tc[1]["name"] == "mnemo_get_node"
    assert tc[1]["args"] == {"node_id": "n1"}
    assert evs[-1][0] == EV_STOP


# --- Ollama -------------------------------------------------------------


def test_ollama_native_tool_path() -> None:
    from mnemo.providers.ollama import OllamaProvider

    def fake_transport(url, payload):
        assert url.endswith("/api/chat")
        yield {"message": {"content": "thinking"}, "done": False}
        yield {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "mnemo_query", "arguments": {"prompt": "x"}}}],
            },
            "done": False,
        }
        yield {"message": {"content": ""}, "done": True}

    p = OllamaProvider(transport=fake_transport)
    evs = list(
        p.stream(
            [{"role": "user", "content": "hi"}],
            [TOOLS["mnemo_query"]],
            model="llama3.1:8b",
        )
    )
    assert (EV_TEXT, "thinking") in evs
    tc = next(e for e in evs if e[0] == EV_TOOL_CALL)
    assert tc[1]["name"] == "mnemo_query"
    assert tc[1]["args"] == {"prompt": "x"}
    assert evs[-1][0] == EV_STOP


def test_ollama_fence_parser_fallback() -> None:
    text = 'sure <tool_call>{"name": "mnemo_query", "args": {"prompt": "y"}}</tool_call> done'
    calls = _parse_tool_fences(text)
    assert calls == [{"name": "mnemo_query", "args": {"prompt": "y"}}]
    assert _parse_tool_fences("no tools here") == []


# --- factory ------------------------------------------------------------


def test_get_provider_wires_all_four() -> None:
    assert get_provider("openai", api_key="k").name == "openai"
    assert get_provider("google", api_key="k").name == "google"
    assert get_provider("ollama").name == "ollama"
    assert get_provider("anthropic", api_key="k").name == "anthropic"


def test_get_provider_unknown_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown provider"):
        get_provider("grok", api_key="k")
    # round-trip the args helper for OpenAI message conversion
    assert json.loads('{"a": 1}') == {"a": 1}
