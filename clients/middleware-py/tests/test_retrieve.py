"""Unit tests for retrieve_context against an httpx MockTransport.

No live daemon, no network. The middleware's contract is:
- empty body / network error / timeout -> "" (no exception)
- happy path -> markdown block with citations + intent line
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import contextmanager

import httpx
import pytest

from mnemo_middleware import retrieve_context
from mnemo_middleware import retrieve as retrieve_mod


@contextmanager
def _mock_daemon(handler: Callable[[httpx.Request], httpx.Response]):
    """Patch httpx.Client to use a MockTransport for the duration."""
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


# --- happy path -----------------------------------------------------------


def test_returns_formatted_block_on_success() -> None:
    payload = {
        "hits": [
            {
                "id": "abc123",
                "citation": "[mnemo:abc123]",
                "type": "memory_user",
                "name": "no_co_author",
                "description": "Never include Co-Authored-By trailer in commits.",
                "body": "Hard rule. Drop the line entirely.",
            }
        ],
        "intent_tags": ["preference"],
        "tokens_used": 42,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/query"
        body = json.loads(request.content)
        assert body["prompt"] == "what's our commit policy?"
        return httpx.Response(200, json=payload)

    with _mock_daemon(handler):
        out = retrieve_context("what's our commit policy?")

    assert out.startswith("## Relevant memory (mnemo)")
    assert "[mnemo:abc123]" in out
    assert "no_co_author" in out
    assert "Co-Authored-By" in out
    assert "intent: preference" in out
    assert "tokens used: 42" in out


def test_passes_optional_project_key_through() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"hits": [], "intent_tags": [], "tokens_used": 0})

    with _mock_daemon(handler):
        retrieve_context("hi", project_key="my-proj", k=3, budget_tokens=400)

    assert captured["body"]["project_key"] == "my-proj"
    assert captured["body"]["k"] == 3
    assert captured["body"]["budget_tokens"] == 400


def test_omits_project_key_when_none() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"hits": [], "intent_tags": [], "tokens_used": 0})

    with _mock_daemon(handler):
        retrieve_context("hi")

    assert "project_key" not in captured["body"]


# --- empty / failure cases -- middleware must be additive ----------------


def test_empty_hits_returns_empty_string() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": [], "intent_tags": [], "tokens_used": 0})

    with _mock_daemon(handler):
        out = retrieve_context("anything")

    assert out == ""


def test_500_error_returns_empty_string_no_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    with _mock_daemon(handler):
        out = retrieve_context("anything")

    assert out == ""


def test_timeout_returns_empty_string_no_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout")

    with _mock_daemon(handler):
        out = retrieve_context("anything")

    assert out == ""


def test_connection_error_returns_empty_string() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("daemon down")

    with _mock_daemon(handler):
        out = retrieve_context("anything")

    assert out == ""


def test_invalid_json_returns_empty_string() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json {")

    with _mock_daemon(handler):
        out = retrieve_context("anything")

    assert out == ""


# --- env-var defaults ----------------------------------------------------


def test_env_overrides_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MNEMO_DEFAULT_BUDGET", "1500")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"hits": [], "intent_tags": [], "tokens_used": 0})

    with _mock_daemon(handler):
        retrieve_context("hi")

    assert captured["body"]["budget_tokens"] == 1500


def test_env_overrides_daemon_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MNEMO_DAEMON_URL", "http://127.0.0.1:9999")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"hits": [], "intent_tags": [], "tokens_used": 0})

    with _mock_daemon(handler):
        retrieve_context("hi")

    assert captured["url"].startswith("http://127.0.0.1:9999/")
