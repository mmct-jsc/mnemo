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
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1


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


@dataclass
class Query:
    id: str
    prompt: str
    intent_tags: list[str]
    retrieved_ids: list[str]
    scores: dict[str, float]
    ts: int


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
  updated_at       INTEGER NOT NULL
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
  enabled         INTEGER NOT NULL DEFAULT 1
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
        with self._lock:
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA journal_mode = WAL")
            self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        row = self.conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self.conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        self.conn.commit()

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
                   project_key, frontmatter_json, hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                  updated_at       = excluded.updated_at
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

    def list_nodes(
        self,
        *,
        type: str | None = None,
        project_key: str | None = None,
        limit: int = 100,
    ) -> list[Node]:
        sql = "SELECT * FROM nodes"
        clauses: list[str] = []
        params: list[object] = []
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        if project_key is not None:
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
        with self._lock:
            self.conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            self.conn.commit()

    def count_nodes(self) -> dict[str, int]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT type, COUNT(*) AS n FROM nodes GROUP BY type"
            ).fetchall()
        return {row["type"]: row["n"] for row in rows}

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> Node:
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
    ) -> None:
        if kind not in SOURCE_KINDS:
            raise ValueError(f"unknown source kind: {kind!r}")
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO sources (path, kind, project_key, enabled)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                  kind        = excluded.kind,
                  project_key = excluded.project_key,
                  enabled     = excluded.enabled
                """,
                (path, kind, project_key, 1 if enabled else 0),
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
            )
            for r in rows
        ]

    def mark_source_indexed(self, path: str, *, when: int | None = None) -> None:
        ts = when if when is not None else int(time.time())
        with self._lock:
            self.conn.execute("UPDATE sources SET last_indexed_at = ? WHERE path = ?", (ts, path))
            self.conn.commit()

    def remove_source(self, path: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM sources WHERE path = ?", (path,))
            self.conn.commit()

    # --- Query audit log ---------------------------------------------------

    def log_query(
        self,
        *,
        prompt: str,
        intent_tags: list[str],
        retrieved_ids: list[str],
        scores: dict[str, float],
    ) -> str:
        qid = uuid.uuid4().hex
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO queries (id, prompt, intent_tags, retrieved_ids, scores, ts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    qid,
                    prompt,
                    json.dumps(intent_tags),
                    json.dumps(retrieved_ids),
                    json.dumps(scores),
                    int(time.time()),
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
            )
            for r in rows
        ]
