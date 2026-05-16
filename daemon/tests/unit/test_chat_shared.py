"""v3.1 phase 6: one chat component, two surfaces + smooth UX.

Surface tests (test_nebula_progressive pattern -- SSE/Alpine can't run
in pytest). Design 2026-05-15-mnemo-v3.1 S3.6:

  * the chat logic is a single shared factory ``window.mnemoChat(opts)``
    in static/chat.js (DRY -- /chat and the phase-7 dock both use it);
    chat.html no longer defines an inline chatPage().
  * streaming is word-smoothed via window.mnemoStreamText (NOT raw
    innerHTML per delta) + a real "working" animation while thinking.
  * the thread is a fixed, bottom-pinned viewport that lazy-loads
    older turns on scroll-up via an IntersectionObserver hitting the
    paginated /messages endpoint.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Store
from tests.conftest import FakeEmbedder

_STATIC = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static"
_TPL = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates"


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def chat_js() -> str:
    return (_STATIC / "chat.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def chat_html() -> str:
    return (_TPL / "chat.html").read_text(encoding="utf-8")


def test_shared_chat_module_exists_and_is_a_factory(chat_js: str) -> None:
    assert "window.mnemoChat" in chat_js
    assert "function mnemoChat(" in chat_js
    # parameterised by surface so /chat + dock share ONE implementation
    assert "surface" in chat_js
    assert "pageContext" in chat_js


def test_chat_page_uses_the_shared_factory_not_an_inline_copy(chat_html: str) -> None:
    assert 'x-data="mnemoChat(' in chat_html
    assert "surface: 'page'" in chat_html or 'surface:"page"' in chat_html
    assert "/static/chat.js" in chat_html
    # the old inline duplicate is gone (DRY)
    assert "function chatPage()" not in chat_html


def test_chat_page_served_with_shared_script(client: TestClient) -> None:
    body = client.get("/chat").text
    assert 'x-data="mnemoChat(' in body
    assert "/static/chat.js" in body
    # served + non-empty
    js = client.get("/static/chat.js")
    assert js.status_code == 200
    assert "mnemoChat" in js.text


def test_streaming_is_word_smoothed_not_raw_innerhtml(chat_js: str) -> None:
    assert "mnemoStreamText" in chat_js
    assert "unit: 'word'" in chat_js or "unit:'word'" in chat_js
    # a real working animation while thinking (not just a text caret)
    assert "mnem-working" in chat_js or "mnem-working" in (
        (_TPL / "chat.html").read_text(encoding="utf-8")
    )


def test_thread_is_pinned_and_lazy_loads_history(chat_js: str) -> None:
    # bottom-pin only when the user is already near the bottom (Claude
    # behaviour), not an unconditional jump
    assert "nearBottom" in chat_js
    # scroll-up lazy history via an observer hitting the paginated API
    assert "IntersectionObserver" in chat_js
    assert "/messages?before=" in chat_js
    assert "loadOlder" in chat_js


def test_shared_module_keeps_the_v30_contract(chat_js: str) -> None:
    # nothing regressed in the extraction: SSE run, citations, tools,
    # drafts, permission, body renderer, moods
    for token in (
        "/events",
        "/message",
        "tool_call",
        "citation",
        "mnemoRenderBody",
        "extractDrafts",
        "permission_request",
        "mnemo-mnem-mood",
    ):
        assert token in chat_js, token
