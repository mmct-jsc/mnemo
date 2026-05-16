"""v3.1 phase 2: every provider surfaces token usage.

Design 2026-05-15-mnemo-v3.1 S3.2: ``stream()`` yields a 4th event
``('usage', {input_tokens, output_tokens, cache_read_input_tokens})``
exactly once, immediately before ``('stop', ...)``. It is emitted only
when the provider actually has usage numbers (a fake client without
usage stays a pure text/tool/stop stream -- this is what keeps the
phase-2/5 provider tests green unchanged).

Mocked clients, offline, deterministic (design S8). Real-SDK wire shape
is the phase-9 live smoke.
"""

from __future__ import annotations

import types

from mnemo.providers import EV_STOP, EV_USAGE
from mnemo.providers.anthropic import AnthropicProvider
from mnemo.providers.openai import OpenAIProvider


def _ns(**kw):
    return types.SimpleNamespace(**kw)


def _usage_then_stop(evs: list) -> dict:
    """Assert there is exactly one usage event and it sits directly
    before the terminal stop, then return its payload."""
    kinds = [e[0] for e in evs]
    assert kinds.count(EV_USAGE) == 1
    assert evs[-1][0] == EV_STOP
    assert evs[-2][0] == EV_USAGE
    return evs[-2][1]


# --- Anthropic ----------------------------------------------------------


class _AStream:
    def __init__(self, final):
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        yield _ns(type="content_block_delta", delta=_ns(type="text_delta", text="hi"))

    def get_final_message(self):
        return self._final


class _AClient:
    def __init__(self):
        self.messages = _ns(stream=self._stream)

    def _stream(self, **kw):
        final = _ns(
            content=[],
            stop_reason="end_turn",
            usage=_ns(
                input_tokens=120,
                output_tokens=30,
                cache_read_input_tokens=80,
            ),
        )
        return _AStream(final)


def test_anthropic_emits_usage_before_stop() -> None:
    p = AnthropicProvider(api_key="k", client=_AClient())
    evs = list(p.stream([{"role": "user", "content": "x"}], [], model="claude-sonnet-4-5-20250929"))
    u = _usage_then_stop(evs)
    assert u == {"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 80}


# --- OpenAI -------------------------------------------------------------


class _OAStream:
    def __iter__(self):
        yield _ns(choices=[_ns(delta=_ns(content="hi", tool_calls=None), finish_reason="stop")])
        # include_usage => a trailing chunk with empty choices + usage
        yield _ns(
            choices=[],
            usage=_ns(
                prompt_tokens=100,
                completion_tokens=20,
                prompt_tokens_details=_ns(cached_tokens=40),
            ),
        )


class _OAClient:
    def __init__(self):
        self.captured: dict = {}

        class _Comp:
            def create(inner, **kw):  # noqa: N805
                self.captured = kw
                return _OAStream()

        self.chat = _ns(completions=_Comp())


def test_openai_emits_usage_and_requests_include_usage() -> None:
    fake = _OAClient()
    p = OpenAIProvider(api_key="k", client=fake)
    evs = list(p.stream([{"role": "user", "content": "x"}], [], model="gpt-4o-mini"))
    u = _usage_then_stop(evs)
    assert u == {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 40}
    assert fake.captured["stream_options"] == {"include_usage": True}


# --- Google -------------------------------------------------------------


class _GClient:
    def __init__(self):
        class _Models:
            def generate_content_stream(inner, **kw):  # noqa: N805
                yield _ns(text="hi", candidates=None, usage_metadata=None)
                yield _ns(
                    text=None,
                    candidates=None,
                    usage_metadata=_ns(
                        prompt_token_count=90,
                        candidates_token_count=15,
                        cached_content_token_count=10,
                    ),
                )

        self.models = _Models()


def test_google_emits_usage_before_stop() -> None:
    from mnemo.providers.google import GoogleProvider

    p = GoogleProvider(api_key="k", client=_GClient())
    evs = list(p.stream([{"role": "user", "content": "x"}], [], model="gemini-2.5-flash"))
    u = _usage_then_stop(evs)
    assert u == {"input_tokens": 90, "output_tokens": 15, "cache_read_input_tokens": 10}


# --- Ollama -------------------------------------------------------------


def test_ollama_emits_usage_from_final_line() -> None:
    from mnemo.providers.ollama import OllamaProvider

    def fake_transport(url, payload):
        yield {"message": {"content": "hi"}, "done": False}
        yield {
            "message": {"content": ""},
            "done": True,
            "prompt_eval_count": 70,
            "eval_count": 12,
        }

    p = OllamaProvider(transport=fake_transport)
    evs = list(p.stream([{"role": "user", "content": "x"}], [], model="llama3.1:8b"))
    u = _usage_then_stop(evs)
    assert u == {"input_tokens": 70, "output_tokens": 12, "cache_read_input_tokens": 0}
