"""Tests for the v2.6 workspaces module + Store CRUD.

v2.6 phase 1: schema (workspaces + workspace_state + source_overrides)
+ workspaces.py public CRUD helpers.

The Store owns the SQL; ``mnemo.workspaces`` wraps the Store methods
with dataclasses + validation so higher layers (server, ingest) hold
typed values, not sqlite Rows.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mnemo import workspaces
from mnemo.store import Store
from mnemo.workspaces import (
    SourceOverride,
    Workspace,
    WorkspaceNotFound,
)

# --- Schema migration ---------------------------------------------------------


def _table_columns(store: Store, table: str) -> set[str]:
    rows = store.conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def test_schema_creates_workspaces_table(store: Store) -> None:
    cols = _table_columns(store, "workspaces")
    assert {
        "id",
        "name",
        "project_keys",
        "filter_prefs",
        "page_state",
        "created_at",
        "updated_at",
        "last_activated_at",
    } <= cols


def test_schema_creates_workspace_state_table(store: Store) -> None:
    cols = _table_columns(store, "workspace_state")
    assert {"singleton", "active_id", "activated_at"} <= cols


def test_schema_creates_source_overrides_table(store: Store) -> None:
    cols = _table_columns(store, "source_overrides")
    assert {"source_path", "decision", "reason", "decided_at"} <= cols


def test_schema_is_idempotent_for_v26(tmp_path: Path) -> None:
    """Re-opening an existing v2.5 DB must not raise (additive migration)."""
    db = tmp_path / "mnemo.db"
    Store(db).close()
    Store(db).close()


# --- Workspace CRUD ----------------------------------------------------------


def test_create_workspace_persists_fields(store: Store) -> None:
    ws = workspaces.create_workspace(
        store,
        name="ai-edge work",
        project_keys=["D--Repository-edge-device", "D--Repository-knowledge-base"],
    )
    assert isinstance(ws, Workspace)
    assert ws.name == "ai-edge work"
    assert ws.project_keys == [
        "D--Repository-edge-device",
        "D--Repository-knowledge-base",
    ]
    assert ws.filter_prefs is None
    assert ws.page_state is None
    assert ws.last_activated_at is None
    assert ws.created_at > 0
    assert ws.updated_at == ws.created_at


def test_create_workspace_accepts_filter_prefs_and_page_state(store: Store) -> None:
    ws = workspaces.create_workspace(
        store,
        name="filtered",
        project_keys=["P1"],
        filter_prefs={"layout": "nebula", "minConfidence": 0.6},
        page_state={"last_page": "/code"},
    )
    fetched = workspaces.get_workspace(store, ws.id)
    assert fetched is not None
    assert fetched.filter_prefs == {"layout": "nebula", "minConfidence": 0.6}
    assert fetched.page_state == {"last_page": "/code"}


def test_create_workspace_rejects_empty_name(store: Store) -> None:
    with pytest.raises(ValueError, match="name"):
        workspaces.create_workspace(store, name="", project_keys=["P1"])


def test_create_workspace_rejects_duplicate_name(store: Store) -> None:
    workspaces.create_workspace(store, name="dup", project_keys=["P1"])
    with pytest.raises(ValueError, match="already exists"):
        workspaces.create_workspace(store, name="dup", project_keys=["P2"])


def test_create_workspace_accepts_empty_project_keys(store: Store) -> None:
    """A workspace with no projects is valid: it surfaces BASE only."""
    ws = workspaces.create_workspace(store, name="base-only-ws", project_keys=[])
    assert ws.project_keys == []


def test_get_workspace_returns_none_when_missing(store: Store) -> None:
    assert workspaces.get_workspace(store, "nonexistent") is None


def test_get_workspace_by_name(store: Store) -> None:
    ws = workspaces.create_workspace(store, name="byname", project_keys=["P1"])
    fetched = workspaces.get_workspace_by_name(store, "byname")
    assert fetched is not None
    assert fetched.id == ws.id


def test_list_workspaces_orders_active_first_then_recent(store: Store) -> None:
    a = workspaces.create_workspace(store, name="A", project_keys=["P1"])
    b = workspaces.create_workspace(store, name="B", project_keys=["P2"])
    c = workspaces.create_workspace(store, name="C", project_keys=["P3"])
    # Activation order: a -> b -> c (so last_activated_at: c > b > a)
    workspaces.set_active_workspace(store, a.id)
    time.sleep(0.01)
    workspaces.set_active_workspace(store, b.id)
    time.sleep(0.01)
    workspaces.set_active_workspace(store, c.id)
    listed = workspaces.list_workspaces(store)
    # Active workspace (c) first, then others by last_activated_at DESC
    assert [w.name for w in listed] == ["C", "B", "A"]


def test_list_workspaces_unactivated_sorted_by_created_desc(store: Store) -> None:
    a = workspaces.create_workspace(store, name="a", project_keys=["P1"])
    time.sleep(0.01)
    b = workspaces.create_workspace(store, name="b", project_keys=["P2"])
    listed = workspaces.list_workspaces(store)
    names = [w.name for w in listed]
    # Newer 'b' before older 'a' for un-activated workspaces
    assert names == ["b", "a"]
    assert {a.name, b.name} == set(names)


def test_update_workspace_patches_fields(store: Store) -> None:
    ws = workspaces.create_workspace(store, name="orig", project_keys=["P1"])
    time.sleep(0.01)
    updated = workspaces.update_workspace(
        store,
        ws.id,
        name="renamed",
        project_keys=["P1", "P2"],
        filter_prefs={"minConfidence": 0.5},
    )
    assert updated is not None
    assert updated.name == "renamed"
    assert updated.project_keys == ["P1", "P2"]
    assert updated.filter_prefs == {"minConfidence": 0.5}
    assert updated.updated_at >= updated.created_at


def test_update_workspace_returns_none_when_missing(store: Store) -> None:
    assert workspaces.update_workspace(store, "missing", name="x") is None


def test_update_workspace_rejects_duplicate_name(store: Store) -> None:
    a = workspaces.create_workspace(store, name="a", project_keys=["P1"])
    workspaces.create_workspace(store, name="b", project_keys=["P2"])
    with pytest.raises(ValueError, match="already exists"):
        workspaces.update_workspace(store, a.id, name="b")


def test_update_workspace_same_name_is_allowed(store: Store) -> None:
    """Setting name to the workspace's existing value is a no-op, not a conflict."""
    ws = workspaces.create_workspace(store, name="keep", project_keys=["P1"])
    updated = workspaces.update_workspace(store, ws.id, name="keep")
    assert updated is not None
    assert updated.name == "keep"


