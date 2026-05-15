"""v3 phase 9: the Mnem companion dock (design S6.A/B/D).

The dock lives in base.html so it's on EVERY page; 5 CSS mood states
(pipeline 18 -- DOM overlay, no canvas); dock state persists via
localStorage; proactive nudges are opt-in. Surface test + asset
presence (the animation can't run in pytest).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Store
from tests.conftest import FakeEmbedder

MOODS = ("idle", "thinking", "speaking", "waiting", "alert")


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


def _static(name: str) -> Path:
    return Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static" / "mnem" / name


def test_dock_present_on_every_page(client: TestClient) -> None:
    # base.html renders on the dashboard (a NON-chat page) -> the dock
    # must be there too, proving it's in the shared layout.
    body = client.get("/").text
    assert 'x-data="mnemDock()"' in body
    assert "mnem-dock" in body
    assert "localStorage" in body
    assert "mnem.docked" in body


def test_five_mood_states_referenced(client: TestClient) -> None:
    body = client.get("/").text
    for mood in MOODS:
        assert f"mnem-{mood}" in body, mood


def test_five_mood_svgs_exist() -> None:
    for mood in MOODS:
        p = _static(f"{mood}.svg")
        assert p.is_file(), p
        assert "<svg" in p.read_text(encoding="utf-8")


def test_proactive_nudge_is_opt_in(client: TestClient) -> None:
    body = client.get("/").text
    # the dwell-timer nudge must check the companion 'proactive' pref
    assert "proactive" in body
    assert "/v1/settings" in body
