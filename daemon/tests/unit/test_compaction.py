"""v3.1 phase 3: hybrid conversation compaction.

Design 2026-05-15-mnemo-v3.1 S3.3, decided fork 2:

  * ``should_compact`` -- cheap token estimate vs a trigger threshold.
  * native path -- Anthropic + a compaction-capable model streams via
    ``client.beta.messages`` with the ``compact-2026-01-12`` beta +
    ``context_management``; the FULL final content (compaction blocks
    included) is preserved verbatim and replayed next turn (the
    claude-api critical rule).
  * fallback path -- any other provider/model: summarize the oldest
    turns into one pinned ``system`` message, keep the recent tail.

All offline: scripted providers + an injected fake Anthropic client.
"""

from __future__ import annotations

import types

from mnemo.chat import AgentLoop
from mnemo.compaction import (
    estimate_tokens,
    should_compact,
    summarize_prefix,
    supports_native_compaction,
)
from mnemo.providers import EV_STOP, EV_TEXT, BaseProvider
from mnemo.providers.anthropic import AnthropicProvider
from mnemo.store import Store


def _ns(**kw):
    return types.SimpleNamespace(**kw)


# --- token estimate / threshold ----------------------------------------


def test_estimate_tokens_scales_with_content() -> None:
    small = [{"role": "user", "content": "hi"}]
    big = [{"role": "user", "content": "x" * 8000}]
    assert estimate_tokens(small) < estimate_tokens(big)
    assert estimate_tokens(big) >= 1000  # ~chars/4


def test_should_compact_only_over_threshold() -> None:
    msgs = [{"role": "user", "content": "x" * 4000}]  # ~1000 tok
    assert should_compact(msgs, trigger_tokens=10) is True
    assert should_compact(msgs, trigger_tokens=10_000) is False


def test_supports_native_compaction_matrix() -> None:
    assert supports_native_compaction("anthropic", "claude-sonnet-4-6") is True
    assert supports_native_compaction("anthropic", "claude-opus-4-7") is True
    # the project default model is NOT compaction-capable -> fallback
    assert supports_native_compaction("anthropic", "claude-sonnet-4-5-20250929") is False
    assert supports_native_compaction("openai", "gpt-4o-mini") is False
    assert supports_native_compaction("fake", "m") is False


# --- summarize fallback -------------------------------------------------


class _ScriptedProvider(BaseProvider):
    """One scripted stream() reply per call (records prompts seen)."""

    name = "fake"

    def __init__(self, replies: list[str]):
        self._replies = replies
        self._i = 0
        self.seen: list[list[dict]] = []

    def stream(self, messages, tools, *, model, system=None, max_output_tokens=4096):
        self.seen.append(messages)
        reply = self._replies[self._i] if self._i < len(self._replies) else "ok"
        self._i += 1
        yield (EV_TEXT, reply)
        yield (EV_STOP, "end_turn")


def test_summarize_prefix_pins_summary_and_keeps_recent_tail() -> None:
    prov = _ScriptedProvider(["SUMMARY: decided X, node n1, [mnemo:n1]"])
    msgs = [{"role": "user", "content": f"turn {i}"} for i in range(10)]
    new_msgs, summary = summarize_prefix(prov, "m", msgs, keep_recent=3)

    assert "SUMMARY" in summary
    # one pinned system message + exactly the last 3 turns
    assert new_msgs[0]["role"] == "system"
    assert "summary" in new_msgs[0]["content"].lower()
    assert [m["content"] for m in new_msgs[1:]] == ["turn 7", "turn 8", "turn 9"]
    assert len(new_msgs) == 4


def test_summarize_prefix_noop_when_already_short() -> None:
    prov = _ScriptedProvider(["unused"])
    msgs = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
    new_msgs, summary = summarize_prefix(prov, "m", msgs, keep_recent=6)
    assert new_msgs == msgs
    assert summary == ""
    assert prov.seen == []  # provider not called


# --- Anthropic native compaction round-trip (mocked) -------------------


class _CompactStream:
    def __init__(self, final):
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        yield _ns(type="content_block_delta", delta=_ns(type="text_delta", text="ok"))

    def get_final_message(self):
        return self._final


class _BetaClient:
    def __init__(self):
        self.captured: dict = {}

        class _BMsgs:
            def stream(inner, **kw):  # noqa: N805
                self.captured = kw
                final = _ns(
                    content=[
                        _ns(type="compaction", id="cmp_1"),
                        _ns(type="text", text="ok"),
                    ],
                    stop_reason="end_turn",
                    usage=_ns(input_tokens=5, output_tokens=2, cache_read_input_tokens=0),
                )
                return _CompactStream(final)

        self.beta = _ns(messages=_BMsgs())
        # non-compact path must be untouched here
        self.messages = _ns(
            stream=lambda **kw: (_ for _ in ()).throw(AssertionError("used non-beta"))
        )


def test_anthropic_compact_mode_uses_beta_and_emits_raw_content() -> None:
    fake = _BetaClient()
    p = AnthropicProvider(api_key="k", client=fake)
    evs = list(
        p.stream(
            [{"role": "user", "content": "hi"}],
            [],
            model="claude-sonnet-4-6",
            system="frozen",
            compact=True,
        )
    )
    assert fake.captured["betas"] == ["compact-2026-01-12"]
    assert fake.captured["context_management"] == {"edits": [{"type": "compact_20260112"}]}
    # the FULL final content (compaction block included) is surfaced so
    # the loop can preserve it verbatim
    raw = next(e for e in evs if e[0] == "compaction")
    types_ = [b["type"] for b in raw[1]]
    assert "compaction" in types_
    assert "text" in types_
    assert evs[-1] == (EV_STOP, "end_turn")


# --- loop hook ----------------------------------------------------------


def test_loop_summarizes_when_over_threshold(store: Store) -> None:
    """A tiny trigger forces the fallback: the loop summarizes the
    prior turns (extra provider call) before answering, emits a
    'compaction' event, and persists the summary on the conversation."""
    conv = store.create_conversation(name="c", provider="fake", model="m")
    # seed a long history so estimate_tokens clears the tiny trigger
    for _ in range(6):
        store.append_message(conv.id, role="user", content={"text": "x" * 400})
        store.append_message(conv.id, role="assistant", content={"text": "y" * 400})

    prov = _ScriptedProvider(["COMPACT SUMMARY keeps n1", "final answer"])
    loop = AgentLoop(
        store,
        prov,
        model="m",
        system="You are Mnem.",
        compaction_trigger_tokens=50,
    )
    events = list(loop.run(conv.id, "new question"))

    comp = next(e for e in events if e["type"] == "compaction")
    assert comp["mode"] == "summarize"
    assert events[-1]["type"] == "done"
    # provider saw the summarize call first, then the bounded answer call
    assert len(prov.seen) == 2
    assert any(m.get("role") == "system" for m in prov.seen[1])
    # summary persisted on the conversation
    refreshed = store.get_conversation(conv.id)
    assert refreshed.summary_json is not None
    assert "summary" in refreshed.summary_json


def test_loop_no_compaction_under_threshold(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="fake", model="m")
    prov = _ScriptedProvider(["answer"])
    loop = AgentLoop(store, prov, model="m", system="s", compaction_trigger_tokens=100_000)
    events = list(loop.run(conv.id, "hi"))
    assert not any(e["type"] == "compaction" for e in events)
    assert len(prov.seen) == 1  # no extra summarize call
