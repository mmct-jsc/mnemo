"""v2.6 workspaces + source overrides.

A *workspace* is a user-named bundle of ``project_key`` strings plus
optional filter prefs and page state. The active workspace scopes
every page's view (Nebula / /code / Search / v3 chat) to the bundle's
projects; with no active workspace, the UI shows BASE-flagged nodes
only.

A *source override* is a per-path decision the user makes in the
reindex report's malformed + suspicious sections (``always_skip`` /
``always_keep`` / ``retry``). The decision persists across reindex
runs so the user does not re-decide on every walk.

The :class:`mnemo.store.Store` owns the SQL; this module wraps the
store with dataclasses + light validation so higher layers (HTTP,
CLI, ingest) hold typed values instead of raw ``sqlite3.Row`` objects.
Activation broadcasting + cap enforcement live in
:mod:`mnemo.server` (v2.6 phase 5).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any

from mnemo.store import Store

# --- Constants ---------------------------------------------------------------

# v2.6 phase 1: decisions the reindex report UI can write back.
#   'always_skip'  -> never index this path again
#   'always_keep'  -> override the malformed / suspicious classification
#   'retry'        -> re-classify on the next walk (e.g. file edited since)
ALLOWED_DECISIONS = frozenset({"always_skip", "always_keep", "retry"})


# Sentinel for ``update_workspace``: ``...`` = "leave field alone"; passing
# ``None`` for nullable fields explicitly clears them. Mirrors the
# ``Store.update_source`` pattern.
_LEAVE_ALONE: Any = ...


# --- Exceptions --------------------------------------------------------------


class WorkspaceNotFoundError(LookupError):
    """Raised by :func:`set_active_workspace` when the id doesn't exist."""


# Back-compat alias for older imports written before the N818 rename.
# Kept indefinitely -- the symbol is part of the module's public API.
WorkspaceNotFound = WorkspaceNotFoundError


# --- Dataclasses -------------------------------------------------------------


@dataclass
class Workspace:
    """One row from the ``workspaces`` table.

    Time columns (``created_at`` / ``updated_at`` / ``last_activated_at``)
    are stored as **epoch milliseconds** -- not seconds. See the schema
    comment in :mod:`mnemo.store` for the rationale.
    """

    id: str
    name: str
    project_keys: list[str]
    filter_prefs: dict[str, Any] | None
    page_state: dict[str, Any] | None
    created_at: int
    updated_at: int
    last_activated_at: int | None


@dataclass
class SourceOverride:
    """One row from the ``source_overrides`` table."""

    source_path: str
    decision: str
    reason: str | None
    decided_at: int


# --- Helpers -----------------------------------------------------------------


def _now_ms() -> int:
    """Epoch milliseconds. Use only for workspaces / overrides tables."""
    return time.time_ns() // 1_000_000


