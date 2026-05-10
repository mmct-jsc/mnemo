"""Tests for patch() / unpatch() with fake SDK clients.

We don't depend on real openai / anthropic / google / ollama at test
time. Each test builds a fake client that *quacks like* the real one
(matching module path + class name + attribute structure), patches it,
and verifies the call was wrapped.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from contextlib import contextmanager

import httpx
import pytest

import mnemo_middleware as mm
from mnemo_middleware import retrieve as retrieve_mod


@contextmanager
def _mock_daemon(handler: Callable[[httpx.Request], httpx.Response]):
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def fake_client(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    retrieve_mod.httpx.Client = fake_client  # type: ignore[assignment]
    try:
        yield
    finally:
        retrieve_mod.httpx.Client = real_client  # type: ignore[assignment]


def _hits_daemon(payload_hits: list[dict] | None = None):
    payload = {
        "hits": payload_hits
        if payload_hits is not None
        else [
            {
                "id": "x1",
                "citation": "[mnemo:x1]",
                "type": "memory_user",
                "name": "rule",
                "description": "Always cite",
                "body": "rule body",
            }
        ],
        "intent_tags": [],
        "tokens_used": 10,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return handler


# --- Build a fake OpenAI client by injecting a fake module --------------


def _make_fake_openai_client():
    """Fake openai.OpenAI lookalike. The shim matches on module path
    starting with 'openai' so we register a stub module."""
    fake_mod = types.ModuleType("openai._client_stub_for_tests")
    sys.modules.setdefault("openai", types.ModuleType("openai"))
    sys.modules["openai._client_stub_for_tests"] = fake_mod

    captured = {"calls": []}

    def create(**kwargs):
        captured["calls"].append(kwargs)
        return {"id": "resp-fake", "ok": True}

    completions = types.SimpleNamespace(create=create)
    chat = types.SimpleNamespace(completions=completions)

    class OpenAI:
        pass

    OpenAI.__module__ = "openai._client_stub_for_tests"
    client = OpenAI()
    client.chat = chat  # type: ignore[attr-defined]
    return client, captured


def test_patch_openai_injects_system_message_on_first_call() -> None:
    client, captured = _make_fake_openai_client()
    mm.patch(client)

    with _mock_daemon(_hits_daemon()):
        client.chat.completions.create(  # type: ignore[attr-defined]
            model="gpt-4o",
            messages=[{"role": "user", "content": "what's the rule?"}],
        )

    sent = captured["calls"][0]["messages"]
    assert sent[0]["role"] == "system"
    assert "[mnemo:x1]" in sent[0]["content"]
    assert sent[1]["role"] == "user"


def test_patch_openai_merges_existing_system_message() -> None:
    client, captured = _make_fake_openai_client()
    mm.patch(client)

    with _mock_daemon(_hits_daemon()):
        client.chat.completions.create(  # type: ignore[attr-defined]
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hi"},
            ],
        )

    sent = captured["calls"][0]["messages"]
    # Single system message, both contents merged.
    sys_msgs = [m for m in sent if m["role"] == "system"]
    assert len(sys_msgs) == 1
    assert "[mnemo:x1]" in sys_msgs[0]["content"]
    assert "You are helpful." in sys_msgs[0]["content"]


def test_unpatch_restores_original_create() -> None:
    client, captured = _make_fake_openai_client()
    original = client.chat.completions.create  # type: ignore[attr-defined]
    mm.patch(client)
    assert client.chat.completions.create is not original  # type: ignore[attr-defined]
    mm.unpatch(client)
    assert client.chat.completions.create is original  # type: ignore[attr-defined]


def test_patch_unsupported_client_raises() -> None:
    class Random:
        pass

    with pytest.raises(mm.UnsupportedClient):
        mm.patch(Random())


def test_invalid_mode_raises() -> None:
    client, _ = _make_fake_openai_client()
    with pytest.raises(ValueError, match="mode must be"):
        mm.patch(client, mode="bogus")


# --- mode='auto' bookkeeping ---------------------------------------------


def test_auto_mode_skips_reinject_on_continued_conversation() -> None:
    client, captured = _make_fake_openai_client()
    mm.patch(client, mode="auto")

    request_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        request_count["n"] += 1
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "id": f"h{request_count['n']}",
                        "citation": f"[mnemo:h{request_count['n']}]",
                        "type": "memory_user",
                        "name": "n",
                        "description": "d",
                        "body": "b",
                    }
                ],
                "intent_tags": [],
                "tokens_used": 1,
            },
        )

    with _mock_daemon(handler):
        # Turn 1: first user message, mnemo should fetch.
        client.chat.completions.create(  # type: ignore[attr-defined]
            model="gpt-4o",
            messages=[{"role": "user", "content": "what's the rule?"}],
        )
        # Turn 2: same conversation continuing (history grows), auto
        # should reuse the prior block instead of re-querying.
        client.chat.completions.create(  # type: ignore[attr-defined]
            model="gpt-4o",
            messages=[
                {"role": "user", "content": "what's the rule?"},
                {"role": "assistant", "content": "the rule is..."},
                {"role": "user", "content": "more detail?"},
            ],
        )

    assert request_count["n"] == 1, "auto mode should not re-fetch on continuation"


def test_every_mode_fetches_each_call() -> None:
    client, _ = _make_fake_openai_client()
    mm.patch(client, mode="every")

    request_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        request_count["n"] += 1
        return httpx.Response(
            200,
            json={
                "hits": [{"id": "x", "citation": "[mnemo:x]", "name": "n"}],
                "intent_tags": [],
                "tokens_used": 1,
            },
        )

    with _mock_daemon(handler):
        for _ in range(3):
            client.chat.completions.create(  # type: ignore[attr-defined]
                model="gpt-4o",
                messages=[{"role": "user", "content": "q"}],
            )

    assert request_count["n"] == 3, "every mode must fetch on every call"


def test_once_mode_fetches_only_first_call() -> None:
    client, _ = _make_fake_openai_client()
    mm.patch(client, mode="once")

    request_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        request_count["n"] += 1
        return httpx.Response(
            200,
            json={
                "hits": [{"id": "x", "citation": "[mnemo:x]", "name": "n"}],
                "intent_tags": [],
                "tokens_used": 1,
            },
        )

    with _mock_daemon(handler):
        for _ in range(5):
            client.chat.completions.create(  # type: ignore[attr-defined]
                model="gpt-4o",
                messages=[{"role": "user", "content": "q"}],
            )

    assert request_count["n"] == 1, "once mode must fetch exactly once"


# --- daemon failure must not break the model call ------------------------


def test_daemon_down_does_not_break_model_call() -> None:
    client, captured = _make_fake_openai_client()
    mm.patch(client)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("daemon down")

    with _mock_daemon(handler):
        result = client.chat.completions.create(  # type: ignore[attr-defined]
            model="gpt-4o",
            messages=[{"role": "user", "content": "anything"}],
        )

    assert result == {"id": "resp-fake", "ok": True}
    # Original messages preserved (no system message injected when block is empty).
    sent = captured["calls"][0]["messages"]
    assert sent == [{"role": "user", "content": "anything"}]


def test_re_patch_updates_mode() -> None:
    client, _ = _make_fake_openai_client()
    mm.patch(client, mode="auto")
    state = client.__mnemo_patch_state__  # type: ignore[attr-defined]
    assert state.mode == "auto"
    mm.patch(client, mode="every")
    assert state.mode == "every"
    # State object preserved (not double-patched).
    assert client.__mnemo_patch_state__ is state  # type: ignore[attr-defined]
