"""v3.1 phase 7: draggable mini-chat dock + Chat tab + Mnem branding.

Surface tests (test_nebula_progressive pattern). Design
2026-05-15-mnemo-v3.1 S3.6 (dock) + S3.7 (branding):

  * the dock is a draggable, edge-snapping, full mini-chat panel
    (the SAME mnemoChat factory as /chat, surface:'dock'), hidden on
    /chat, position persisted in localStorage['mnem.pos'].
  * a Chat tab in the top nav.
  * the Mnem mark is the favicon + nav logo (the generic data-URI "M"
    is gone).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Store
from tests.conftest import FakeEmbedder

_UI = Path(__file__).resolve().parents[2] / "mnemo" / "ui"
BASE_HTML = (_UI / "templates" / "base.html").read_text(encoding="utf-8")
MARK_SVG = _UI / "static" / "mnem" / "mark.svg"


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


# --- branding -----------------------------------------------------------


def test_mark_svg_exists_and_is_a_clean_glyph() -> None:
    assert MARK_SVG.is_file()
    svg = MARK_SVG.read_text(encoding="utf-8")
    assert "<svg" in svg
    # simplified: a constellation, NOT the full mascot face
    assert 'aria-label="mnemo"' in svg
    # no eyes / mouth (the mascot has paired eye circles + a q-curve mouth)
    assert svg.count('fill="#7ee7e0"') <= 2


def test_favicon_is_the_mnem_mark_not_a_data_uri(client: TestClient) -> None:
    body = client.get("/").text
    assert "/static/mnem/mark.svg" in body
    assert 'rel="icon"' in body
    # the generic data-URI "M" placeholder is gone
    assert "data:image/svg+xml,%3Csvg" not in body
    assert MARK_SVG.is_file()
    assert client.get("/static/mnem/mark.svg").status_code == 200


def test_nav_brand_uses_the_mark(client: TestClient) -> None:
    body = client.get("/").text
    # the brand becomes the mark + wordmark (not bare text)
    assert 'class="brand"' in body
    brand_region = body.split('class="brand"', 1)[1][:200]
    assert "/static/mnem/mark.svg" in brand_region


def test_nav_has_a_chat_tab(client: TestClient) -> None:
    body = client.get("/").text
    nav = body.split("<nav>", 1)[1].split("</nav>", 1)[0]
    assert 'href="/chat"' in nav
    assert ">Chat<" in nav


# --- draggable mini-chat dock -------------------------------------------


def test_dock_is_a_full_mini_chat_not_a_redirect() -> None:
    # the dock embeds the SHARED chat component (surface:'dock', plus
    # the auto-attached pageContext), not a bare "Open chat" deep link
    assert "mnemoChat({ surface: 'dock'" in BASE_HTML
    assert "pageContext" in BASE_HTML
    assert "/static/chat.js" in BASE_HTML


def test_dock_is_draggable_and_edge_snaps_persisted() -> None:
    assert "pointerdown" in BASE_HTML  # drag start
    assert "mnem.pos" in BASE_HTML  # persisted {x,y,edge}
    assert "snap" in BASE_HTML.lower()  # edge-snap on release


def test_dock_is_hidden_on_chat_page() -> None:
    # redundant on /chat -- the full surface is already there
    assert "startsWith('/chat')" in BASE_HTML
    assert "hiddenOnChat" in BASE_HTML or "hideDock" in BASE_HTML


def test_chat_route_marks_nav_active(client: TestClient) -> None:
    body = client.get("/chat").text
    # the Chat tab is highlighted on /chat
    assert 'href="/chat"' in body