def _row_to_workspace(row: sqlite3.Row) -> Workspace:
    return Workspace(
        id=row["id"],
        name=row["name"],
        project_keys=json.loads(row["project_keys"]) if row["project_keys"] else [],
        filter_prefs=json.loads(row["filter_prefs"]) if row["filter_prefs"] else None,
        page_state=json.loads(row["page_state"]) if row["page_state"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_activated_at=row["last_activated_at"],
    )


def _row_to_override(row: sqlite3.Row) -> SourceOverride:
    return SourceOverride(
        source_path=row["source_path"],
        decision=row["decision"],
        reason=row["reason"],
        decided_at=row["decided_at"],
    )


# --- Workspaces CRUD ---------------------------------------------------------


def create_workspace(
    store: Store,
    *,
    name: str,
    project_keys: list[str],
    filter_prefs: dict[str, Any] | None = None,
    page_state: dict[str, Any] | None = None,
) -> Workspace:
    """Create a new workspace. Name must be non-empty and unique.

    Pre-checks duplicate name so callers get a friendly ValueError instead
    of a raw ``sqlite3.IntegrityError``.
    """
    if not isinstance(name, str):
        raise ValueError("workspace name must be a non-empty string")
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("workspace name must be non-empty")
    if get_workspace_by_name(store, clean_name) is not None:
        raise ValueError(f"workspace name {clean_name!r} already exists")
    wid = uuid.uuid4().hex
    now = _now_ms()
    with store._lock:
        store.conn.execute(
            """
            INSERT INTO workspaces
              (id, name, project_keys, filter_prefs, page_state,
               created_at, updated_at, last_activated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                wid,
                clean_name,
                json.dumps(list(project_keys)),
                json.dumps(filter_prefs) if filter_prefs is not None else None,
                json.dumps(page_state) if page_state is not None else None,
                now,
                now,
            ),
        )
        store.conn.commit()
    fetched = get_workspace(store, wid)
    assert fetched is not None  # just inserted; defensive against race
    return fetched


def get_workspace(store: Store, workspace_id: str) -> Workspace | None:
    with store._lock:
        row = store.conn.execute(
            "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
    return _row_to_workspace(row) if row else None


def get_workspace_by_name(store: Store, name: str) -> Workspace | None:
    with store._lock:
        row = store.conn.execute("SELECT * FROM workspaces WHERE name = ?", (name,)).fetchone()
    return _row_to_workspace(row) if row else None


def list_workspaces(store: Store) -> list[Workspace]:
    """List workspaces ordered active-first, then by recency.

    Ordering: active workspace first (so the switcher dropdown shows the
    current state at the top), then by ``last_activated_at DESC`` (most
    recently used next), and finally by ``created_at DESC`` for
    un-activated workspaces (newer first).
    """
    active_id = _get_active_id(store) or ""
    with store._lock:
        rows = store.conn.execute(
            """
            SELECT * FROM workspaces
            ORDER BY
              (id = ?) DESC,
              (last_activated_at IS NULL) ASC,
              last_activated_at DESC,
              created_at DESC
            """,
            (active_id,),
        ).fetchall()
    return [_row_to_workspace(r) for r in rows]


def update_workspace(
    store: Store,
    workspace_id: str,
    *,
    name: str | Any = _LEAVE_ALONE,
    project_keys: list[str] | Any = _LEAVE_ALONE,
    filter_prefs: dict[str, Any] | None | Any = _LEAVE_ALONE,
    page_state: dict[str, Any] | None | Any = _LEAVE_ALONE,
) -> Workspace | None:
    """Patch a workspace. Sentinel ``...`` = "leave field alone".

    Returns the updated workspace, or None if the id doesn't exist.
    Raises ``ValueError`` if ``name`` is empty or collides with another
    workspace (collision with self is allowed -- it's a no-op rename).
    """
    existing = get_workspace(store, workspace_id)
    if existing is None:
        return None

    sets: list[str] = []
    params: list[object] = []

    if name is not _LEAVE_ALONE:
        clean = name.strip() if isinstance(name, str) else ""
        if not clean:
            raise ValueError("workspace name must be non-empty")
        other = get_workspace_by_name(store, clean)
        if other is not None and other.id != workspace_id:
            raise ValueError(f"workspace name {clean!r} already exists")
        sets.append("name = ?")
        params.append(clean)

    if project_keys is not _LEAVE_ALONE:
        sets.append("project_keys = ?")
        params.append(json.dumps(list(project_keys)))

    if filter_prefs is not _LEAVE_ALONE:
        sets.append("filter_prefs = ?")
        params.append(json.dumps(filter_prefs) if filter_prefs is not None else None)

    if page_state is not _LEAVE_ALONE:
        sets.append("page_state = ?")
        params.append(json.dumps(page_state) if page_state is not None else None)

    if not sets:
        return existing

    sets.append("updated_at = ?")
    params.append(_now_ms())
    params.append(workspace_id)

    with store._lock:
        store.conn.execute(
            f"UPDATE workspaces SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        store.conn.commit()
    return get_workspace(store, workspace_id)


def delete_workspace(store: Store, workspace_id: str) -> bool:
    """Delete a workspace. Returns True if it existed.

    FK ``workspace_state.active_id REFERENCES workspaces(id) ON DELETE SET NULL``
    clears the active pointer automatically when the deleted workspace
    was the active one.
    """
    with store._lock:
        cur = store.conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        store.conn.commit()
    return cur.rowcount > 0


# --- Active-workspace pointer ------------------------------------------------


def _get_active_id(store: Store) -> str | None:
    with store._lock:
        row = store.conn.execute(
            "SELECT active_id FROM workspace_state WHERE singleton = 0"
        ).fetchone()
    if row is None:
        return None
    aid = row["active_id"]
    return aid if aid else None


def get_active_workspace(store: Store) -> Workspace | None:
    """Return the active workspace, or None when no workspace is active.

    No-workspace mode is the BASE-only UI mode -- every page filters to
    BASE-flagged nodes.
    """
    active_id = _get_active_id(store)
    if not active_id:
        return None
    return get_workspace(store, active_id)


def set_active_workspace(store: Store, workspace_id: str) -> None:
    """Activate ``workspace_id``.

    Writes the singleton ``workspace_state`` row + bumps
    ``last_activated_at`` on the workspace so it sorts first next time.

    Raises :class:`WorkspaceNotFound` if no workspace with that id exists.
    Cap enforcement + SSE broadcast happen at the HTTP layer (v2.6 phase 5),
    not here.
    """
    if get_workspace(store, workspace_id) is None:
        raise WorkspaceNotFoundError(workspace_id)
    now = _now_ms()
    with store._lock:
        store.conn.execute(
            """
            INSERT OR REPLACE INTO workspace_state (singleton, active_id, activated_at)
            VALUES (0, ?, ?)
            """,
            (workspace_id, now),
        )
        store.conn.execute(
            "UPDATE workspaces SET last_activated_at = ? WHERE id = ?",
            (now, workspace_id),
        )
        store.conn.commit()


def clear_active_workspace(store: Store) -> None:
    """Clear the active workspace -- enter BASE-only UI mode."""
    with store._lock:
        store.conn.execute("DELETE FROM workspace_state WHERE singleton = 0")
        store.conn.commit()


# --- Source overrides --------------------------------------------------------


def upsert_source_override(
    store: Store,
    *,
    source_path: str,
    decision: str,
    reason: str | None,
) -> SourceOverride:
    """Insert or update one override row. Idempotent on ``source_path``."""
    if decision not in ALLOWED_DECISIONS:
        raise ValueError(f"decision {decision!r} not in {sorted(ALLOWED_DECISIONS)!r}")
    now = _now_ms()
    with store._lock:
        store.conn.execute(
            """
            INSERT INTO source_overrides (source_path, decision, reason, decided_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_path) DO UPDATE SET
              decision   = excluded.decision,
              reason     = excluded.reason,
              decided_at = excluded.decided_at
            """,
            (source_path, decision, reason, now),
        )
        store.conn.commit()
    fetched = get_source_override(store, source_path)
    assert fetched is not None
    return fetched


def get_source_override(store: Store, source_path: str) -> SourceOverride | None:
    with store._lock:
        row = store.conn.execute(
            "SELECT * FROM source_overrides WHERE source_path = ?", (source_path,)
        ).fetchone()
    return _row_to_override(row) if row else None


def list_source_overrides(store: Store) -> list[SourceOverride]:
    """List overrides newest-first."""
    with store._lock:
        rows = store.conn.execute(
            "SELECT * FROM source_overrides ORDER BY decided_at DESC, source_path ASC"
        ).fetchall()
    return [_row_to_override(r) for r in rows]


def delete_source_override(store: Store, source_path: str) -> bool:
    with store._lock:
        cur = store.conn.execute(
            "DELETE FROM source_overrides WHERE source_path = ?", (source_path,)
        )
        store.conn.commit()
    return cur.rowcount > 0


def batch_upsert_source_overrides(
    store: Store,
    items: list[dict[str, Any]],
) -> list[SourceOverride]:
    """Upsert a list of overrides in one logical batch.

    Used by ``POST /v1/source_overrides`` to apply the user's report
    decisions in one trip. Each item must have ``source_path`` and
    ``decision``; ``reason`` is optional.
    """
    written: list[SourceOverride] = []
    for item in items:
        written.append(
            upsert_source_override(
                store,
                source_path=item["source_path"],
                decision=item["decision"],
                reason=item.get("reason"),
            )
        )
    return written
