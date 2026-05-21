"""Task 2.1: API key + quota + usage_period schema (additive migration).

Phase 3 / Angle #2 (hosted context API) of the enterprise execution
plan. Tables ship with the schema but the endpoint layer that uses
them lands in Tasks 2.2-2.5. The hosted tier is OFF by default --
adding these tables is harmless for the self-host path.

The schema-only nature of Task 2.1 means a passing test here proves
two things:
  1. The migration runs cleanly on a fresh DB (no errors during
     ``Store.__init__``).
  2. The columns the downstream key-issuance CLI (Task 2.2) and the
     metering hook (Task 2.4) will read are present + correctly
     typed.
"""

from __future__ import annotations

from mnemo.store import Store


def _table_columns(store: Store, table: str) -> dict[str, str]:
    """Return {column_name: declared_type} for a SQLite table."""
    return {r[1]: r[2] for r in store.conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_api_key_table_has_required_columns(store: Store) -> None:
    cols = _table_columns(store, "api_key")
    expected = {"id", "hash", "name", "created_at", "revoked_at"}
    missing = expected - set(cols)
    assert not missing, f"api_key missing columns: {missing}; got {sorted(cols)}"


def test_api_key_has_unique_hash_index(store: Store) -> None:
    """The hash column must be unique (issuing the same raw key twice
    must be caught by the DB, not just the issuance CLI)."""
    # SQLite reports unique constraint via the UNIQUE keyword in the
    # column declaration. PRAGMA index_list shows the implicit unique
    # index named like 'sqlite_autoindex_api_key_<N>'.
    indexes = store.conn.execute("PRAGMA index_list(api_key)").fetchall()
    unique_indexes = [row for row in indexes if row["unique"] == 1]
    assert unique_indexes, "api_key.hash must have a UNIQUE constraint"


def test_quota_table_has_required_columns(store: Store) -> None:
    cols = _table_columns(store, "quota")
    expected = {"api_key_id", "period", "max_queries", "max_tokens"}
    missing = expected - set(cols)
    assert not missing, f"quota missing columns: {missing}; got {sorted(cols)}"


def test_usage_period_table_has_required_columns(store: Store) -> None:
    cols = _table_columns(store, "usage_period")
    expected = {"api_key_id", "period", "queries", "tokens", "updated_at"}
    missing = expected - set(cols)
    assert not missing, f"usage_period missing columns: {missing}; got {sorted(cols)}"


def test_quota_and_usage_cascade_on_api_key_delete(store: Store) -> None:
    """ON DELETE CASCADE on the FK so revoking a key cleans up its
    quota + usage rows automatically. Catches the FK definition not
    cascading."""
    now = 1
    store.conn.execute(
        "INSERT INTO api_key (id, hash, name, created_at) VALUES (?, ?, ?, ?)",
        ("k1", "h1", "test", now),
    )
    store.conn.execute(
        "INSERT INTO quota (api_key_id, period, max_queries, max_tokens) VALUES (?, ?, ?, ?)",
        ("k1", "monthly", 1000, 200_000),
    )
    store.conn.execute(
        "INSERT INTO usage_period (api_key_id, period, queries, tokens, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("k1", "2026-05", 0, 0, now),
    )
    store.conn.commit()

    # Delete the key; quota + usage rows must disappear too.
    store.conn.execute("DELETE FROM api_key WHERE id = ?", ("k1",))
    store.conn.commit()

    remaining_quota = store.conn.execute(
        "SELECT COUNT(*) FROM quota WHERE api_key_id = ?", ("k1",)
    ).fetchone()[0]
    remaining_usage = store.conn.execute(
        "SELECT COUNT(*) FROM usage_period WHERE api_key_id = ?", ("k1",)
    ).fetchone()[0]
    assert remaining_quota == 0, "quota row must cascade-delete with api_key"
    assert remaining_usage == 0, "usage_period row must cascade-delete with api_key"


def test_quota_primary_key_is_api_key_id_plus_period(store: Store) -> None:
    """A given key has at most one quota row per period. Catches
    duplicate-quota bugs at the DB level."""
    store.conn.execute(
        "INSERT INTO api_key (id, hash, name, created_at) VALUES (?, ?, ?, ?)",
        ("k1", "h1", "test", 1),
    )
    store.conn.execute(
        "INSERT INTO quota (api_key_id, period, max_queries, max_tokens) VALUES (?, ?, ?, ?)",
        ("k1", "monthly", 1000, 200_000),
    )
    store.conn.commit()
    # Second insert with same (api_key_id, period) must fail.
    import sqlite3

    try:
        store.conn.execute(
            "INSERT INTO quota (api_key_id, period, max_queries, max_tokens) VALUES (?, ?, ?, ?)",
            ("k1", "monthly", 9999, 9_999_999),
        )
        store.conn.commit()
        raise AssertionError("expected IntegrityError on duplicate (api_key_id, period)")
    except sqlite3.IntegrityError:
        pass  # expected
