"""Phase 3 follow-up: ``mnemo key set-quota`` CLI + Store.set_quota.

Closes the only Phase 3 feature gap. Phase 3a's
``docs/hosted/deploying.md`` previously told operators to set
quotas via direct SQLite -- this wrapper replaces that step.

Pairs with the existing key-management surface (create / list /
revoke from Task 2.2) and feeds the existing Phase 3b quota
enforcement on /v1/query (Task 2.5). No new schema; no /v1
endpoint change; pure operator-side ergonomics.
"""

from __future__ import annotations

import sqlite3
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


# --- Store-level: set_quota --------------------------------------------


def test_set_quota_first_call_creates_row(store: Store) -> None:
    _, key_id = store.create_api_key("partner-A")
    store.set_quota(key_id, max_queries=1000, max_tokens=200_000)
    row = store.conn.execute(
        "SELECT max_queries, max_tokens, period FROM quota WHERE api_key_id = ?",
        (key_id,),
    ).fetchone()
    assert row is not None
    assert row["max_queries"] == 1000
    assert row["max_tokens"] == 200_000
    assert row["period"] == "monthly"  # default


def test_set_quota_is_idempotent_and_updates_in_place(store: Store) -> None:
    """Calling set_quota twice with new limits updates the existing
    row via ON CONFLICT DO UPDATE -- no composite-PK collision."""
    _, key_id = store.create_api_key("partner-A")
    store.set_quota(key_id, max_queries=100, max_tokens=10_000)
    store.set_quota(key_id, max_queries=500, max_tokens=50_000)
    rows = store.conn.execute(
        "SELECT max_queries, max_tokens FROM quota WHERE api_key_id = ?",
        (key_id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["max_queries"] == 500
    assert rows[0]["max_tokens"] == 50_000


def test_set_quota_rejects_unknown_key(store: Store) -> None:
    """FK on api_key(id) blocks setting a quota on a non-existent key."""
    with pytest.raises(sqlite3.IntegrityError):
        store.set_quota("does-not-exist", max_queries=100, max_tokens=10_000)


def test_set_quota_rejects_negative_limits(store: Store) -> None:
    _, key_id = store.create_api_key("partner-A")
    for bad in (-1, -100):
        with pytest.raises(ValueError, match="must be >="):
            store.set_quota(key_id, max_queries=bad, max_tokens=10_000)
        with pytest.raises(ValueError, match="must be >="):
            store.set_quota(key_id, max_queries=10_000, max_tokens=bad)


def test_set_quota_zero_is_allowed(store: Store) -> None:
    """``max_queries=0`` is a valid posture (key exists but every
    request is rejected). Useful for staging keys before activation."""
    _, key_id = store.create_api_key("partner-A")
    store.set_quota(key_id, max_queries=0, max_tokens=0)
    allowed, reason = store.check_quota(key_id, "2026-05")
    # With 0 quota + 0 usage, strict >= triggers: 0 >= 0 -> blocked.
    assert allowed is False
    assert reason is not None


# --- CLI-level tests ---------------------------------------------------


def test_cli_set_quota_persists_via_billing_report(runner: CliRunner) -> None:
    """End-to-end: create -> set-quota -> billing report shows the
    quota fields. The integration path billing systems actually use."""
    create = runner.invoke(app, ["key", "create", "partner-A"])
    assert create.exit_code == 0
    key_id = None
    for line in create.output.splitlines():
        stripped = line.strip()
        if stripped.startswith("id:"):
            key_id = stripped.split(":", 1)[1].strip()
            break
    assert key_id

    set_result = runner.invoke(
        app,
        [
            "key",
            "set-quota",
            key_id,
            "--max-queries",
            "10000",
            "--max-tokens",
            "2000000",
        ],
    )
    assert set_result.exit_code == 0, set_result.output
    assert "Quota set" in set_result.output
    assert "10000" in set_result.output

    # Billing report (any period) should now show the quota fields.
    bill = runner.invoke(app, ["billing", "report", "--period", "2026-05"])
    assert bill.exit_code == 0
    assert "10000" in bill.output
    assert "2000000" in bill.output


def test_cli_set_quota_overwrites_existing(runner: CliRunner) -> None:
    """Operator can update the quota after issuance without
    intermediate steps."""
    create = runner.invoke(app, ["key", "create", "partner-A"])
    key_id = None
    for line in create.output.splitlines():
        stripped = line.strip()
        if stripped.startswith("id:"):
            key_id = stripped.split(":", 1)[1].strip()
            break
    assert key_id

    runner.invoke(app, ["key", "set-quota", key_id, "--max-queries", "100", "--max-tokens", "1000"])
    runner.invoke(app, ["key", "set-quota", key_id, "--max-queries", "999", "--max-tokens", "9999"])

    bill = runner.invoke(app, ["billing", "report", "--period", "2026-05"])
    assert "999" in bill.output
    assert "9999" in bill.output
    assert "100,1000" not in bill.output  # old values gone


def test_cli_set_quota_unknown_key_exits_nonzero(runner: CliRunner) -> None:
    result = runner.invoke(
        app,
        [
            "key",
            "set-quota",
            "does-not-exist",
            "--max-queries",
            "10",
            "--max-tokens",
            "100",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "no key" in result.output.lower() or "does-not-exist" in result.output


def test_cli_set_quota_negative_limits_error(runner: CliRunner) -> None:
    create = runner.invoke(app, ["key", "create", "partner-A"])
    key_id = None
    for line in create.output.splitlines():
        stripped = line.strip()
        if stripped.startswith("id:"):
            key_id = stripped.split(":", 1)[1].strip()
            break
    assert key_id

    result = runner.invoke(
        app,
        [
            "key",
            "set-quota",
            key_id,
            "--max-queries",
            "-5",
            "--max-tokens",
            "100",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "invalid" in result.output.lower() or ">=" in result.output


def test_cli_set_quota_requires_max_queries_and_max_tokens(runner: CliRunner) -> None:
    """Both --max-queries and --max-tokens are required. Missing
    either errors loudly (no silent default-to-0 surprise)."""
    create = runner.invoke(app, ["key", "create", "partner-A"])
    key_id = None
    for line in create.output.splitlines():
        stripped = line.strip()
        if stripped.startswith("id:"):
            key_id = stripped.split(":", 1)[1].strip()
            break
    assert key_id

    only_queries = runner.invoke(app, ["key", "set-quota", key_id, "--max-queries", "100"])
    assert only_queries.exit_code != 0

    only_tokens = runner.invoke(app, ["key", "set-quota", key_id, "--max-tokens", "1000"])
    assert only_tokens.exit_code != 0