def test_delete_workspace_removes_row(store: Store) -> None:
    ws = workspaces.create_workspace(store, name="to-delete", project_keys=["P1"])
    assert workspaces.delete_workspace(store, ws.id) is True
    assert workspaces.get_workspace(store, ws.id) is None


def test_delete_workspace_returns_false_when_missing(store: Store) -> None:
    assert workspaces.delete_workspace(store, "missing") is False


# --- Active-workspace pointer ------------------------------------------------


def test_get_active_workspace_returns_none_initially(store: Store) -> None:
    assert workspaces.get_active_workspace(store) is None


def test_set_active_workspace_updates_pointer(store: Store) -> None:
    ws = workspaces.create_workspace(store, name="act", project_keys=["P1"])
    workspaces.set_active_workspace(store, ws.id)
    active = workspaces.get_active_workspace(store)
    assert active is not None
    assert active.id == ws.id
    assert active.last_activated_at is not None
    assert active.last_activated_at > 0


def test_set_active_workspace_raises_when_missing(store: Store) -> None:
    with pytest.raises(WorkspaceNotFound):
        workspaces.set_active_workspace(store, "nonexistent")


def test_set_active_workspace_replaces_previous(store: Store) -> None:
    a = workspaces.create_workspace(store, name="a", project_keys=["P1"])
    b = workspaces.create_workspace(store, name="b", project_keys=["P2"])
    workspaces.set_active_workspace(store, a.id)
    workspaces.set_active_workspace(store, b.id)
    active = workspaces.get_active_workspace(store)
    assert active is not None
    assert active.id == b.id


