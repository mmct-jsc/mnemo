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
    path = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates" / "chat.html"
    return path.read_text(encoding="utf-8")


def test_chat_page_200s_with_shell(client: TestClient) -> None:
    r = client.get("/chat")
    assert r.status_code == 200
    body = r.text
    # v3.1: the page uses the SHARED mnemoChat factory (one impl, two
    # surfaces) -- the inline chatPage() copy was removed.
    assert 'x-data="mnemoChat(' in body
    assert "chat-rail" in body  # left conversation rail
    assert "chat-thread" in body  # centre thread
    assert "chat-cite" in body  # right citation panel


def test_chat_page_loads_the_shared_module(chat_html: str) -> None:
    assert "/static/chat.js" in chat_html
    assert "function chatPage()" not in chat_html  # no inline duplicate


def test_chat_page_renders_tool_calls_and_drafts(chat_html: str) -> None:
    assert "tool_call" in chat_html  # collapsible tool-call rows
    assert "draft-card" in chat_html  # mnemo-draft one-click save
    assert "chat-cite" in chat_html  # citation side panel shell


def test_chat_page_has_conversation_rail_and_prompt(chat_html: str) -> None:
    assert "newConversation" in chat_html or "new chat" in chat_html.lower()
    assert "sendMessage" in chat_html  # composer @submit
    assert "load-older" in chat_html  # pinned-thread lazy-history sentinel
