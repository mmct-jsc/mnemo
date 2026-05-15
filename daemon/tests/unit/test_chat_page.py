"""v3 phase 8: the /chat page (design S6.C).

Surface test (same pattern as test_nebula_progressive): the WebGL/SSE
behaviour can't run in pytest, so we lock the page's structural
contract -- 3-column shell, Alpine state, SSE consumption of
/v1/chat/<id>/events, the citation side panel, tool-call rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Store
from tests.conftest import FakeEmbedder


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def chat_html() -> str:
    path = (
        Path(__file__).resolve().parents[2]
        / "mnemo"
        / "ui"
        / "templates"
        / "chat.html"
    )
    return path.read_text(encoding="utf-8")


def test_chat_page_200s_with_shell(client: TestClient) -> None:
    r = client.get("/chat")
    assert r.status_code == 200
    body = r.text
    assert 'x-data="chatPage()"' in body
    assert "chat-rail" in body  # left conversation rail
    assert "chat-thread" in body  # centre thread
    assert "chat-cite" in body  # right citation panel


def test_chat_page_consumes_the_sse_stream(chat_html: str) -> None:
    # the page must open the in-flight run's event stream
    assert "/v1/chat/" in chat_html
    assert "/events" in chat_html
    # and POST a message to start it
    assert "/message" in chat_html


def test_chat_page_renders_tool_calls_and_citations(chat_html: str) -> None:
    assert "tool_call" in chat_html  # collapsible tool-call rows
    assert "citation" in chat_html  # citation events -> side panel
    # the side panel reuses the v2.2 body renderer for full-fidelity
    assert "mnemoRenderBody" in chat_html


def test_chat_page_has_conversation_rail_and_prompt(chat_html: str) -> None:
    assert "newConversation" in chat_html or "new chat" in chat_html.lower()
    assert "sendMessage" in chat_html
    assert "/v1/chat" in chat_html  # list/create conversations
