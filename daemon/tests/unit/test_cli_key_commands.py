"""Task 2.2: ``mnemo key {create, list, revoke}`` CLI + Store helpers.

The store methods are the unit of correctness; the CLI is a thin
wrapper. Tests split accordingly:

- Store-level: round-trip (create -> verify -> revoke -> verify
  returns None); raw-key is never persisted; list excludes / includes
  revoked correctly; per-key salt is unique across keys.
- CLI-level: ``mnemo key create`` prints the raw key + persists the
  hash + reports the key id; ``list`` shows active vs revoked status;
  ``revoke`` is idempotent at the user level (1 on second call).
"""

from __future__ import annotations

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
    """Every CLI test gets its own MNEMO_HOME so the persisted store
    doesn't leak between tests."""
    return isolated_mnemo_home


def _live_store() -> Store:
    """Open the store the CLI will write to (sandboxed via the autouse
    isolated_mnemo_home fixture)."""
    paths.ensure_runtime_dirs()
    return Store(paths.db_path())


# --- Store-level tests --------------------------------------------------


def test_create_returns_raw_key_and_id_and_persists_only_hash(store: Store) -> None:
    raw_key, key_id = store.create_api_key("partner-A")
    assert raw_key  # non-empty
    assert key_id
    assert len(raw_key) >= 32, "raw key must carry meaningful entropy"

    # The raw key must NOT appear anywhere in the api_key row.
    row = store.conn.execute("SELECT * FROM api_key WHERE id = ?", (key_id,)).fetchone()
    assert row is not None
    assert row["hash"] != raw_key, "raw key MUST NOT be stored verbatim"
    assert raw_key not in row["hash"]
    assert row["salt"], "per-key salt must be persisted alongside the hash"
    assert row["salt"] != raw_key


def test_verify_round_trip(store: Store) -> None:
    raw_key, key_id = store.create_api_key("partner-A")
    assert store.verify_api_key(raw_key) == key_id
    # Anything else fails.
    assert store.verify_api_key(raw_key + "X") is None
    assert store.verify_api_key("") is None


def test_two_keys_get_different_salts(store: Store) -> None:
    """Per-key salt: no rainbow table works across keys."""
    _, id_a = store.create_api_key("partner-A")
    _, id_b = store.create_api_key("partner-B")
    salts = {
        r["id"]: r["salt"]
        for r in store.conn.execute(
            "SELECT id, salt FROM api_key WHERE id IN (?, ?)", (id_a, id_b)
        ).fetchall()
    }
    assert salts[id_a] != salts[id_b]


def test_revoke_then_verify_returns_none(store: Store) -> None:
    raw_key, key_id = store.create_api_key("partner-A")
    assert store.revoke_api_key(key_id) is True
    assert store.verify_api_key(raw_key) is None


def test_revoke_is_idempotent_at_store_level(store: Store) -> None:
    """Second revoke returns False (already revoked); doesn't crash."""
    _, key_id = store.create_api_key("partner-A")
    assert store.revoke_api_key(key_id) is True
    assert store.revoke_api_key(key_id) is False
    assert store.revoke_api_key("does-not-exist") is False


def test_list_excludes_revoked_by_default(store: Store) -> None:
    _, active_id = store.create_api_key("active")
    _, revoked_id = store.create_api_key("revoked")
    store.revoke_api_key(revoked_id)

    default = {k["id"] for k in store.list_api_keys()}
    assert default == {active_id}, f"default list should only show active; got {default}"

    full = {k["id"] for k in store.list_api_keys(include_revoked=True)}
    assert full == {active_id, revoked_id}


# --- CLI-level tests ----------------------------------------------------


def test_cli_create_prints_raw_key_and_id(runner: CliRunner) -> None:
    result = runner.invoke(app, ["key", "create", "partner-A"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "RAW KEY" in out
    assert "id:" in out
    assert "partner-A" in out

    # Pull the raw key out of the output (it's the >30-char line that
    # isn't a label) and confirm it isn't in any persisted hash.
    raw_key = None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) > 30 and "RAW" not in stripped and "id:" not in stripped:
            raw_key = stripped
            break
    assert raw_key, f"could not parse raw key from CLI output:\n{out}"

    store = _live_store()
    try:
        row = store.conn.execute("SELECT hash FROM api_key").fetchone()
        assert row is not None
        assert row["hash"] != raw_key, "raw key must not be stored verbatim"
        assert raw_key not in row["hash"]
    finally:
        store.close()


def test_cli_list_shows_status(runner: CliRunner) -> None:
    runner.invoke(app, ["key", "create", "partner-A"])
    runner.invoke(app, ["key", "create", "partner-B"])
    # List should show both as active.
    result = runner.invoke(app, ["key", "list"])
    assert result.exit_code == 0, result.output
    assert "partner-A" in result.output
    assert "partner-B" in result.output
    assert "active" in result.output
    assert "REVOKED" not in result.output


def test_cli_revoke_excludes_from_default_list(runner: CliRunner) -> None:
    create = runner.invoke(app, ["key", "create", "partner-A"])
    # Pull key_id out of the create output (line "id:   <hex>")
    key_id = None
    for line in create.output.splitlines():
        stripped = line.strip()
        if stripped.startswith("id:"):
            key_id = stripped.split(":", 1)[1].strip()
            break
    assert key_id

    rev = runner.invoke(app, ["key", "revoke", key_id])
    assert rev.exit_code == 0, rev.output
    assert "Revoked" in rev.output

    # Default list now empty
    default = runner.invoke(app, ["key", "list"])
    assert default.exit_code == 0, default.output
    assert "partner-A" not in default.output

    # --include-revoked shows it
    full = runner.invoke(app, ["key", "list", "--include-revoked"])
    assert full.exit_code == 0, full.output
    assert "partner-A" in full.output
    assert "REVOKED" in full.output


def test_cli_revoke_unknown_key_exits_nonzero(runner: CliRunner) -> None:
    result = runner.invoke(app, ["key", "revoke", "does-not-exist"])
    assert result.exit_code == 1, result.output
    # The message is helpful: tells the user it was either not found
    # or already revoked.
    assert "not found" in result.output.lower() or "revoked" in result.output.lower()
