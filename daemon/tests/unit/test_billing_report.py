"""Task 2.6: ``mnemo billing report --period`` + Store.billing_report.

Two layers:
- Store-level: the SQL aggregation matches the documented contract --
  key with no usage shows zeros; quota fields default to 0 when unset;
  over_quota flips correctly per dimension; rows are ordered by name.
- CLI-level: ``mnemo billing report --period <YYYY-MM>`` emits CSV with
  the stable column header, one row per key, deterministically ordered.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnemo import paths
from mnemo.cli import app
from mnemo.store import Store


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _sandbox(isolated_mnemo_home: Path) -> Path:
    return isolated_mnemo_home


def _live_store() -> Store:
    paths.ensure_runtime_dirs()
    return Store(paths.db_path())


def _seed_usage(store: Store, key_id: str, period: str, queries: int, tokens: int) -> None:
    store.conn.execute(
        "INSERT INTO usage_period (api_key_id, period, queries, tokens, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (key_id, period, queries, tokens, 1),
    )
    store.conn.commit()


def _seed_quota(store: Store, key_id: str, max_queries: int, max_tokens: int) -> None:
    store.conn.execute(
        "INSERT INTO quota (api_key_id, period, max_queries, max_tokens) VALUES (?, ?, ?, ?)",
        (key_id, "monthly", max_queries, max_tokens),
    )
    store.conn.commit()


# --- Store-level tests --------------------------------------------------


def test_empty_store_returns_no_rows(store: Store) -> None:
    assert store.billing_report("2026-05") == []


def test_key_with_no_usage_shows_zeros(store: Store) -> None:
    _, key_id = store.create_api_key("partner-A")
    rows = store.billing_report("2026-05")
    assert len(rows) == 1
    r = rows[0]
    assert r["key_id"] == key_id
    assert r["key_name"] == "partner-A"
    assert r["queries"] == 0
    assert r["tokens"] == 0
    assert r["quota_queries"] == 0
    assert r["quota_tokens"] == 0
    assert r["over_quota"] is False


def test_usage_only_no_quota_is_under_quota(store: Store) -> None:
    """No quota set -> over_quota is False regardless of usage."""
    _, key_id = store.create_api_key("partner-A")
    _seed_usage(store, key_id, "2026-05", queries=10_000, tokens=1_000_000)
    rows = store.billing_report("2026-05")
    assert len(rows) == 1
    assert rows[0]["queries"] == 10_000
    assert rows[0]["tokens"] == 1_000_000
    assert rows[0]["over_quota"] is False, (
        "no quota set -> over_quota must default to False, not crash on "
        "zero-division or wrap to True"
    )


def test_over_quota_on_queries_dimension(store: Store) -> None:
    _, key_id = store.create_api_key("partner-A")
    _seed_quota(store, key_id, max_queries=100, max_tokens=200_000)
    _seed_usage(store, key_id, "2026-05", queries=150, tokens=50_000)
    rows = store.billing_report("2026-05")
    assert rows[0]["over_quota"] is True, "over on queries should set over_quota"


def test_over_quota_on_tokens_dimension(store: Store) -> None:
    _, key_id = store.create_api_key("partner-A")
    _seed_quota(store, key_id, max_queries=1_000, max_tokens=100_000)
    _seed_usage(store, key_id, "2026-05", queries=50, tokens=200_000)
    rows = store.billing_report("2026-05")
    assert rows[0]["over_quota"] is True, "over on tokens should set over_quota"


def test_under_both_dimensions_is_not_over(store: Store) -> None:
    _, key_id = store.create_api_key("partner-A")
    _seed_quota(store, key_id, max_queries=1_000, max_tokens=200_000)
    _seed_usage(store, key_id, "2026-05", queries=5, tokens=200)
    rows = store.billing_report("2026-05")
    assert rows[0]["over_quota"] is False


def test_period_filters_usage(store: Store) -> None:
    """Usage for 2026-04 must not bleed into the 2026-05 report."""
    _, key_id = store.create_api_key("partner-A")
    _seed_usage(store, key_id, "2026-04", queries=999, tokens=999_999)
    _seed_usage(store, key_id, "2026-05", queries=5, tokens=200)
    rows = store.billing_report("2026-05")
    assert rows[0]["queries"] == 5
    assert rows[0]["tokens"] == 200


def test_revoked_keys_still_appear_in_report(store: Store) -> None:
    """We bill keys that were active during the period even if
    revoked since. The report includes them so revenue attribution
    survives mid-period revocations."""
    _, key_id = store.create_api_key("partner-A")
    _seed_usage(store, key_id, "2026-05", queries=10, tokens=2_000)
    store.revoke_api_key(key_id)
    rows = store.billing_report("2026-05")
    assert len(rows) == 1, "revoked key with usage must still appear in billing"
    assert rows[0]["queries"] == 10


def test_rows_are_ordered_by_key_name(store: Store) -> None:
    """Stable ordering means CSV diffs across periods are meaningful."""
    store.create_api_key("zeta-key")
    store.create_api_key("alpha-key")
    store.create_api_key("mid-key")
    rows = store.billing_report("2026-05")
    names = [r["key_name"] for r in rows]
    assert names == sorted(names), f"rows must be alphabetically sorted; got {names}"


# --- CLI-level tests ----------------------------------------------------


def test_cli_emits_csv_header_even_on_empty_store(runner: CliRunner) -> None:
    result = runner.invoke(app, ["billing", "report", "--period", "2026-05"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[0] == "key_name,queries,tokens,quota_queries,quota_tokens,over_quota", (
        f"CSV header drifted (downstream billing systems bind to it). Got: {lines[0]!r}"
    )


def test_cli_csv_row_shape_after_seed(runner: CliRunner) -> None:
    """Drive create -> seed -> report end-to-end via the CLI."""
    runner.invoke(app, ["key", "create", "partner-A"])
    store = _live_store()
    try:
        # Pull the freshly-created key's id, then seed usage + quota
        key_id = store.conn.execute("SELECT id FROM api_key").fetchone()["id"]
        _seed_quota(store, key_id, max_queries=100, max_tokens=200_000)
        _seed_usage(store, key_id, "2026-05", queries=150, tokens=50_000)
    finally:
        store.close()

    result = runner.invoke(app, ["billing", "report", "--period", "2026-05"])
    assert result.exit_code == 0, result.output

    reader = csv.DictReader(io.StringIO(result.output))
    rows = list(reader)
    assert len(rows) == 1
    r = rows[0]
    assert r["key_name"] == "partner-A"
    assert r["queries"] == "150"
    assert r["tokens"] == "50000"
    assert r["quota_queries"] == "100"
    assert r["quota_tokens"] == "200000"
    assert r["over_quota"] == "true"


def test_cli_requires_period(runner: CliRunner) -> None:
    """--period is required; the CLI fails loudly if omitted instead
    of silently defaulting to 'this month' (which would surprise
    automation that runs the report on a schedule)."""
    result = runner.invoke(app, ["billing", "report"])
    assert result.exit_code != 0, "missing --period must error, not default"
