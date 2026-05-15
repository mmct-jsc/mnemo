"""v3 phase 3: chat REST + SSE event stream (design S5).

Offline: a scripted provider is injected via
``app.state.mnemo_state.chat_provider_factory`` so the agent loop runs
without a network or key. SSE is consumed through TestClient (the
StreamingResponse body is buffered to completion).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from mnemo.providers import EV_STOP, EV_TEXT, BaseProvider
from mnemo.server import create_app
from mnemo.store import Store
from tests.conftest import FakeEmbedder


class ScriptedProvider(BaseProvider):
    name = "scripted"

    def stream(self, messages, tools, *, model, system=None, max_output_tokens=4096):
        yield (EV_TEXT, "Hello from ")
        yield (EV_TEXT, "Mnem [mnemo:n1]")
        yield (EV_STOP, "end_turn")


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        c.app.state.mnemo_state.chat_provider_factory = lambda name, **kw: ScriptedProvider()
        yield c


def _new_conv(client: TestClient, **body) -> dict:
    r = client.post("/v1/chat", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_create_lists_and_defaults(client: TestClient) -> None:
    conv = _new_conv(client, name="MQTT dig", project_key="P1")
    assert len(conv["id"]) == 32
    assert conv["provider"] == "anthropic"  # design S4 default
    assert conv["model"]  # a default model string

    listed = client.get("/v1/chat", params={"project_key": "P1"}).json()
    assert conv["id"] in {c["id"] for c in listed}
    # project filter excludes other projects
    assert client.get("/v1/chat", params={"project_key": "P2"}).json() == []


def test_get_patch_delete_conversation(client: TestClient) -> None:
    conv = _new_conv(client, name="old")
    cid = conv["id"]

    got = client.get(f"/v1/chat/{cid}")
    assert got.status_code == 200
    assert got.json()["name"] == "old"
    assert got.json()["messages"] == []

    patched = client.patch(f"/v1/chat/{cid}", json={"name": "new", "model": "m2"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "new"
    assert patched.json()["model"] == "m2"

    assert client.delete(f"/v1/chat/{cid}").status_code == 200
    assert cid not in {c["id"] for c in client.get("/v1/chat").json()}
    # still fetchable by id, now archived
    assert client.get(f"/v1/chat/{cid}").json()["archived_at"] is not None


def test_message_then_sse_stream_runs_the_loop(client: TestClient) -> None:
    cid = _new_conv(client, name="c")["id"]

    r = client.post(f"/v1/chat/{cid}/message", json={"text": "hello"})
    assert r.status_code == 200
    assert r.json()["stream_url"] == f"/v1/chat/{cid}/events"

    ev = client.get(f"/v1/chat/{cid}/events")
    assert ev.status_code == 200
    assert ev.headers["content-type"].startswith("text/event-stream")
    body = ev.text
    assert "event: thinking" in body
    assert "event: text_delta" in body
    assert "event: citation" in body
    assert "event: done" in body

    msgs = client.get(f"/v1/chat/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"]["text"] == "hello"
    assert "Hello from Mnem" in msgs[1]["content"]["text"]
    assert msgs[1]["content"]["citations"] == ["n1"]


def test_message_conflicts_when_run_in_flight(client: TestClient) -> None:
    cid = _new_conv(client, name="c")["id"]
    state = client.app.state.mnemo_state
    # Simulate an in-flight run by holding the per-conversation lock.
    from mnemo.server import _chat_lock

    lock = _chat_lock(state, cid)
    assert lock.acquire(blocking=False)
    try:
        r = client.post(f"/v1/chat/{cid}/message", json={"text": "again"})
        assert r.status_code == 409
        assert r.json()["detail"]["stream_url"] == f"/v1/chat/{cid}/events"
    finally:
        lock.release()


def test_cancel_is_idempotent(client: TestClient) -> None:
    cid = _new_conv(client, name="c")["id"]
    assert client.post(f"/v1/chat/{cid}/cancel").status_code == 200
    assert client.post(f"/v1/chat/{cid}/cancel").status_code == 200


def test_unknown_conversation_404s(client: TestClient) -> None:
    assert client.get("/v1/chat/nope").status_code == 404
    assert client.patch("/v1/chat/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/v1/chat/nope").status_code == 404
    assert client.post("/v1/chat/nope/message", json={"text": "h"}).status_code == 404
    assert client.get("/v1/chat/nope/events").status_code == 404


def test_events_idle_when_nothing_pending(client: TestClient) -> None:
    cid = _new_conv(client, name="c")["id"]
    ev = client.get(f"/v1/chat/{cid}/events")
    assert ev.status_code == 200
    assert "event: idle" in ev.text
