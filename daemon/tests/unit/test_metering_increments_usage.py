"""Task 2.4: post-/v1/query metering hook writes per-key usage rows.

Locks two contracts:

1. **Anti-goal #1**: a self-host loopback request (flag off OR loopback
   exemption) MUST NOT write a row to ``usage_period``. The free
   local-first plugin never gets a billing row.
2. **Hosted contract**: a non-loopback key-authenticated request DOES
   write a row; second request to the same key in the same period
   atomically increments queries + tokens (UPSERT, not race-prone
   read-modify-write).

Pairs with Task 2.3's auth dependency (the metering hook reads
``api_key_id`` returned by ``api_key_or_local``).
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from mnemo import config, server
from mnemo.config import Config
from mnemo.server import create_app
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder


@pytest.fixture
def daemon_client(store: Store) -> TestClient:
    return TestClient(create_app(store=store, embedder=FakeEmbedder()))


def _seed_one_node(store: Store) -> None:
    store.upsert_node(
        Node(
            id="n1",
            type="memory_feedback",
            name="seed",
            description=None,
            body="seed body for metering tests",
            source_path="/seed/n1.md",
            source_kind="memory_dir",
            project_key=None,
            frontmatter_json=None,
            hash="h",
            created_at=1,
            updated_at=1,
        )
    )


def _enable_hosted_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Config()
    cfg.hosted_auth_enabled = True
    monkeypatch.setattr(config, "load", lambda: cfg)


def _force_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_is_loopback", lambda host: True)


def _usage_rows(store: Store) -> list[dict]:
    return [
        dict(r)
        for r in store.conn.execute(
            "SELECT api_key_id, period, queries, tokens FROM usage_period"
        ).fetchall()
    ]


# --- Store-level: the UPSERT primitive itself --------------------------


def test_record_usage_inserts_first_then_upserts(store: Store) -> None:
    """First call creates the row; subsequent calls in the same
    (api_key_id, period) atomically increment via ON CONFLICT DO
    UPDATE -- no read-modify-write race."""
    _, key_id = store.create_api_key("partner-A")
    store.record_usage(key_id, "2026-05", queries=1, tokens=120)
    store.record_usage(key_id, "2026-05", queries=1, tokens=80)
    store.record_usage(key_id, "2026-05", queries=3, tokens=300)

    rows = _usage_rows(store)
    assert len(rows) == 1, f"expected one upserted row; got {len(rows)}"
    r = rows[0]
    assert r["api_key_id"] == key_id
    assert r["period"] == "2026-05"
    assert r["queries"] == 5
    assert r["tokens"] == 500


def test_record_usage_separate_periods_separate_rows(store: Store) -> None:
    _, key_id = store.create_api_key("partner-A")
    store.record_usage(key_id, "2026-05", queries=10, tokens=1_000)
    store.record_usage(key_id, "2026-06", queries=1, tokens=100)

    rows = sorted(_usage_rows(store), key=lambda r: r["period"])
    assert len(rows) == 2
    assert (rows[0]["period"], rows[0]["queries"]) == ("2026-05", 10)
    assert (rows[1]["period"], rows[1]["queries"]) == ("2026-06", 1)


# --- Anti-goal: self-host loopback never gets metered ------------------


def test_query_with_flag_off_writes_no_usage_row(
    daemon_client: TestClient,
    store: Store,
) -> None:
    """Anti-goal-critical: default behavior (flag off) does NOT
    write to usage_period regardless of how many queries fire.
    The free local-first plugin stays free."""
    _seed_one_node(store)
    for _ in range(3):
        r = daemon_client.post(
            "/v1/query",
            json={"prompt": "seed", "k": 1, "budget_tokens": 200},
        )
        assert r.status_code == 200
    assert _usage_rows(store) == [], (
        "flag-off /v1/query MUST NOT meter; self-host plugin stays free"
    )


def test_query_with_loopback_exemption_writes_no_usage_row(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with hosted_auth_enabled=True, a loopback request takes
    the exemption path -> api_key_id is None -> no usage row."""
    _enable_hosted_auth(monkeypatch)
    _force_loopback(monkeypatch)
    _seed_one_node(store)
    r = daemon_client.post(
        "/v1/query",
        json={"prompt": "seed", "k": 1, "budget_tokens": 200},
    )
    assert r.status_code == 200
    assert _usage_rows(store) == [], (
        "loopback-exempt requests must not be metered (they are local UI / "
        "CLI / plugin calls, not paying hosted-tier consumers)"
    )


# --- Hosted-tier contract: key-authenticated requests are metered ------


def test_key_authenticated_query_writes_usage_row(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_hosted_auth(monkeypatch)
    _seed_one_node(store)
    raw_key, key_id = store.create_api_key("partner-A")

    r = daemon_client.post(
        "/v1/query",
        json={"prompt": "seed", "k": 1, "budget_tokens": 200},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 200, r.text

    rows = _usage_rows(store)
    assert len(rows) == 1, f"expected exactly one usage row; got {rows}"
    r0 = rows[0]
    assert r0["api_key_id"] == key_id
    assert r0["queries"] == 1
    # tokens reflects retrieve.query's tokens_used (could be 0 on a
    # FakeEmbedder + minimal-seed test corpus; what matters is the
    # column is actually written, not the magnitude).
    assert r0["tokens"] >= 0
    # Period is the current UTC YYYY-MM.
    assert r0["period"] == time.strftime("%Y-%m", time.gmtime())


def test_two_queries_increment_atomically(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two consecutive requests on the same key in the same period
    leave a single row with queries=2 (not two rows / not a race)."""
    _enable_hosted_auth(monkeypatch)
    _seed_one_node(store)
    raw_key, _ = store.create_api_key("partner-A")

    for _ in range(2):
        r = daemon_client.post(
            "/v1/query",
            json={"prompt": "seed", "k": 1, "budget_tokens": 200},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert r.status_code == 200

    rows = _usage_rows(store)
    assert len(rows) == 1, f"two requests on one key/period must yield ONE upserted row; got {rows}"
    assert rows[0]["queries"] == 2


def test_two_keys_get_separate_rows(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_hosted_auth(monkeypatch)
    _seed_one_node(store)
    raw_a, id_a = store.create_api_key("partner-A")
    raw_b, id_b = store.create_api_key("partner-B")

    for raw in (raw_a, raw_b):
        r = daemon_client.post(
            "/v1/query",
            json={"prompt": "seed", "k": 1, "budget_tokens": 200},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert r.status_code == 200

    rows = {r["api_key_id"]: r for r in _usage_rows(store)}
    assert set(rows) == {id_a, id_b}
    assert rows[id_a]["queries"] == 1
    assert rows[id_b]["queries"] == 1
