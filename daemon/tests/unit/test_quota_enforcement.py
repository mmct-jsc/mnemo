"""Task 2.5: HTTP 429 from /v1/query when a key is over its monthly quota.

Locks the rejection-shape contract:

1. **Store-level**: ``check_quota`` returns ``(False, reason)`` when
   ``queries >= max_queries`` OR ``tokens >= max_tokens``. Returns
   ``(True, None)`` when no quota row exists (open-billing posture).
2. **Endpoint-level**: pre-handler check raises 429 with
   ``Retry-After`` pointing at the start of next UTC month so
   clients know exactly when their bucket resets.
3. **Anti-goal preserved**: flag-off + loopback exemption skip the
   quota check entirely. Self-host never gets a 429.

Pairs with Task 2.3 (auth) + Task 2.4 (metering): the quota check
runs only when ``api_key_id`` is not None.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from mnemo import config, server
from mnemo.config import Config
from mnemo.server import _seconds_until_next_month_utc, create_app
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
            body="quota test seed",
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


def _set_quota(store: Store, key_id: str, max_queries: int, max_tokens: int) -> None:
    store.conn.execute(
        "INSERT INTO quota (api_key_id, period, max_queries, max_tokens) VALUES (?, ?, ?, ?)",
        (key_id, "monthly", max_queries, max_tokens),
    )
    store.conn.commit()


def _set_usage(store: Store, key_id: str, period: str, queries: int, tokens: int) -> None:
    store.conn.execute(
        "INSERT INTO usage_period (api_key_id, period, queries, tokens, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (key_id, period, queries, tokens, 1),
    )
    store.conn.commit()


# --- Store-level: check_quota -------------------------------------------


def test_no_quota_row_is_open_billing(store: Store) -> None:
    """No quota set -> never blocked. Operator can leave keys
    quota-less for usage-tracking-only deployments."""
    _, key_id = store.create_api_key("partner-A")
    _set_usage(store, key_id, "2026-05", queries=10_000, tokens=999_999)
    allowed, reason = store.check_quota(key_id, "2026-05")
    assert allowed is True
    assert reason is None


def test_check_quota_at_queries_limit_blocks(store: Store) -> None:
    _, key_id = store.create_api_key("partner-A")
    _set_quota(store, key_id, max_queries=100, max_tokens=200_000)
    _set_usage(store, key_id, "2026-05", queries=100, tokens=50_000)
    allowed, reason = store.check_quota(key_id, "2026-05")
    assert allowed is False
    assert "queries" in (reason or "").lower()


def test_check_quota_at_tokens_limit_blocks(store: Store) -> None:
    _, key_id = store.create_api_key("partner-A")
    _set_quota(store, key_id, max_queries=10_000, max_tokens=50_000)
    _set_usage(store, key_id, "2026-05", queries=10, tokens=50_000)
    allowed, reason = store.check_quota(key_id, "2026-05")
    assert allowed is False
    assert "tokens" in (reason or "").lower()


def test_check_quota_under_both_dimensions_allows(store: Store) -> None:
    _, key_id = store.create_api_key("partner-A")
    _set_quota(store, key_id, max_queries=100, max_tokens=200_000)
    _set_usage(store, key_id, "2026-05", queries=50, tokens=10_000)
    allowed, reason = store.check_quota(key_id, "2026-05")
    assert allowed is True
    assert reason is None


def test_check_quota_other_periods_dont_block_this_period(store: Store) -> None:
    """A key that maxed out last month gets a fresh budget this month."""
    _, key_id = store.create_api_key("partner-A")
    _set_quota(store, key_id, max_queries=100, max_tokens=200_000)
    _set_usage(store, key_id, "2026-04", queries=999, tokens=999_999)
    allowed, _ = store.check_quota(key_id, "2026-05")
    assert allowed is True, "previous-period usage must not block current period"


def test_check_quota_no_usage_row_allows(store: Store) -> None:
    """Quota set + no usage yet -> allowed. Catches the
    no-usage-row branch of the LEFT JOIN."""
    _, key_id = store.create_api_key("partner-A")
    _set_quota(store, key_id, max_queries=100, max_tokens=200_000)
    allowed, _ = store.check_quota(key_id, "2026-05")
    assert allowed is True


# --- Retry-After computation -------------------------------------------


def test_retry_after_is_positive_and_under_a_month(store: Store) -> None:
    secs = _seconds_until_next_month_utc()
    # 31 days max, 1 minimum.
    assert 1 <= secs <= 31 * 24 * 3600


# --- Endpoint-level: 429 with Retry-After --------------------------------


def test_query_returns_429_when_over_queries_quota(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_hosted_auth(monkeypatch)
    _seed_one_node(store)
    raw_key, key_id = store.create_api_key("partner-A")
    _set_quota(store, key_id, max_queries=1, max_tokens=200_000)
    # Pre-fill current period at the quota
    period = time.strftime("%Y-%m", time.gmtime())
    _set_usage(store, key_id, period, queries=1, tokens=0)

    r = daemon_client.post(
        "/v1/query",
        json={"prompt": "seed", "k": 1, "budget_tokens": 200},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 429, r.text
    assert "queries" in r.text.lower()
    assert "Retry-After" in r.headers
    retry = int(r.headers["Retry-After"])
    assert retry >= 1


def test_query_returns_429_when_over_tokens_quota(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_hosted_auth(monkeypatch)
    _seed_one_node(store)
    raw_key, key_id = store.create_api_key("partner-A")
    _set_quota(store, key_id, max_queries=10_000, max_tokens=100)
    period = time.strftime("%Y-%m", time.gmtime())
    _set_usage(store, key_id, period, queries=5, tokens=100)

    r = daemon_client.post(
        "/v1/query",
        json={"prompt": "seed", "k": 1, "budget_tokens": 200},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 429, r.text
    assert "tokens" in r.text.lower()


def test_query_with_quota_under_limit_allowed(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_hosted_auth(monkeypatch)
    _seed_one_node(store)
    raw_key, key_id = store.create_api_key("partner-A")
    _set_quota(store, key_id, max_queries=100, max_tokens=200_000)
    # No pre-filled usage; quota check passes
    r = daemon_client.post(
        "/v1/query",
        json={"prompt": "seed", "k": 1, "budget_tokens": 200},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 200, r.text


# --- Anti-goal preservation ---------------------------------------------


def test_flag_off_never_429s_even_with_quota_set(
    daemon_client: TestClient,
    store: Store,
) -> None:
    """Anti-goal: flag off -> no quota check fires no matter what
    state the quota + usage tables contain."""
    _seed_one_node(store)
    _, key_id = store.create_api_key("partner-A")
    _set_quota(store, key_id, max_queries=1, max_tokens=1)
    period = time.strftime("%Y-%m", time.gmtime())
    _set_usage(store, key_id, period, queries=999, tokens=999)

    # Flag off (default) -> the auth dep returns None -> quota
    # check is skipped entirely.
    r = daemon_client.post(
        "/v1/query",
        json={"prompt": "seed", "k": 1, "budget_tokens": 200},
    )
    assert r.status_code == 200, r.text


def test_loopback_exemption_skips_quota_check(
    daemon_client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loopback exemption -> api_key_id is None -> quota check
    skipped even when flag is on."""
    _enable_hosted_auth(monkeypatch)
    _force_loopback(monkeypatch)
    _seed_one_node(store)
    _, key_id = store.create_api_key("partner-A")
    _set_quota(store, key_id, max_queries=1, max_tokens=1)
    period = time.strftime("%Y-%m", time.gmtime())
    _set_usage(store, key_id, period, queries=999, tokens=999)

    r = daemon_client.post(
        "/v1/query",
        json={"prompt": "seed", "k": 1, "budget_tokens": 200},
    )
    assert r.status_code == 200, r.text