def test_clear_active_workspace(store: Store) -> None:
    ws = workspaces.create_workspace(store, name="x", project_keys=["P1"])
    workspaces.set_active_workspace(store, ws.id)
    workspaces.clear_active_workspace(store)
    assert workspaces.get_active_workspace(store) is None


def test_delete_active_workspace_clears_pointer(store: Store) -> None:
    """Deleting the currently-active workspace must clear the pointer (FK ON DELETE SET NULL)."""
    ws = workspaces.create_workspace(store, name="act-del", project_keys=["P1"])
    workspaces.set_active_workspace(store, ws.id)
    workspaces.delete_workspace(store, ws.id)
    assert workspaces.get_active_workspace(store) is None


# --- Source override CRUD ----------------------------------------------------


def test_upsert_source_override_persists(store: Store) -> None:
    ov = workspaces.upsert_source_override(
        store,
        source_path="/repo/path/sensitive.env",
        decision="always_skip",
        reason="suspicious:suspected_secret",
    )
    assert isinstance(ov, SourceOverride)
    assert ov.source_path == "/repo/path/sensitive.env"
    assert ov.decision == "always_skip"
    assert ov.reason == "suspicious:suspected_secret"
    assert ov.decided_at > 0


def test_upsert_source_override_replaces_existing(store: Store) -> None:
    workspaces.upsert_source_override(store, source_path="/x", decision="always_skip", reason="r1")
    workspaces.upsert_source_override(store, source_path="/x", decision="always_keep", reason="r2")
    got = workspaces.get_source_override(store, "/x")
    assert got is not None
    assert got.decision == "always_keep"
    assert got.reason == "r2"


def test_upsert_source_override_rejects_unknown_decision(store: Store) -> None:
    with pytest.raises(ValueError, match="decision"):
        workspaces.upsert_source_override(store, source_path="/x", decision="banana", reason=None)


def test_list_source_overrides_orders_by_decided_at_desc(store: Store) -> None:
    workspaces.upsert_source_override(store, source_path="/a", decision="always_skip", reason=None)
    time.sleep(0.01)
    workspaces.upsert_source_override(store, source_path="/b", decision="always_keep", reason=None)
    listed = workspaces.list_source_overrides(store)
    assert [ov.source_path for ov in listed] == ["/b", "/a"]


def test_get_source_override_returns_none_when_missing(store: Store) -> None:
    assert workspaces.get_source_override(store, "/missing") is None


def test_delete_source_override_returns_true_when_existed(store: Store) -> None:
    workspaces.upsert_source_override(store, source_path="/x", decision="always_skip", reason=None)
    assert workspaces.delete_source_override(store, "/x") is True
    assert workspaces.get_source_override(store, "/x") is None


def test_delete_source_override_returns_false_when_missing(store: Store) -> None:
    assert workspaces.delete_source_override(store, "/missing") is False


def test_batch_upsert_source_overrides(store: Store) -> None:
    """The /v1/source_overrides batch endpoint sends a list; helper writes them in one transaction."""
    items = [
        {"source_path": "/a", "decision": "always_skip", "reason": "r1"},
        {"source_path": "/b", "decision": "always_keep", "reason": "r2"},
    ]
    written = workspaces.batch_upsert_source_overrides(store, items)
    assert len(written) == 2
    assert {ov.source_path for ov in written} == {"/a", "/b"}
