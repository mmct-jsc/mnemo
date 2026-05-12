"""SQLite-backed store for mnemo nodes, edges, sources, and queries.

The store is the only component that touches SQLite directly. Higher layers
(ingest, retrieve, server) use the typed dataclasses defined here.

Schema is defined in ``SCHEMA_SQL`` and is idempotent: the constructor calls
``CREATE TABLE IF NOT EXISTS`` for every table, so reopening an existing
database is safe.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import sqlite_vec

from mnemo.paths import path_under_source


def _deserialize_float32(blob: bytes) -> list[float]:
    """Inverse of :func:`sqlite_vec.serialize_float32`.

    sqlite-vec packs vectors as a little-endian ``float32`` array; the
    library only provides the serializer because reads typically go
    through SQL helpers like ``vec_to_json``. For v1.2 phase 2 we need
    to round-trip query embeddings in Python, so unpack here.
    """
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


SCHEMA_VERSION = 1
EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 dim. Bump + reindex to switch models.


# --- Allowed enum values (kept in code, not as SQL CHECK constraints, so we can
#     evolve them via migrations without rewriting tables) ----------------------

NODE_TYPES = frozenset(
    {
        "memory_user",
        "memory_feedback",
        "memory_project",
        "memory_reference",
        "project_doc",
        "plan_doc",
        "session_summary",
    }
)

SOURCE_KINDS = frozenset(
    {
        "memory_dir",
        "claude_md",
        "plan_dir",
        "transcripts",
    }
)

EDGE_RELATIONS = frozenset(
    {
        "applies_to",
        "derived_from",
        "contradicts",
        "supersedes",
        "mentions",
        "co_occurs_with",
    }
)

EDGE_SOURCES = frozenset({"inferred", "user", "frontmatter"})

# v1.2: feedback_event.reason enumeration. Each reason maps to a signal:
#   thumbs_up        -> +1.0 (explicit positive)
#   thumbs_down      -> -1.0 (explicit negative)
#   cite_copied      -> +0.5 (implicit positive; user copied the citation)
#   inferred_requery -> -0.5 (implicit negative; user re-asked a similar
#                              query within 5 min, suggesting earlier hits
#                              missed the mark)
# Stored as TEXT so future signals can be added without a schema migration.
FEEDBACK_REASONS = frozenset(
    {
        "thumbs_up",
        "thumbs_down",
        "cite_copied",
        "inferred_requery",
    }
)


# --- Dataclasses -------------------------------------------------------------


@dataclass
class Node:
    id: str
    type: str
    name: str
    description: str | None
    body: str
    source_path: str
    source_kind: str
    project_key: str | None
    frontmatter_json: str | None
    hash: str
    created_at: int
    updated_at: int
    # v1.1: BASE knowledge bypasses project isolation. A BASE-flagged
    # node surfaces in every project's queries regardless of the
    # active project. Frontmatter `base: true` sets it on parse; the
    # node detail UI exposes a toggle.
    base: bool = False

    @classmethod
    def new(
        cls,
        *,
        type: str,
        name: str,
        body: str,
        source_path: str,
        source_kind: str,
        description: str | None = None,
        project_key: str | None = None,
        frontmatter_json: str | None = None,
        hash: str = "",
        base: bool = False,
    ) -> Node:
        if type not in NODE_TYPES:
            raise ValueError(f"unknown node type: {type!r}")
        if source_kind not in SOURCE_KINDS:
            raise ValueError(f"unknown source kind: {source_kind!r}")
        now = int(time.time())
        return cls(
            id=uuid.uuid4().hex,
            type=type,
            name=name,
            description=description,
            body=body,
            source_path=source_path,
            source_kind=source_kind,
            project_key=project_key,
            frontmatter_json=frontmatter_json,
            hash=hash,
            created_at=now,
            updated_at=now,
            base=base,
        )


@dataclass
class Edge:
    src_id: str
    dst_id: str
    relation: str
    weight: float
    source: str
    created_at: int


@dataclass
class Source:
    path: str
    kind: str
    project_key: str | None
    last_indexed_at: int | None
    enabled: bool
    # v1.1: comma-separated glob patterns for include/exclude. None means
    # "use the kind's default include set" (see ingest.iter_files). Stored
    # as TEXT; UI input is comma-separated; ingest layer parses into a
    # pathspec.PathSpec.
    include: str | None = None
    exclude: str | None = None


@dataclass
class Query:
    id: str
    prompt: str
    intent_tags: list[str]
    retrieved_ids: list[str]
    scores: dict[str, float]
    ts: int
    # v1.2 phase 2: the query embedding, persisted so the inferred-
    # re-query detector can cosine-compare new prompts against recent
    # ones. None for pre-1.2 rows that never had an embedding written.
    embedding: list[float] | None = None
    # v1.2 phase 5: per-hit breakdown of the 6-term scoring formula,
    # captured at retrieval time so the auto-tuner can rescore with
    # alternative weights without re-running the embedder. Shape:
    # ``{node_id: {"vector": ..., "graph": ..., "recency": ...,
    # "type": ..., "project": ..., "lexical": ...}, ...}``. None for
    # pre-1.2 rows.
    score_components: dict[str, dict[str, float]] | None = None


@dataclass
class FeedbackEvent:
    """v1.2 phase 1: one row of user feedback on a retrieval hit.

    Every signal source -- explicit thumbs in the UI, implicit
    cite-copied tap, the daemon-side inferred re-query detector --
    writes a row here with the same shape so the v1.2 auto-tuner has a
    uniform input.

    `signal` is the numeric weight the optimizer consumes:
    +1.0 for thumbs_up, -1.0 for thumbs_down, +0.5 for cite_copied,
    -0.5 for inferred_requery. The mapping is centralized in
    :func:`signal_for_reason` so callers don't have to memorize it.

    Idempotency: `(query_id, node_id, reason)` is unique. Re-logging
    the same triple updates `signal` and `created_at` in place rather
    than producing duplicates -- matters when a user double-clicks
    the thumbs button or when the inferred detector fires twice.
    """

    id: int  # AUTOINCREMENT primary key; -1 means "not yet persisted"
    query_id: str
    node_id: str
    signal: float
    reason: str
    created_at: int


def signal_for_reason(reason: str) -> float:
    """Canonical signal value for a given reason.

    Centralized so the HTTP layer can default the signal field when
    the caller only supplies `reason`, and so all signal sources
    agree on magnitudes. Raises ValueError on unknown reasons.
    """
    if reason not in FEEDBACK_REASONS:
        raise ValueError(f"unknown feedback reason: {reason!r}")
    return {
        "thumbs_up": 1.0,
        "thumbs_down": -1.0,
        "cite_copied": 0.5,
        "inferred_requery": -0.5,
    }[reason]


@dataclass
class ActiveProject:
    """Singleton row in the ``active_project`` table.

    Tracks which project the daemon should treat as 'active' when a query
    arrives without an explicit ``project_key``. v1.1 hybrid contract:
    a per-call ``project_key`` overrides this; absence falls back to it.
    """

    project_key: str
    path: str
    since: int


# --- SQL --------------------------------------------------------------------


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS nodes (
  id               TEXT PRIMARY KEY,
  type             TEXT NOT NULL,
  name             TEXT NOT NULL,
  description      TEXT,
  body             TEXT NOT NULL,
  source_path      TEXT NOT NULL,
  source_kind      TEXT NOT NULL,
  project_key      TEXT,
  frontmatter_json TEXT,
  hash             TEXT NOT NULL,
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL,
  base             INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_nodes_type    ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project_key);
CREATE INDEX IF NOT EXISTS idx_nodes_updated ON nodes(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_nodes_source  ON nodes(source_path);

CREATE TABLE IF NOT EXISTS edges (
  src_id     TEXT NOT NULL,
  dst_id     TEXT NOT NULL,
  relation   TEXT NOT NULL,
  weight     REAL NOT NULL DEFAULT 1.0,
  created_at INTEGER NOT NULL,
  source     TEXT NOT NULL,
  PRIMARY KEY (src_id, dst_id, relation),
  FOREIGN KEY (src_id) REFERENCES nodes(id) ON DELETE CASCADE,
  FOREIGN KEY (dst_id) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges(relation);

CREATE TABLE IF NOT EXISTS sources (
  path            TEXT PRIMARY KEY,
  kind            TEXT NOT NULL,
  project_key     TEXT,
  last_indexed_at INTEGER,
  enabled         INTEGER NOT NULL DEFAULT 1,
  include         TEXT,
  exclude         TEXT
);

CREATE TABLE IF NOT EXISTS queries (
  id            TEXT PRIMARY KEY,
  prompt        TEXT NOT NULL,
  intent_tags   TEXT,
  retrieved_ids TEXT,
  scores        TEXT,
  ts            INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_queries_ts ON queries(ts DESC);

-- Active project state. Singleton row enforced via a CHECK constraint so
-- multiple-row inserts fail fast. Empty when no project is active.
CREATE TABLE IF NOT EXISTS active_project (
  singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
  project_key  TEXT NOT NULL,
  path         TEXT NOT NULL,
  since        INTEGER NOT NULL
);

-- v1.2 phase 1: user-feedback events on retrieval hits.
--
-- One row per (query_id, node_id, reason) tuple. Re-logging the same
-- triple updates `signal` and `created_at` in place instead of
-- producing duplicates -- the UNIQUE constraint + INSERT OR REPLACE
-- in :meth:`Store.log_feedback_event` enforces this.
--
-- FK cascades on both sides so deleting a node or purging an old
-- query row also drops any feedback that mentioned it. The query log
-- is intentionally not purged today but the cascade keeps the option
-- open without separate cleanup code.
CREATE TABLE IF NOT EXISTS feedback_event (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  query_id    TEXT NOT NULL,
  node_id     TEXT NOT NULL,
  signal      REAL NOT NULL,
  reason      TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  UNIQUE (query_id, node_id, reason),
  FOREIGN KEY (query_id) REFERENCES queries(id) ON DELETE CASCADE,
  FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_feedback_query ON feedback_event(query_id);
CREATE INDEX IF NOT EXISTS idx_feedback_node  ON feedback_event(node_id);
CREATE INDEX IF NOT EXISTS idx_feedback_ts    ON feedback_event(created_at DESC);
"""


# Vec-extension schema is created lazily on first call to a vec method, so
# tests that don't touch embeddings don't pay the extension load cost.
VEC_SCHEMA_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding float[{EMBEDDING_DIM}]);

CREATE TABLE IF NOT EXISTS chunk_meta (
  vec_rowid  INTEGER PRIMARY KEY,
  node_id    TEXT NOT NULL,
  chunk_idx  INTEGER NOT NULL,
  chunk_text TEXT NOT NULL,
  FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunk_meta_node ON chunk_meta(node_id);
"""


# --- Store ------------------------------------------------------------------


class Store:
    """Thin SQLite wrapper. Single connection, RLock-serialized for thread safety.

    The store can be shared across threads (e.g., the watcher's worker thread
    and the HTTP server's event loop). All public methods take the internal
    RLock for the duration of their work, including cursor consumption, so
    callers do not need to coordinate.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._vec_initialized = False
        with self._lock:
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA journal_mode = WAL")
            self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        # Lightweight migrations for tables that grew columns after a release
        # was already in users' hands. SQLite supports ALTER TABLE ADD COLUMN
        # idempotently only if we check first -- it errors on existing column.
        self._ensure_columns(
            "sources",
            {
                "include": "TEXT",
                "exclude": "TEXT",
            },
        )
        self._ensure_columns(
            "nodes",
            {
                "base": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        # v1.2 phase 2: store the query embedding alongside the audit row
        # so the inferred-re-query detector can do cosine over recent
        # prompts. NULL on legacy rows; the detector skips those.
        #
        # v1.2 phase 5: store the per-hit 6-term score components so the
        # auto-tuner can rescore with alternative weights without
        # re-running the embedder. NULL on legacy rows; the optimizer
        # filters them out.
        self._ensure_columns(
            "queries",
            {
                "embedding": "BLOB",
                "score_components": "TEXT",
            },
        )
        row = self.conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self.conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        self.conn.commit()

    def _ensure_columns(self, table: str, columns: dict[str, str]) -> None:
        """Add missing columns to ``table``. ``columns`` maps name -> SQL type."""
        existing = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, sql_type in columns.items():
            if name not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    def schema_version(self) -> int:
        with self._lock:
            row = self.conn.execute("SELECT version FROM schema_version").fetchone()
            return int(row["version"])

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- Nodes -------------------------------------------------------------

    def upsert_node(self, node: Node) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO nodes
                  (id, type, name, description, body, source_path, source_kind,
                   project_key, frontmatter_json, hash, created_at, updated_at, base)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  type             = excluded.type,
                  name             = excluded.name,
                  description      = excluded.description,
                  body             = excluded.body,
                  source_path      = excluded.source_path,
                  source_kind      = excluded.source_kind,
                  project_key      = excluded.project_key,
                  frontmatter_json = excluded.frontmatter_json,
                  hash             = excluded.hash,
                  updated_at       = excluded.updated_at,
                  base             = excluded.base
                """,
                (
                    node.id,
                    node.type,
                    node.name,
                    node.description,
                    node.body,
                    node.source_path,
                    node.source_kind,
                    node.project_key,
                    node.frontmatter_json,
                    node.hash,
                    node.created_at,
                    node.updated_at,
                    1 if node.base else 0,
                ),
            )
            self.conn.commit()

    def get_node(self, node_id: str) -> Node | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
            return self._row_to_node(row) if row else None

    def get_node_by_source(self, source_path: str) -> Node | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM nodes WHERE source_path = ?", (source_path,)
            ).fetchone()
            return self._row_to_node(row) if row else None

    def get_nodes_by_ids(self, ids: list[str]) -> dict[str, Node]:
        """Batched lookup. Returns ``{id: Node}`` for ids that exist.

        One SELECT instead of N. Use in retrieval scoring loops where a
        per-candidate ``get_node`` call would be O(K) round-trips.
        """
        if not ids:
            return {}
        with self._lock:
            placeholders = ",".join("?" * len(ids))
            rows = self.conn.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders})", ids
            ).fetchall()
        return {row["id"]: self._row_to_node(row) for row in rows}

    def get_edges_for_nodes(
        self,
        node_ids: list[str],
        *,
        relations: tuple[str, ...] | None = None,
    ) -> list[Edge]:
        """Batched: every edge with src_id OR dst_id in ``node_ids``.

        Optionally filter by relations. One SELECT regardless of fan-out.
        """
        if not node_ids:
            return []
        placeholders = ",".join("?" * len(node_ids))
        sql = f"SELECT * FROM edges WHERE src_id IN ({placeholders}) OR dst_id IN ({placeholders})"
        params: list[object] = list(node_ids) + list(node_ids)
        if relations:
            rel_ph = ",".join("?" * len(relations))
            sql += f" AND relation IN ({rel_ph})"
            params.extend(relations)
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [
            Edge(
                src_id=r["src_id"],
                dst_id=r["dst_id"],
                relation=r["relation"],
                weight=r["weight"],
                source=r["source"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def list_nodes(
        self,
        *,
        type: str | None = None,
        project_key: str | None = None,
        limit: int = 100,
        include_base: bool = True,
    ) -> list[Node]:
        """List nodes with optional filters.

        When ``project_key`` is set, the result includes nodes whose
        project_key matches **plus** any BASE-flagged nodes (since BASE
        knowledge applies to every project). Pass ``include_base=False``
        to suppress that union (admin / debugging use only).
        """
        sql = "SELECT * FROM nodes"
        clauses: list[str] = []
        params: list[object] = []
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        if project_key is not None:
            if include_base:
                # Project's nodes OR any BASE node, regardless of project.
                clauses.append("(project_key = ? OR base = 1)")
                params.append(project_key)
            else:
                clauses.append("project_key = ?")
                params.append(project_key)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_node(r) for r in rows]

    def delete_node(self, node_id: str) -> None:
        # Vec rows are not FK-linked (virtual table), so clean them up explicitly
        # before the cascade fires on chunk_meta.
        if self._vec_initialized:
            self.delete_chunks(node_id)
        with self._lock:
            self.conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            self.conn.commit()

    def count_nodes(
        self,
        *,
        project_key: str | None = None,
        include_base: bool = True,
    ) -> dict[str, int]:
        """Count nodes by type, optionally restricted to one project.

        v1.1: when a project filter is active in the UI, the type-counts
        dropdown should reflect that scope -- otherwise picking a project
        and seeing 'project (29)' (the global total) misleads the user.
        BASE-flagged nodes are included by default since they apply to
        every project.
        """
        sql = "SELECT type, COUNT(*) AS n FROM nodes"
        params: list[object] = []
        if project_key is not None:
            if include_base:
                sql += " WHERE (project_key = ? OR base = 1)"
            else:
                sql += " WHERE project_key = ?"
            params.append(project_key)
        sql += " GROUP BY type"
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return {row["type"]: row["n"] for row in rows}

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> Node:
        # `base` column was added by an idempotent migration; rows from
        # databases that haven't been re-opened since the migration have
        # the column as 0. Defensive get() so test fixtures with bare
        # SELECT * still work.
        try:
            base_val = bool(row["base"])
        except (KeyError, IndexError):
            base_val = False
        return Node(
            id=row["id"],
            type=row["type"],
            name=row["name"],
            description=row["description"],
            body=row["body"],
            source_path=row["source_path"],
            source_kind=row["source_kind"],
            project_key=row["project_key"],
            frontmatter_json=row["frontmatter_json"],
            hash=row["hash"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            base=base_val,
        )

    # --- Edges -------------------------------------------------------------

    def add_edge(
        self,
        src_id: str,
        dst_id: str,
        relation: str,
        *,
        weight: float = 1.0,
        source: str = "inferred",
    ) -> None:
        if relation not in EDGE_RELATIONS:
            raise ValueError(f"unknown edge relation: {relation!r}")
        if source not in EDGE_SOURCES:
            raise ValueError(f"unknown edge source: {source!r}")
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO edges (src_id, dst_id, relation, weight, created_at, source)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(src_id, dst_id, relation) DO UPDATE SET
                  weight = excluded.weight,
                  source = excluded.source
                """,
                (src_id, dst_id, relation, weight, int(time.time()), source),
            )
            self.conn.commit()

    def get_edges(
        self,
        *,
        src_id: str | None = None,
        dst_id: str | None = None,
        relation: str | None = None,
    ) -> list[Edge]:
        sql = "SELECT * FROM edges"
        clauses: list[str] = []
        params: list[object] = []
        if src_id is not None:
            clauses.append("src_id = ?")
            params.append(src_id)
        if dst_id is not None:
            clauses.append("dst_id = ?")
            params.append(dst_id)
        if relation is not None:
            clauses.append("relation = ?")
            params.append(relation)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [
            Edge(
                src_id=r["src_id"],
                dst_id=r["dst_id"],
                relation=r["relation"],
                weight=r["weight"],
                source=r["source"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def remove_edge(self, src_id: str, dst_id: str, relation: str) -> None:
        with self._lock:
            self.conn.execute(
                "DELETE FROM edges WHERE src_id = ? AND dst_id = ? AND relation = ?",
                (src_id, dst_id, relation),
            )
            self.conn.commit()

    # --- Sources -----------------------------------------------------------

    def register_source(
        self,
        path: str,
        kind: str,
        *,
        project_key: str | None = None,
        enabled: bool = True,
        include: str | None = None,
        exclude: str | None = None,
    ) -> None:
        if kind not in SOURCE_KINDS:
            raise ValueError(f"unknown source kind: {kind!r}")
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO sources (path, kind, project_key, enabled, include, exclude)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                  kind        = excluded.kind,
                  project_key = excluded.project_key,
                  enabled     = excluded.enabled,
                  include     = excluded.include,
                  exclude     = excluded.exclude
                """,
                (path, kind, project_key, 1 if enabled else 0, include, exclude),
            )
            self.conn.commit()

    def list_sources(self, *, only_enabled: bool = False) -> list[Source]:
        sql = "SELECT * FROM sources"
        if only_enabled:
            sql += " WHERE enabled = 1"
        with self._lock:
            rows = self.conn.execute(sql).fetchall()
        return [
            Source(
                path=r["path"],
                kind=r["kind"],
                project_key=r["project_key"],
                last_indexed_at=r["last_indexed_at"],
                enabled=bool(r["enabled"]),
                include=r["include"],
                exclude=r["exclude"],
            )
            for r in rows
        ]

    def get_source(self, path: str) -> Source | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM sources WHERE path = ?", (path,)).fetchone()
        if row is None:
            return None
        return Source(
            path=row["path"],
            kind=row["kind"],
            project_key=row["project_key"],
            last_indexed_at=row["last_indexed_at"],
            enabled=bool(row["enabled"]),
            include=row["include"],
            exclude=row["exclude"],
        )

    def update_source(
        self,
        path: str,
        *,
        project_key: str | None = ...,  # type: ignore[assignment]
        enabled: bool | None = None,
        include: str | None = ...,  # type: ignore[assignment]
        exclude: str | None = ...,  # type: ignore[assignment]
    ) -> Source | None:
        """Patch an existing source. Sentinel ``...`` = "leave field alone";
        ``None`` for nullable fields means "explicitly clear".

        Returns the updated source, or None if no row with that path exists.
        """
        existing = self.get_source(path)
        if existing is None:
            return None
        sets: list[str] = []
        params: list[object] = []
        if project_key is not ...:
            sets.append("project_key = ?")
            params.append(project_key)
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(1 if enabled else 0)
        if include is not ...:
            sets.append("include = ?")
            params.append(include)
        if exclude is not ...:
            sets.append("exclude = ?")
            params.append(exclude)
        if not sets:
            return existing
        params.append(path)
        with self._lock:
            self.conn.execute(
                f"UPDATE sources SET {', '.join(sets)} WHERE path = ?",
                params,
            )
            self.conn.commit()
        return self.get_source(path)

    def mark_source_indexed(self, path: str, *, when: int | None = None) -> None:
        ts = when if when is not None else int(time.time())
        with self._lock:
            self.conn.execute("UPDATE sources SET last_indexed_at = ? WHERE path = ?", (ts, path))
            self.conn.commit()

    def remove_source(self, path: str) -> int:
        """Unregister a source and cascade-delete its nodes.

        v1.1.1: previously this only DELETEd the sources row, leaving every
        node that was ingested from the path orphaned in the graph. The
        reindex orphan-sweep only looks at nodes whose source_path still
        matches a *registered* source, so once the source was gone its
        nodes lingered forever -- visible in the UI's Nodes / Graph pages
        long after the user expected them cleaned up.

        Now: walk every node whose ``source_path`` falls under ``path``
        (using the same :func:`path_under_source` semantics as the reindex
        reconciler) and delete it via :meth:`delete_node`, which also
        clears its vec/chunk_meta rows. Returns the number of nodes
        removed so the HTTP layer can show the user how much was cleaned
        up.

        Idempotent: removing an unregistered source is a no-op and
        returns 0.
        """
        src = self.get_source(path)
        if src is None:
            # Source isn't registered. Still issue the DELETE so callers
            # see a uniform "remove" semantic, but there's nothing to
            # cascade because we don't know what kind it was.
            with self._lock:
                self.conn.execute("DELETE FROM sources WHERE path = ?", (path,))
                self.conn.commit()
            return 0

        # Collect candidate node IDs in one read, then delete outside the
        # cursor so delete_node (which takes _lock itself) doesn't deadlock.
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, source_path FROM nodes",
            ).fetchall()
        to_delete = [r["id"] for r in rows if path_under_source(r["source_path"], path, src.kind)]
        for node_id in to_delete:
            self.delete_node(node_id)  # also deletes chunks via vec cleanup

        with self._lock:
            self.conn.execute("DELETE FROM sources WHERE path = ?", (path,))
            self.conn.commit()
        return len(to_delete)

    def find_orphan_nodes(self) -> list[Node]:
        """Return nodes whose source_path matches no registered source.

        v1.1.1 dedicated cleanup helper. Pre-1.1.1, removing a source did
        NOT cascade its nodes; users who removed a source under the old
        behavior still have those nodes in their store. The reindex
        orphan-sweep cannot reach them because it only walks nodes that
        live under a still-registered source.

        This walks every node, checks it against every registered source
        using the same :func:`path_under_source` semantics the reconciler
        uses, and returns the ones with no match. Use together with
        :meth:`delete_node` (or the ``mnemo source orphans --prune`` CLI)
        to clean up.

        Returns the orphan nodes in newest-first order.
        """
        sources = self.list_sources()
        with self._lock:
            rows = self.conn.execute("SELECT * FROM nodes ORDER BY updated_at DESC").fetchall()
        orphans: list[Node] = []
        for row in rows:
            node_path = row["source_path"]
            if not any(path_under_source(node_path, s.path, s.kind) for s in sources):
                orphans.append(self._row_to_node(row))
        return orphans

    # --- Active project (singleton) ---------------------------------------

    def get_active_project(self) -> ActiveProject | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT project_key, path, since FROM active_project WHERE singleton_id = 1"
            ).fetchone()
        if row is None:
            return None
        return ActiveProject(project_key=row["project_key"], path=row["path"], since=row["since"])

    def set_active_project(self, *, project_key: str, path: str) -> ActiveProject:
        ts = int(time.time())
        with self._lock:
            # UPSERT on the singleton row. SQLite supports ON CONFLICT REPLACE
            # via INSERT OR REPLACE; the CHECK keeps rows from multiplying.
            self.conn.execute(
                """
                INSERT OR REPLACE INTO active_project (singleton_id, project_key, path, since)
                VALUES (1, ?, ?, ?)
                """,
                (project_key, path, ts),
            )
            self.conn.commit()
        return ActiveProject(project_key=project_key, path=path, since=ts)

    def clear_active_project(self) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM active_project WHERE singleton_id = 1")
            self.conn.commit()

    # --- Query audit log ---------------------------------------------------

    def log_query(
        self,
        *,
        prompt: str,
        intent_tags: list[str],
        retrieved_ids: list[str],
        scores: dict[str, float],
        embedding: list[float] | None = None,
        score_components: dict[str, dict[str, float]] | None = None,
    ) -> str:
        """Write one audit row.

        v1.2 phase 2 added optional ``embedding`` for the inferred-
        re-query detector. v1.2 phase 5 added optional
        ``score_components`` for the auto-tuner. Pre-1.2 callers that
        omit both keep working; their row gets NULL in those columns
        and downstream consumers filter them out.
        """
        qid = uuid.uuid4().hex
        emb_blob: bytes | None = None
        if embedding is not None:
            if len(embedding) != EMBEDDING_DIM:
                raise ValueError(f"embedding dim {len(embedding)} != expected {EMBEDDING_DIM}")
            emb_blob = sqlite_vec.serialize_float32(embedding)
        comps_json: str | None = (
            json.dumps(score_components) if score_components is not None else None
        )
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO queries
                  (id, prompt, intent_tags, retrieved_ids, scores, ts,
                   embedding, score_components)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    qid,
                    prompt,
                    json.dumps(intent_tags),
                    json.dumps(retrieved_ids),
                    json.dumps(scores),
                    int(time.time()),
                    emb_blob,
                    comps_json,
                ),
            )
            self.conn.commit()
        return qid

    def recent_queries(self, limit: int = 50) -> list[Query]:
        # ts has 1-second resolution, so ties within the same second fall back
        # to rowid order (SQLite's monotonic insertion order).
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM queries ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            Query(
                id=r["id"],
                prompt=r["prompt"],
                intent_tags=json.loads(r["intent_tags"]) if r["intent_tags"] else [],
                retrieved_ids=json.loads(r["retrieved_ids"]) if r["retrieved_ids"] else [],
                scores=json.loads(r["scores"]) if r["scores"] else {},
                ts=r["ts"],
                embedding=None,
            )
            for r in rows
        ]

    def recent_queries_with_embeddings(
        self, *, window_seconds: int = 300, limit: int = 100
    ) -> list[Query]:
        """Return queries within the last ``window_seconds`` that have
        an embedding stored, newest first.

        v1.2 phase 2 helper for the inferred-re-query detector: filters
        out null-embedding (pre-1.2 legacy) rows AND anything older than
        the window so the cosine loop stays bounded.

        ``limit`` caps the row count to keep the detector cheap even if
        a burst of queries lands inside the window.
        """
        cutoff = int(time.time()) - window_seconds
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM queries
                WHERE ts >= ? AND embedding IS NOT NULL
                ORDER BY ts DESC, rowid DESC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
        result: list[Query] = []
        for r in rows:
            emb_blob = r["embedding"]
            # Convert blob back to list[float]. sqlite-vec serializes as
            # little-endian float32; numpy reads it the same way without
            # an explicit dtype dance.
            vec = list(_deserialize_float32(emb_blob)) if emb_blob else None
            result.append(
                Query(
                    id=r["id"],
                    prompt=r["prompt"],
                    intent_tags=json.loads(r["intent_tags"]) if r["intent_tags"] else [],
                    retrieved_ids=json.loads(r["retrieved_ids"]) if r["retrieved_ids"] else [],
                    scores=json.loads(r["scores"]) if r["scores"] else {},
                    ts=r["ts"],
                    embedding=vec,
                    score_components=None,
                )
            )
        return result

    def recent_queries_with_components(
        self,
        *,
        min_feedback: int = 1,
        limit: int = 5000,
    ) -> list[Query]:
        """Return queries with ``score_components`` populated, oldest first.

        v1.2 phase 5 helper for the auto-tuner:
        - Drops rows with NULL ``score_components`` (pre-1.2 legacy or
          embed-skipped paths) -- the optimizer cannot rescore them.
        - With ``min_feedback >= 1`` (default), also requires at least
          one row in ``feedback_event`` for that query so MRR has
          ground truth to score against. ``min_feedback=0`` returns
          everything with components (used by tests + diagnostic CLI).
        - Order is **ascending by ts** -- the auto-tuner does a
          time-ordered 80/20 train/val split and wants the oldest
          queries first so newer feedback never leaks into training.

        ``limit`` is a hard cap so a long-running daemon's audit log
        doesn't load entirely into memory.
        """
        with self._lock:
            if min_feedback <= 0:
                rows = self.conn.execute(
                    """
                    SELECT * FROM queries
                    WHERE score_components IS NOT NULL
                    ORDER BY ts ASC, rowid ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    """
                    SELECT q.* FROM queries q
                    WHERE q.score_components IS NOT NULL
                      AND (
                        SELECT COUNT(*) FROM feedback_event f
                        WHERE f.query_id = q.id
                      ) >= ?
                    ORDER BY q.ts ASC, q.rowid ASC
                    LIMIT ?
                    """,
                    (min_feedback, limit),
                ).fetchall()
        result: list[Query] = []
        for r in rows:
            comps = json.loads(r["score_components"]) if r["score_components"] else None
            result.append(
                Query(
                    id=r["id"],
                    prompt=r["prompt"],
                    intent_tags=json.loads(r["intent_tags"]) if r["intent_tags"] else [],
                    retrieved_ids=json.loads(r["retrieved_ids"]) if r["retrieved_ids"] else [],
                    scores=json.loads(r["scores"]) if r["scores"] else {},
                    ts=r["ts"],
                    embedding=None,
                    score_components=comps,
                )
            )
        return result

    # --- Feedback events (v1.2 phase 1) ------------------------------------

    def log_feedback_event(
        self,
        *,
        query_id: str,
        node_id: str,
        signal: float,
        reason: str,
        when: int | None = None,
    ) -> FeedbackEvent:
        """Insert or update one feedback row.

        Idempotent on ``(query_id, node_id, reason)``: re-logging the
        same triple refreshes ``signal`` and ``created_at`` rather than
        creating duplicate rows. This is critical for the UI (a
        double-clicked thumbs button shouldn't double-count) and for
        the inferred re-query detector (which can fire repeatedly for
        the same window).

        ``when`` is exposed for tests; production callers leave it as
        None and the row gets the current epoch.
        """
        if reason not in FEEDBACK_REASONS:
            raise ValueError(f"unknown feedback reason: {reason!r}")
        ts = when if when is not None else int(time.time())
        with self._lock:
            # Compose an UPSERT keyed on the unique (query_id, node_id, reason)
            # constraint defined in the schema. ON CONFLICT...DO UPDATE keeps
            # the original id (autoincrement primary key) and refreshes the
            # mutable fields.
            cur = self.conn.execute(
                """
                INSERT INTO feedback_event (query_id, node_id, signal, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(query_id, node_id, reason)
                DO UPDATE SET signal = excluded.signal,
                              created_at = excluded.created_at
                RETURNING id, query_id, node_id, signal, reason, created_at
                """,
                (query_id, node_id, signal, reason, ts),
            )
            row = cur.fetchone()
            self.conn.commit()
        return FeedbackEvent(
            id=row["id"],
            query_id=row["query_id"],
            node_id=row["node_id"],
            signal=row["signal"],
            reason=row["reason"],
            created_at=row["created_at"],
        )

    def list_feedback_events(
        self,
        *,
        query_id: str | None = None,
        node_id: str | None = None,
        limit: int = 1000,
    ) -> list[FeedbackEvent]:
        """List feedback events, newest first.

        Without filters returns the most recent ``limit`` rows globally
        (useful for retune dataset assembly). With ``query_id`` set the
        UI can show what feedback exists for a specific query; with
        ``node_id`` set, what feedback a single node has accumulated.
        """
        clauses: list[str] = []
        params: list[object] = []
        if query_id is not None:
            clauses.append("query_id = ?")
            params.append(query_id)
        if node_id is not None:
            clauses.append("node_id = ?")
            params.append(node_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self.conn.execute(
                f"SELECT * FROM feedback_event {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return [
            FeedbackEvent(
                id=r["id"],
                query_id=r["query_id"],
                node_id=r["node_id"],
                signal=r["signal"],
                reason=r["reason"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # --- Vector index (sqlite-vec) ----------------------------------------

    def ensure_vec(self) -> None:
        """Lazily load the sqlite-vec extension and create vec/meta tables.

        Called automatically by all vec methods. Safe to call multiple times.
        """
        if self._vec_initialized:
            return
        with self._lock:
            if self._vec_initialized:
                return
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            self.conn.executescript(VEC_SCHEMA_SQL)
            self.conn.commit()
            self._vec_initialized = True

    def upsert_chunks(self, node_id: str, chunks: list[tuple[int, list[float], str]]) -> None:
        """Replace all chunks for ``node_id``. ``chunks`` = [(idx, vector, text), ...]."""
        self.ensure_vec()
        with self._lock:
            # Remove old chunks (cascade-safe; both tables get their entries cleared)
            old = self.conn.execute(
                "SELECT vec_rowid FROM chunk_meta WHERE node_id = ?", (node_id,)
            ).fetchall()
            for row in old:
                self.conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (row["vec_rowid"],))
            self.conn.execute("DELETE FROM chunk_meta WHERE node_id = ?", (node_id,))

            # Insert new chunks
            for chunk_idx, vector, text in chunks:
                if len(vector) != EMBEDDING_DIM:
                    raise ValueError(f"vector dim {len(vector)} != expected {EMBEDDING_DIM}")
                vec_blob = sqlite_vec.serialize_float32(vector)
                cur = self.conn.execute(
                    "INSERT INTO vec_chunks (embedding) VALUES (?)", (vec_blob,)
                )
                self.conn.execute(
                    """INSERT INTO chunk_meta (vec_rowid, node_id, chunk_idx, chunk_text)
                       VALUES (?, ?, ?, ?)""",
                    (cur.lastrowid, node_id, chunk_idx, text),
                )
            self.conn.commit()

    def delete_chunks(self, node_id: str) -> None:
        self.ensure_vec()
        with self._lock:
            old = self.conn.execute(
                "SELECT vec_rowid FROM chunk_meta WHERE node_id = ?", (node_id,)
            ).fetchall()
            for row in old:
                self.conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (row["vec_rowid"],))
            self.conn.execute("DELETE FROM chunk_meta WHERE node_id = ?", (node_id,))
            self.conn.commit()

    def get_chunk_embeddings(
        self,
        pairs: list[tuple[str, int]],
    ) -> dict[tuple[str, int], list[float]]:
        """Bulk-fetch chunk embeddings by ``(node_id, chunk_idx)`` pairs.

        v1.2 phase 4 MMR re-rank reads back each candidate's best-chunk
        embedding so it can compute pairwise cosine for the diversity
        penalty. Doing it as one query (via a VALUES-CTE) instead of N
        round-trips keeps the rerank step in the sub-millisecond budget.

        Pairs that don't resolve (deleted node, stale chunk_idx) are
        silently omitted from the result so the caller can rely on
        ``dict.get(pair)`` returning None for those cases.
        """
        if not pairs:
            return {}
        self.ensure_vec()

        # SQLite has no native tuple-IN; build a values list and JOIN.
        placeholders = ",".join(["(?, ?)"] * len(pairs))
        flat: list[object] = []
        for nid, idx in pairs:
            flat.append(nid)
            flat.append(idx)
        sql = f"""
            WITH wanted(node_id, chunk_idx) AS (VALUES {placeholders})
            SELECT m.node_id, m.chunk_idx, v.embedding
            FROM wanted w
            JOIN chunk_meta m
              ON m.node_id = w.node_id AND m.chunk_idx = w.chunk_idx
            JOIN vec_chunks v ON v.rowid = m.vec_rowid
        """
        with self._lock:
            rows = self.conn.execute(sql, flat).fetchall()
        out: dict[tuple[str, int], list[float]] = {}
        for r in rows:
            blob = r["embedding"]
            if blob is None:
                continue
            out[(r["node_id"], r["chunk_idx"])] = _deserialize_float32(blob)
        return out

    def list_embedded_node_ids(self) -> set[str]:
        self.ensure_vec()
        with self._lock:
            rows = self.conn.execute("SELECT DISTINCT node_id FROM chunk_meta").fetchall()
        return {r["node_id"] for r in rows}

    def vec_search(
        self,
        query_vec: list[float],
        *,
        k: int = 20,
        type_filter: list[str] | None = None,
        project_key: str | None = None,
    ) -> list[tuple[str, int, str, float]]:
        """Return ``k`` nearest chunks as (node_id, chunk_idx, chunk_text, distance).

        Sorted ascending by distance (lower = more similar; sqlite-vec uses L2
        distance on normalized vectors, so it's monotonic in cosine distance).
        Optional filters are applied via JOIN on the nodes table.
        """
        self.ensure_vec()
        if len(query_vec) != EMBEDDING_DIM:
            raise ValueError(f"query dim {len(query_vec)} != expected {EMBEDDING_DIM}")
        vec_blob = sqlite_vec.serialize_float32(query_vec)

        # Oversample if filters are present so we have enough survivors after filtering.
        oversample = 4 if (type_filter or project_key) else 1
        sql = """
            SELECT m.node_id, m.chunk_idx, m.chunk_text, v.distance
            FROM vec_chunks v
            JOIN chunk_meta m ON v.rowid = m.vec_rowid
            JOIN nodes n      ON m.node_id = n.id
            WHERE v.embedding MATCH ?
              AND k = ?
        """
        params: list[object] = [vec_blob, k * oversample]
        if type_filter:
            placeholders = ",".join(["?"] * len(type_filter))
            sql += f" AND n.type IN ({placeholders})"
            params.extend(type_filter)
        if project_key is not None:
            sql += " AND n.project_key = ?"
            params.append(project_key)
        sql += " ORDER BY v.distance LIMIT ?"
        params.append(k)

        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [(r["node_id"], r["chunk_idx"], r["chunk_text"], float(r["distance"])) for r in rows]
        return [
            Query(
                id=r["id"],
                prompt=r["prompt"],
                intent_tags=json.loads(r["intent_tags"]) if r["intent_tags"] else [],
                retrieved_ids=json.loads(r["retrieved_ids"]) if r["retrieved_ids"] else [],
                scores=json.loads(r["scores"]) if r["scores"] else {},
                ts=r["ts"],
            )
            for r in rows
        ]
