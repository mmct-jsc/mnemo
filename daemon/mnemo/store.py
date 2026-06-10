"""SQLite-backed store for mnemo nodes, edges, sources, and queries.

The store is the only component that touches SQLite directly. Higher layers
(ingest, retrieve, server) use the typed dataclasses defined here.

Schema is defined in ``SCHEMA_SQL`` and is idempotent: the constructor calls
``CREATE TABLE IF NOT EXISTS`` for every table, so reopening an existing
database is safe.
"""

from __future__ import annotations

import hashlib
import json
import re
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


_TRUNCATION_MARKER_RE = re.compile(r"\n\.\.\. \(\d+ more lines\)\s*$")


def _strip_truncation_marker(body: str | None) -> str:
    """Strip the legacy ``\\n... (N more lines)`` suffix from a code node
    body.

    v2.0 phase 4 wrote this marker into the stored body so the UI
    could show "this is truncated". v2.1 polish: the marker leaks
    into embeddings, query hits, and (when chat lands in v3) into
    user-visible LLM output. Keeping it only as a UI affordance
    via separate metadata leaves the body as clean source code.

    This stripper runs on every node read so:
    - Old data with markers (pre-v2.1) gets cleaned at read time --
      no schema migration needed; a reindex will rewrite cleanly.
    - New ingest no longer adds the marker
      (see ``parsers.code._truncate_lines``).
    - Embeddings already computed include the marker, but a
      reindex re-embeds clean text.
    """
    if not body:
        return body or ""
    return _TRUNCATION_MARKER_RE.sub("", body)


def _edge_confidence(row: object) -> float:
    """Read the ``confidence`` column from an edges row, defaulting to 1.0.

    v2.0 phase 1 added the column via ``_ensure_columns`` so pre-v2.0
    stores back-fill on first open. ``sqlite3.Row`` doesn't accept ``.get``,
    so guard membership before indexing. Defensive against any future
    SELECT that doesn't include the column.
    """
    try:
        keys = row.keys()  # type: ignore[attr-defined]
    except AttributeError:
        return 1.0
    if "confidence" not in keys:
        return 1.0
    val = row["confidence"]  # type: ignore[index]
    return 1.0 if val is None else float(val)


SCHEMA_VERSION = 1
EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 dim. Bump + reindex to switch models.
# v5.27.0: only the first 32 KB of a body is FTS-indexed (mirrors the
# retrieval-side lexical cap) so a giant plan_doc doesn't bloat the index.
FTS_BODY_CAP = 32 * 1024


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
        # v2.0 phase 1: decision provenance. One node per git commit
        # ingested from a code_repo source (phase 9 wires the parser).
        "commit",
        # v2.0 phase 4: Tier 1 universal code graph. One node per
        # source file (``code_module``) plus one per top-level
        # declaration (``code_function`` / ``code_class``) and one per
        # class method (``code_method``). Tier 1 is language-structure
        # only -- call resolution lands in phase 5 (Tier 2), framework
        # extractors in phases 6-8 (Tier 3).
        "code_module",
        "code_function",
        "code_class",
        "code_method",
        # v2.0 phase 6: Tier 3 backend framework routes. One ``code_route``
        # per detected route declaration (FastAPI ``@router.get(...)``,
        # Flask ``@app.route(...)``, Express ``app.get(path, handler)``,
        # ...). The ``routes_to`` edge wires each route to its handler
        # function.
        "code_route",
        # v2.0 phase 7: Tier 3 frontend + cross-stack. ``code_component``
        # is a React / Vue / Svelte component; ``code_endpoint`` is the
        # cross-stack URI anchor that both backend routes and frontend
        # fetches converge on, so a single graph traversal can walk
        # Component -> Endpoint <- Route -> Handler.
        "code_component",
        "code_endpoint",
    }
)

SOURCE_KINDS = frozenset(
    {
        "memory_dir",
        "claude_md",
        "plan_dir",
        "transcripts",
        # v2.0 phase 1: new source kinds. ``code_repo`` is the
        # tree-sitter-indexed shape (phase 4 wires the parser);
        # ``docs_dir`` is a markdown harvest without the frontmatter
        # requirement that memory_dir enforces.
        "code_repo",
        "docs_dir",
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
        # v2.0 phase 1: decision provenance family. Producers land in
        # phase 9 (commit auto-linker). Carrying them in the schema now
        # so the column / index plumbing is in place before parsers run.
        "references_function",  # commit -> code_function it touched
        "motivated_by",  # commit -> memory_feedback / plan_doc / memory_project
        "closed_by",  # memory_feedback / plan_doc -> commit that resolved it
        # v2.0 phase 4: Tier 1 structural edges. ``defines`` links a
        # ``code_module`` to its top-level declarations; ``method_of``
        # links a ``code_method`` to its containing ``code_class``;
        # ``imports`` links a module to another (best-effort cross-file
        # resolution -- unresolved targets simply don't get an edge).
        "defines",
        "method_of",
        "imports",
        # v2.0 phase 5: Tier 2 semantic call graph. ``calls`` links a
        # caller function/method to a callee. Stack-Graphs-style scope
        # resolution emits edges with high confidence (0.95) for
        # within-file resolution and lower (0.8) for cross-file via
        # imports. Unresolved call sites do NOT emit an edge -- the
        # graph stays clean and the LLM can lexical-match if it cares.
        "calls",
        # v2.0 phase 6: Tier 3 backend framework wiring. ``routes_to``
        # links a ``code_route`` node to its handler function so
        # cross-stack sitemap queries can walk Component -> Route ->
        # Handler -> Service in phase 7+.
        "routes_to",
        # v2.0 phase 7: ``renders`` links a parent component to a child
        # component (React composition). ``at_endpoint`` is the
        # cross-stack glue: both a ``code_route`` AND a
        # ``code_component`` point at the same ``code_endpoint`` URI
        # node when their HTTP-method + path match, which is what
        # creates the Component <-> Route join via a single endpoint
        # traversal.
        "renders",
        "at_endpoint",
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
    # v5 phase 1: local_only nodes are excluded from pasteable prompts
    # (the prompt-architect output may land in a foreign LLM). Set on
    # parse via frontmatter ``local_only: true``, a ``_private`` path
    # segment, or a body starting with ``[LOCAL ONLY]``. Default False
    # so legacy rows stay fully visible.
    local_only: bool = False

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
        local_only: bool = False,
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
            local_only=local_only,
        )


@dataclass
class Edge:
    src_id: str
    dst_id: str
    relation: str
    weight: float
    source: str
    created_at: int
    # v2.0 phase 1: per-edge uncertainty. Defaults to 1.0 so all v1.x
    # edges and any hand-built Edge() in tests keep their old semantics
    # without a value override. Later phases populate this for inferred
    # edges: Tier 2 unresolved ``calls`` = 0.5, Tier 3 framework matches
    # = 0.9, auto-inferred ``motivated_by`` / ``closed_by`` = 0.6 (bumped
    # to 0.9 when the commit body cites the doc explicitly).
    confidence: float = 1.0


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


# --- Audit queue (v5.22.0, Phase 4a) -----------------------------------

_AUDIT_STATUSES = ("open", "dismissed", "resolved")


@dataclass
class AuditFinding:
    """One row of the proactive ``audit_queue``.

    ``node_ids`` is decoded from the stored JSON array; ``locus`` is the
    problem locus the fingerprint keys on (joined missing targets /
    concept / symbol) or ``None``. Times are epoch seconds.
    """

    fingerprint: str
    type: str
    severity: str
    node_ids: list[str]
    description: str
    locus: str | None
    status: str
    first_seen: int
    last_seen: int


def _finding_locus(finding: dict) -> str | None:
    """The problem locus a finding's fingerprint keys on, derived from the
    detector's per-finding extras: orphan_reference -> joined
    ``missing_targets``; semantic_orphan -> ``concept``; dead_code /
    god_object -> ``symbol``. Deterministic (sorted) so the fingerprint is
    stable across audit runs. ``None`` when the finding has no extra locus
    (e.g. ``stale``)."""
    missing = finding.get("missing_targets")
    if missing:
        return ",".join(sorted(missing))
    concept = finding.get("concept")
    if concept:
        return str(concept)
    symbol = finding.get("symbol")
    if symbol:
        return str(symbol)
    return None


def _finding_fingerprint(finding: dict) -> str:
    """Stable, order-independent identity for a finding:
    ``sha1(type + "\\n" + sorted(node_ids) + "\\n" + (locus or ""))``. Two
    audit runs that produce the same logical finding map to the same
    fingerprint, so the queue de-duplicates + tracks status across runs."""
    node_ids = ",".join(sorted(finding.get("node_ids", [])))
    locus = _finding_locus(finding) or ""
    raw = f"{finding.get('type', '')}\n{node_ids}\n{locus}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# v3 phase 1: agentic chat companion (design doc S5). Roles map 1:1
# to the provider message protocol; tool_call / tool_result are the
# agent-loop turns surfaced as collapsible rows in the UI.
CHAT_ROLES = frozenset({"user", "assistant", "tool_call", "tool_result", "system"})


@dataclass
class Conversation:
    """One agentic-chat thread. ``page_context`` is the auto-attached
    page state (``{page, selected_node_id, filters}``) the companion
    uses to ground answers. ``archived_at`` is a soft-delete -- archived
    conversations are still fetchable by id but hidden from the rail."""

    id: str
    name: str
    project_key: str | None
    page_context: dict | None
    provider: str
    model: str
    created_at: int
    updated_at: int
    archived_at: int | None = None
    # v3.1: hybrid-compaction state / running summary (provider-agnostic
    # fallback path) and the running token counter shown in the UI.
    summary_json: dict | None = None
    tokens_total: int = 0


@dataclass
class ChatMessage:
    """One turn. ``content`` is the parsed ``content_json``:
    ``{text?, tool_call?, tool_result?, citations?: [node_id]}``.
    The v3.1 ``token_*`` fields are the per-turn provider usage (NULL on
    legacy / unmeasured rows)."""

    id: str
    conversation_id: str
    seq: int
    role: str
    content: dict
    created_at: int
    token_in: int | None = None
    token_out: int | None = None
    cache_read: int | None = None


@dataclass
class ChatBookmark:
    """A user-pinned message in a conversation (v3.1). ``message_seq``
    is the target turn's ``seq``; ``label`` is an optional free-text
    note. Server-persisted so it survives reload + device."""

    id: str
    conversation_id: str
    message_seq: int
    label: str | None
    created_at: int


@dataclass
class ChatPermission:
    """A persisted always-allow grant. ``project_key`` None = global
    (applies to every project); a project-scoped grant only applies to
    that project_key."""

    project_key: str | None
    tool_name: str
    granted_at: int


# --- SQL --------------------------------------------------------------------


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

-- v5.27.0: nodes_fts (FTS5) is created in _init_schema, not here --
-- executescript can't parameterize and virtual tables need the
-- backfill guard anyway.
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
  -- v2.0 phase 1: per-edge uncertainty. Pre-v2.0 rows back-fill to 1.0
  -- via the ADD COLUMN ... DEFAULT path in _ensure_columns.
  confidence REAL NOT NULL DEFAULT 1.0,
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

-- v2.6 phase 1: workspaces -- user-named bundles of project_keys + filter
-- prefs. The active workspace scopes every page's view (Nebula / /code /
-- Search / v3 chat) to the bundle's projects; with no active workspace
-- the UI shows BASE-flagged nodes only.
--
-- Time columns store epoch milliseconds (NOT seconds like the rest of
-- the schema). The extra precision keeps rapid create / activate cycles
-- ordered correctly across a single test run, and the UI consumes the
-- value directly via `new Date(ms)`.
CREATE TABLE IF NOT EXISTS workspaces (
  id                TEXT PRIMARY KEY,
  name              TEXT NOT NULL UNIQUE,
  project_keys      TEXT NOT NULL,                 -- JSON array
  filter_prefs      TEXT,                          -- JSON object (or NULL)
  page_state        TEXT,                          -- JSON object (or NULL)
  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL,
  last_activated_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_workspaces_activated
  ON workspaces(last_activated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workspaces_created
  ON workspaces(created_at DESC);

-- Singleton row holding the active workspace pointer.
-- Empty (no row) / NULL active_id means "no workspace active = BASE-only
-- UI mode". FK ON DELETE SET NULL so deleting the active workspace
-- automatically clears the pointer; PRAGMA foreign_keys = ON (set in
-- Store.__init__) enforces this.
CREATE TABLE IF NOT EXISTS workspace_state (
  singleton    INTEGER PRIMARY KEY CHECK (singleton = 0),
  active_id    TEXT REFERENCES workspaces(id) ON DELETE SET NULL,
  activated_at INTEGER
);

-- Per-path decisions from the reindex report's malformed + suspicious
-- sections. Reindex consults this table BEFORE classifying so a user's
-- "always_skip" / "always_keep" / "retry" choice persists across runs.
CREATE TABLE IF NOT EXISTS source_overrides (
  source_path TEXT PRIMARY KEY,
  decision    TEXT NOT NULL,    -- 'always_skip' | 'always_keep' | 'retry'
  reason      TEXT,             -- e.g. 'suspicious:suspected_secret'
  decided_at  INTEGER NOT NULL  -- epoch milliseconds
);

CREATE INDEX IF NOT EXISTS idx_source_overrides_decided
  ON source_overrides(decided_at DESC);

-- v2.6.3: cached Nebula force-layout positions. The GPU force
-- simulation is expensive on 10 k+ nodes; once it settles we
-- persist the final positions keyed by (scope_key, fingerprint).
-- The client loads them directly on the next visit -> instant
-- settled render, no re-simulation. The fingerprint is a hash of
-- the in-scope node id set + edge count, so a reindex / node-write
-- that changes the graph naturally changes the fingerprint and
-- invalidates the cache (exactly "recompute only on reindex /
-- impact actions"). One row per scope; PUT overwrites. This lives
-- in the ALWAYS-run SCHEMA_SQL (not the lazy VEC_SCHEMA_SQL) so the
-- table exists even on stores that never touch embeddings.
CREATE TABLE IF NOT EXISTS graph_layout (
  scope_key   TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  positions   TEXT NOT NULL,   -- JSON array [x0,y0,x1,y1,...]
  updated_at  INTEGER NOT NULL
);

-- v3 phase 1: agentic chat companion. Conversations + messages +
-- the always-allow permission allowlist are first-class rows (design
-- doc 2026-05-14-mnemo-v3-design.md S5). Lives in the always-run
-- SCHEMA_SQL so a pre-v3 DB grows the tables on first reopen
-- (idempotent executescript). chat_messages FK cascades so purging a
-- conversation drops its messages (PRAGMA foreign_keys = ON, set in
-- Store.__init__).
CREATE TABLE IF NOT EXISTS chat_conversations (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  project_key  TEXT,
  page_context TEXT,                 -- JSON object (or NULL)
  provider     TEXT NOT NULL,
  model        TEXT NOT NULL,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL,
  archived_at  INTEGER               -- soft-delete; NULL = active
);

CREATE INDEX IF NOT EXISTS idx_chat_conv_project
  ON chat_conversations(project_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL
                  REFERENCES chat_conversations(id) ON DELETE CASCADE,
  seq             INTEGER NOT NULL, -- 0..N within a conversation
  role            TEXT NOT NULL,    -- user|assistant|tool_call|tool_result|system
  content_json    TEXT NOT NULL,    -- JSON {text?,tool_call?,tool_result?,citations?}
  created_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_msg_conv
  ON chat_messages(conversation_id, seq);

-- project_key NULL = a global always-allow grant. SQLite permits
-- repeated NULLs in a composite PK, so grant_permission guards
-- existence before INSERT -> idempotent regardless of the NULL-PK
-- quirk.
CREATE TABLE IF NOT EXISTS chat_permissions (
  project_key TEXT,
  tool_name   TEXT NOT NULL,
  granted_at  INTEGER NOT NULL,
  PRIMARY KEY (project_key, tool_name)
);

-- v3.1: server-persisted bookmarks. FK cascades with the owning
-- conversation (PRAGMA foreign_keys = ON). message_seq points at a
-- chat_messages.seq; we don't FK it (messages have no per-seq unique
-- index and a bookmark can briefly outlive a re-streamed turn).
CREATE TABLE IF NOT EXISTS chat_bookmarks (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL
                  REFERENCES chat_conversations(id) ON DELETE CASCADE,
  message_seq     INTEGER NOT NULL,
  label           TEXT,
  created_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_bm_conv
  ON chat_bookmarks(conversation_id, message_seq);

-- Phase 3 / Angle #2 (hosted context API): API key + per-key quota +
-- per-period usage metering. The hosted tier is OFF by default at the
-- endpoint layer (a config flag enables api-key auth on /v1/query;
-- self-host loopback stays unauthenticated). Tables ship in v0.1 of
-- Phase 3 / Task 2.1; the issuance CLI (Task 2.2), auth dependency
-- (Task 2.3), metering hook (Task 2.4), and quota enforcement
-- (Task 2.5) consume them. The schema is harmless for any install
-- that does not enable hosted mode.

CREATE TABLE IF NOT EXISTS api_key (
  id          TEXT PRIMARY KEY,
  hash        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  revoked_at  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_api_key_hash ON api_key(hash);

CREATE TABLE IF NOT EXISTS quota (
  api_key_id   TEXT NOT NULL
               REFERENCES api_key(id) ON DELETE CASCADE,
  period       TEXT NOT NULL,
  max_queries  INTEGER NOT NULL,
  max_tokens   INTEGER NOT NULL,
  PRIMARY KEY (api_key_id, period)
);

CREATE TABLE IF NOT EXISTS usage_period (
  api_key_id  TEXT NOT NULL
              REFERENCES api_key(id) ON DELETE CASCADE,
  period      TEXT NOT NULL,
  queries     INTEGER NOT NULL DEFAULT 0,
  tokens      INTEGER NOT NULL DEFAULT 0,
  updated_at  INTEGER NOT NULL,
  PRIMARY KEY (api_key_id, period)
);

CREATE INDEX IF NOT EXISTS idx_usage_period_key ON usage_period(api_key_id);

-- v5.22.0 Phase 4a: proactive audit queue. The deterministic auditor runs
-- after each reindex and reconciles its findings into this table by a
-- stable fingerprint (sha1 of type + sorted node_ids + locus), so the same
-- finding is de-duplicated + status-tracked across runs. READ-ONLY w.r.t.
-- the node graph -- nothing here mutates a node; only queue metadata
-- changes. Lives in the ALWAYS-run SCHEMA_SQL (not the lazy VEC_SCHEMA_SQL)
-- so the table exists on stores that never touch embeddings. Status
-- lifecycle: open (the nav badge counts these) / dismissed (user "ignore";
-- sticky) / resolved (auto when an open finding disappears; reopens if
-- re-detected).
CREATE TABLE IF NOT EXISTS audit_queue (
  fingerprint TEXT PRIMARY KEY,
  type        TEXT NOT NULL,
  severity    TEXT NOT NULL,
  node_ids    TEXT NOT NULL,                 -- JSON array
  description TEXT NOT NULL,
  locus       TEXT,                          -- nullable problem locus
  status      TEXT NOT NULL DEFAULT 'open',  -- open|dismissed|resolved
  first_seen  INTEGER NOT NULL,
  last_seen   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_queue_status ON audit_queue(status);
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
                # v5 phase 1: local_only flag excludes nodes from
                # pasteable prompt-architect output. Additive migration
                # so legacy DBs grow the column with default 0 on first
                # reopen; the prompt-architect's retrieve.query call
                # passes ``exclude_local_only=True`` to filter them.
                "local_only": "INTEGER NOT NULL DEFAULT 0",
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
        # Phase 3 / Task 2.2: api_key.salt for per-key salted SHA-256
        # hashing. Each key has its own random 16-byte salt; verify
        # iterates the (small) active-key set + recomputes the hash.
        # Additive migration -- old empty rows (there are none in the
        # wild yet, but defense-in-depth) get NULL salt.
        self._ensure_columns(
            "api_key",
            {
                "salt": "TEXT",
            },
        )
        # v2.0 phase 1: per-edge uncertainty. Existing v1.x edges back-fill
        # to 1.0 (the column DEFAULT) so retrieval scoring stays
        # bit-for-bit identical until later phases start emitting <1.0
        # values for Tier 2/3 inferred edges and the provenance auto-linker.
        self._ensure_columns(
            "edges",
            {
                "confidence": "REAL NOT NULL DEFAULT 1.0",
            },
        )
        # v3.1: per-turn provider token usage (NULL on legacy / unmeasured
        # rows -- the UI dims those) and per-conversation compaction state
        # + running token counter. Additive so a pre-v3.1 chat DB upgrades
        # in place on first reopen.
        self._ensure_columns(
            "chat_messages",
            {
                "token_in": "INTEGER",
                "token_out": "INTEGER",
                "cache_read": "INTEGER",
            },
        )
        self._ensure_columns(
            "chat_conversations",
            {
                "summary_json": "TEXT",
                "tokens_total": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        # v5.27.0: FTS5 lexical channel. Bodies capped at 32 KB (mirrors the
        # retrieval-side lexical cap) so a giant plan_doc doesn't bloat the
        # index. Backfilled once when the table is empty (pre-v5.27 stores
        # upgrade in place on first reopen).
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts "
            "USING fts5(id UNINDEXED, name, description, body)"
        )
        fts_rows = self.conn.execute("SELECT count(*) FROM nodes_fts").fetchone()[0]
        if fts_rows == 0:
            self.conn.execute(
                "INSERT INTO nodes_fts (id, name, description, body) "
                "SELECT id, name, coalesce(description, ''), "
                "substr(coalesce(body, ''), 1, ?) FROM nodes",
                (FTS_BODY_CAP,),
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
                   project_key, frontmatter_json, hash, created_at, updated_at, base,
                   local_only)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                  base             = excluded.base,
                  local_only       = excluded.local_only
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
                    1 if node.local_only else 0,
                ),
            )
            # v5.27.0: keep the FTS5 lexical index in lockstep.
            self.conn.execute("DELETE FROM nodes_fts WHERE id = ?", (node.id,))
            self.conn.execute(
                "INSERT INTO nodes_fts (id, name, description, body) VALUES (?, ?, ?, ?)",
                (
                    node.id,
                    node.name,
                    node.description or "",
                    (node.body or "")[:FTS_BODY_CAP],
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
                confidence=_edge_confidence(r),
            )
            for r in rows
        ]

    def list_nodes(
        self,
        *,
        type: str | None = None,
        project_key: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_base: bool = True,
    ) -> list[Node]:
        """List nodes with optional filters.

        When ``project_key`` is set, the result includes nodes whose
        project_key matches **plus** any BASE-flagged nodes (since BASE
        knowledge applies to every project). Pass ``include_base=False``
        to suppress that union (admin / debugging use only).

        v2.6.7: ``offset`` enables real SQL pagination (``LIMIT ?
        OFFSET ?``) so the UI fetches exactly one page instead of
        loading 10 000 rows and slicing in Python. Pair with
        ``count_nodes_total`` for the (uncapped) page count.
        """
        sql = "SELECT * FROM nodes"
        clauses, params = self._node_filter_sql(
            type=type, project_key=project_key, include_base=include_base
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_node(r) for r in rows]

    @staticmethod
    def _node_filter_sql(
        *, type: str | None, project_key: str | None, include_base: bool
    ) -> tuple[list[str], list[object]]:
        """Shared WHERE builder for list_nodes / count_nodes_total so
        the page list and its total can never drift out of sync."""
        clauses: list[str] = []
        params: list[object] = []
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        if project_key is not None:
            if include_base:
                clauses.append("(project_key = ? OR base = 1)")
                params.append(project_key)
            else:
                clauses.append("project_key = ?")
                params.append(project_key)
        return clauses, params

    def count_nodes_total(
        self,
        *,
        type: str | None = None,
        project_key: str | None = None,
        include_base: bool = True,
    ) -> int:
        """Scalar ``SELECT COUNT(*)`` honoring the SAME filters as
        ``list_nodes``. No LIMIT -> the real total, never capped at
        10 000 (the v2.6.7 fix for "Showing 1-25 of 10000")."""
        sql = "SELECT COUNT(*) AS n FROM nodes"
        clauses, params = self._node_filter_sql(
            type=type, project_key=project_key, include_base=include_base
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
        return int(row["n"]) if row else 0

    def list_project_keys(self) -> list[str]:
        """Distinct non-NULL ``project_key`` values, sorted. Feeds the
        nodes-browse project dropdown -- previously derived from a
        capped ``list_nodes(limit=10_000)`` scan that silently
        dropped projects beyond the first 10 000 rows."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT project_key FROM nodes "
                "WHERE project_key IS NOT NULL ORDER BY project_key"
            ).fetchall()
        return [r["project_key"] for r in rows]

    def delete_node(self, node_id: str) -> None:
        # Vec rows are not FK-linked (virtual table), so clean them up explicitly
        # before the cascade fires on chunk_meta.
        if self._vec_initialized:
            self.delete_chunks(node_id)
        with self._lock:
            self.conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            self.conn.execute("DELETE FROM nodes_fts WHERE id = ?", (node_id,))
            self.conn.commit()

    def bm25_search(self, query_text: str, k: int = 40) -> list[tuple[str, int]]:
        """Top-k lexical candidates via FTS5 BM25 (v5.27.0 exactness).

        Returns ``[(node_id, rank_position)]`` with 0-based positions in
        BM25 order. Query tokens are individually quoted so arbitrary user
        text can never break the MATCH grammar; empty/garbage -> []. This
        gives retrieval lexical RECALL -- a name-exact node that misses
        the vector top-40 can now still become a candidate."""
        tokens = list(
            dict.fromkeys(t.lower() for t in re.findall(r"[A-Za-z0-9_]{3,}", query_text or ""))
        )
        if not tokens:
            return []
        match = " OR ".join(f'"{t}"' for t in tokens)
        with self._lock:
            try:
                rows = self.conn.execute(
                    "SELECT id FROM nodes_fts WHERE nodes_fts MATCH ? "
                    "ORDER BY bm25(nodes_fts) LIMIT ?",
                    (match, k),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [(r["id"], i) for i, r in enumerate(rows)]

    def rebuild_fts(self) -> None:
        """Rebuild the FTS5 index from the nodes table (consistency backstop
        after bulk operations like a reindex sweep or source cascade)."""
        with self._lock:
            self.conn.execute("DELETE FROM nodes_fts")
            self.conn.execute(
                "INSERT INTO nodes_fts (id, name, description, body) "
                "SELECT id, name, coalesce(description, ''), "
                "substr(coalesce(body, ''), 1, ?) FROM nodes",
                (FTS_BODY_CAP,),
            )
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
        # SELECT * still work. Same defense for v5's local_only column.
        try:
            base_val = bool(row["base"])
        except (KeyError, IndexError):
            base_val = False
        try:
            local_only_val = bool(row["local_only"])
        except (KeyError, IndexError):
            local_only_val = False
        return Node(
            id=row["id"],
            type=row["type"],
            name=row["name"],
            description=row["description"],
            body=_strip_truncation_marker(row["body"]),
            source_path=row["source_path"],
            source_kind=row["source_kind"],
            project_key=row["project_key"],
            frontmatter_json=row["frontmatter_json"],
            hash=row["hash"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            base=base_val,
            local_only=local_only_val,
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
        confidence: float = 1.0,
    ) -> None:
        if relation not in EDGE_RELATIONS:
            raise ValueError(f"unknown edge relation: {relation!r}")
        if source not in EDGE_SOURCES:
            raise ValueError(f"unknown edge source: {source!r}")
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO edges (src_id, dst_id, relation, weight, created_at, source, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(src_id, dst_id, relation) DO UPDATE SET
                  weight = excluded.weight,
                  source = excluded.source,
                  confidence = excluded.confidence
                """,
                (src_id, dst_id, relation, weight, int(time.time()), source, confidence),
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
                confidence=_edge_confidence(r),
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

    # --- Nebula layout cache (v2.6.3) --------------------------------------

    def get_graph_layout(self, scope_key: str) -> tuple[str, str] | None:
        """Return ``(fingerprint, positions_json)`` for ``scope_key`` or
        ``None`` if no layout is cached. The caller compares the stored
        fingerprint against the live graph fingerprint to decide hit vs
        miss -- a row whose fingerprint no longer matches is a stale
        layout (the graph changed via reindex) and must be recomputed.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT fingerprint, positions FROM graph_layout WHERE scope_key = ?",
                (scope_key,),
            ).fetchone()
        if row is None:
            return None
        return (row["fingerprint"], row["positions"])

    def put_graph_layout(self, *, scope_key: str, fingerprint: str, positions_json: str) -> None:
        """Upsert the settled layout for ``scope_key``. One row per scope;
        a fresh fingerprint overwrites the prior layout in place."""
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO graph_layout
                  (scope_key, fingerprint, positions, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (scope_key, fingerprint, positions_json, int(time.time())),
            )
            self.conn.commit()

    # --- Audit queue (v5.22.0, Phase 4a) -----------------------------------

    def reconcile_audit_queue(
        self,
        findings: list[dict],
        detector_types: tuple[str, ...],
        *,
        now: int | None = None,
    ) -> dict[str, int]:
        """Upsert the proactive auditor's findings into ``audit_queue`` and
        scope-guarded auto-resolve. Read-only w.r.t. the node graph -- only
        the queue table changes (Phase 4a anti-goal: no node mutation).

        Lifecycle per fingerprint:

        - not present -> insert as ``open``
        - present (open / dismissed) -> bump ``last_seen``, refresh
          severity / description / node_ids / locus, KEEP status
          (``dismissed`` is sticky)
        - present + ``resolved`` + re-detected -> reopen to ``open``
        - an ``open`` row whose ``type`` is in ``detector_types`` but which
          the fresh ``findings`` no longer produce -> ``resolved``

        ``detector_types`` are the finding ``type`` values this audit is
        authoritative for, in the SAME form ``finding['type']`` carries
        (singular, e.g. ``"orphan_reference"``). Auto-resolve is guarded by
        this set so a type the audit did not run is never wrongly closed.

        Returns ``{"new", "reopened", "resolved", "unchanged"}`` counts.
        """
        ts = int(time.time()) if now is None else int(now)
        fresh: dict[str, dict] = {_finding_fingerprint(f): f for f in findings}
        counts = {"new": 0, "reopened": 0, "resolved": 0, "unchanged": 0}
        with self._lock:
            for fp, f in fresh.items():
                row = self.conn.execute(
                    "SELECT status FROM audit_queue WHERE fingerprint = ?", (fp,)
                ).fetchone()
                node_ids_json = json.dumps(list(f.get("node_ids", [])))
                locus = _finding_locus(f)
                severity = f.get("severity", "low")
                description = f.get("description", "")
                if row is None:
                    self.conn.execute(
                        """
                        INSERT INTO audit_queue
                          (fingerprint, type, severity, node_ids, description,
                           locus, status, first_seen, last_seen)
                        VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
                        """,
                        (
                            fp,
                            f.get("type", ""),
                            severity,
                            node_ids_json,
                            description,
                            locus,
                            ts,
                            ts,
                        ),
                    )
                    counts["new"] += 1
                elif row["status"] == "resolved":
                    self.conn.execute(
                        """
                        UPDATE audit_queue
                           SET status = 'open', severity = ?, description = ?,
                               node_ids = ?, locus = ?, last_seen = ?
                         WHERE fingerprint = ?
                        """,
                        (severity, description, node_ids_json, locus, ts, fp),
                    )
                    counts["reopened"] += 1
                else:
                    # open or dismissed -> refresh, keep status
                    self.conn.execute(
                        """
                        UPDATE audit_queue
                           SET severity = ?, description = ?, node_ids = ?,
                               locus = ?, last_seen = ?
                         WHERE fingerprint = ?
                        """,
                        (severity, description, node_ids_json, locus, ts, fp),
                    )
                    counts["unchanged"] += 1

            scope = tuple(dict.fromkeys(detector_types))  # de-dup, keep order
            if scope:
                placeholders = ",".join("?" * len(scope))
                open_rows = self.conn.execute(
                    "SELECT fingerprint FROM audit_queue "
                    f"WHERE status = 'open' AND type IN ({placeholders})",
                    scope,
                ).fetchall()
                for r in open_rows:
                    if r["fingerprint"] not in fresh:
                        self.conn.execute(
                            "UPDATE audit_queue SET status = 'resolved', "
                            "last_seen = ? WHERE fingerprint = ?",
                            (ts, r["fingerprint"]),
                        )
                        counts["resolved"] += 1
            self.conn.commit()
        return counts

    @staticmethod
    def _audit_queue_where(status: str | None) -> tuple[str, tuple]:
        """Shared WHERE builder so list + count never drift. ``None`` /
        ``"all"`` -> no filter."""
        if status is None or status == "all":
            return "", ()
        return "WHERE status = ?", (status,)

    def list_audit_queue(
        self, *, status: str | None = "open", limit: int = 25, offset: int = 0
    ) -> list[AuditFinding]:
        """Page of queued findings, severity-ranked (high -> candidate) then
        most-recently-seen first (``fingerprint`` tiebreak keeps pages
        stable). ``status=None`` / ``"all"`` lists every row."""
        where, params = self._audit_queue_where(status)
        sql = (
            "SELECT fingerprint, type, severity, node_ids, description, locus, "
            "status, first_seen, last_seen FROM audit_queue "
            f"{where} ORDER BY "
            "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 "
            "WHEN 'low' THEN 2 WHEN 'candidate' THEN 3 ELSE 4 END, "
            "last_seen DESC, fingerprint LIMIT ? OFFSET ?"
        )
        with self._lock:
            rows = self.conn.execute(sql, (*params, int(limit), int(offset))).fetchall()
        return [
            AuditFinding(
                fingerprint=r["fingerprint"],
                type=r["type"],
                severity=r["severity"],
                node_ids=json.loads(r["node_ids"]),
                description=r["description"],
                locus=r["locus"],
                status=r["status"],
                first_seen=r["first_seen"],
                last_seen=r["last_seen"],
            )
            for r in rows
        ]

    def count_audit_queue(self, status: str | None = None) -> int:
        """COUNT(*) of the queue, same WHERE as :meth:`list_audit_queue`."""
        where, params = self._audit_queue_where(status)
        with self._lock:
            row = self.conn.execute(
                f"SELECT COUNT(*) AS n FROM audit_queue {where}", params
            ).fetchone()
        return int(row["n"])

    def audit_queue_counts(self) -> dict[str, int]:
        """One aggregate pass -> ``{"open", "dismissed", "resolved"}`` (each
        defaulting to 0). Feeds the nav badge + the UI status chips."""
        out = dict.fromkeys(_AUDIT_STATUSES, 0)
        with self._lock:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) AS n FROM audit_queue GROUP BY status"
            ).fetchall()
        for r in rows:
            if r["status"] in out:
                out[r["status"]] = int(r["n"])
        return out

    def set_audit_finding_status(self, fingerprint: str, status: str) -> bool:
        """Flip one finding's status (the user's "ignore" / "restore"). This
        is queue metadata, NOT a node edit. Returns ``True`` if a row
        matched. Raises ``ValueError`` on an unknown status."""
        if status not in _AUDIT_STATUSES:
            raise ValueError(f"unknown audit status: {status!r}")
        with self._lock:
            cur = self.conn.execute(
                "UPDATE audit_queue SET status = ? WHERE fingerprint = ?",
                (status, fingerprint),
            )
            self.conn.commit()
        return cur.rowcount > 0

    def get_audit_finding(self, fingerprint: str) -> AuditFinding | None:
        """One queued finding by fingerprint, or ``None``. Used by the
        confirm-then-apply path (v5.23.0) to reconstruct the target node +
        dead-citation set from the persisted row."""
        with self._lock:
            row = self.conn.execute(
                "SELECT fingerprint, type, severity, node_ids, description, locus, "
                "status, first_seen, last_seen FROM audit_queue WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        if row is None:
            return None
        return AuditFinding(
            fingerprint=row["fingerprint"],
            type=row["type"],
            severity=row["severity"],
            node_ids=json.loads(row["node_ids"]),
            description=row["description"],
            locus=row["locus"],
            status=row["status"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
        )

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

    def recent_queries(self, limit: int = 50, *, offset: int = 0) -> list[Query]:
        # ts has 1-second resolution, so ties within the same second fall back
        # to rowid order (SQLite's monotonic insertion order).
        # v2.6.7: ``offset`` enables real SQL pagination for the audit
        # page (was: fetch 10 000 + slice in Python -> capped + slow).
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM queries ORDER BY ts DESC, rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
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

    def count_queries(self) -> int:
        """Scalar total of audit rows -- the uncapped page count for
        /audit-page (was: ``len(recent_queries(limit=10_000))``)."""
        with self._lock:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM queries").fetchone()
        return int(row["n"]) if row else 0

    def query_audit_stats(self) -> dict[str, object]:
        """One-pass audit summary for the /audit-page side cards.

        v2.6.7: the cards used to be derived from a 10 000-row
        load-all (``all_q``) the pagination rewrite removed. Numeric
        stats now come from a single SQL aggregate (json1's
        ``json_array_length`` sums hit counts without materialising
        rows). The tag histogram still scans the log, but only the
        short ``intent_tags`` column -- far lighter than rebuilding
        full Query objects -- so it stays correct + uncapped.
        Returns ``{total_queries, total_hits, first_ts, last_ts,
        top_tags}`` (top_tags = list[(tag, n)] desc, "none" kept;
        the route filters it).
        """
        with self._lock:
            agg = self.conn.execute(
                """
                SELECT COUNT(*) AS n,
                       COALESCE(SUM(json_array_length(retrieved_ids)), 0) AS hits,
                       COALESCE(MIN(ts), 0) AS first_ts,
                       COALESCE(MAX(ts), 0) AS last_ts
                FROM queries
                """
            ).fetchone()
            tag_rows = self.conn.execute(
                "SELECT intent_tags FROM queries WHERE intent_tags IS NOT NULL"
            ).fetchall()
        counter: dict[str, int] = {}
        for r in tag_rows:
            try:
                tags = json.loads(r["intent_tags"]) if r["intent_tags"] else []
            except (ValueError, TypeError):
                continue
            for t in tags:
                if t:
                    counter[t] = counter.get(t, 0) + 1
        top_tags = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        return {
            "total_queries": int(agg["n"]) if agg else 0,
            "total_hits": int(agg["hits"]) if agg else 0,
            "first_ts": int(agg["first_ts"]) if agg else 0,
            "last_ts": int(agg["last_ts"]) if agg else 0,
            "top_tags": top_tags,
        }

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

    # --- API keys (Phase 3 / Task 2.2) ------------------------------------

    @staticmethod
    def _hash_api_key(salt: str, raw_key: str) -> str:
        """The salted-SHA-256 the api_key.hash column stores.

        Salt + raw_key concatenated then hashed. Per-key salt means
        no rainbow table works across keys; small active-key set
        means the O(N) verify-iteration is acceptable for v0.1.
        """
        import hashlib

        return hashlib.sha256((salt + raw_key).encode("utf-8")).hexdigest()

    def create_api_key(self, name: str) -> tuple[str, str]:
        """Mint a new api_key. Returns ``(raw_key, key_id)``.

        The raw_key is NEVER persisted -- only its salted hash. The
        caller (the issuance CLI in :mod:`mnemo.cli`) prints the raw
        key ONCE to stdout and never sees it again.

        ``name`` is a free-form human label (e.g.
        ``"design-partner-A"``) used by ``list_api_keys`` + the
        billing report.
        """
        import secrets
        import time
        import uuid

        raw_key = secrets.token_urlsafe(32)
        salt = secrets.token_hex(16)
        h = self._hash_api_key(salt, raw_key)
        key_id = uuid.uuid4().hex
        now = int(time.time())
        with self._lock:
            self.conn.execute(
                "INSERT INTO api_key (id, hash, salt, name, created_at) VALUES (?, ?, ?, ?, ?)",
                (key_id, h, salt, name, now),
            )
            self.conn.commit()
        return raw_key, key_id

    def list_api_keys(self, *, include_revoked: bool = False) -> list[dict]:
        """List api_keys, newest-created first.

        Default excludes revoked keys; ``include_revoked=True`` shows
        everything (billing CLI uses this to attribute usage to keys
        that were active during a billing period but revoked since).
        Raw keys and hashes are intentionally NOT returned.
        """
        where = "" if include_revoked else "WHERE revoked_at IS NULL"
        with self._lock:
            rows = self.conn.execute(
                f"SELECT id, name, created_at, revoked_at FROM api_key "
                f"{where} ORDER BY created_at DESC, id ASC"
            ).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "created_at": r["created_at"],
                "revoked_at": r["revoked_at"],
            }
            for r in rows
        ]

    def revoke_api_key(self, key_id: str) -> bool:
        """Mark a key as revoked. Returns True if a row was updated
        (the key was active before this call); False if the key does
        not exist OR was already revoked. Idempotent."""
        import time

        now = int(time.time())
        with self._lock:
            cur = self.conn.execute(
                "UPDATE api_key SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, key_id),
            )
            self.conn.commit()
        return cur.rowcount > 0

    def verify_api_key(self, raw_key: str) -> str | None:
        """Validate a raw key against the active-key set.

        Returns the ``api_key.id`` on a match; None otherwise. O(N)
        in the number of ACTIVE keys (revoked keys are skipped at the
        SQL level via the WHERE clause). Acceptable for the v0.1
        hosted tier; v0.2 may add an indexed prefix-lookup hint.
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, hash, salt FROM api_key WHERE revoked_at IS NULL"
            ).fetchall()
        for r in rows:
            if r["salt"] is None:
                continue  # NULL-salt legacy row (none in the wild today)
            if self._hash_api_key(r["salt"], raw_key) == r["hash"]:
                return r["id"]
        return None

    def set_quota(
        self,
        api_key_id: str,
        *,
        max_queries: int,
        max_tokens: int,
        period: str = "monthly",
    ) -> None:
        """Upsert a quota for a key (Phase 3 / set-quota CLI follow-up).

        Wraps the SQLite step Phase 3a's docs/hosted/deploying.md used
        to require. Idempotent via ``ON CONFLICT (api_key_id, period)
        DO UPDATE`` -- calling set_quota a second time updates the
        limits in place rather than failing on the composite PK.

        Raises ``sqlite3.IntegrityError`` if ``api_key_id`` does not
        exist (the FK on api_key(id) catches it cleanly). The CLI
        wrapper turns that into a friendly "no key with id X" error.

        ``period`` defaults to ``"monthly"`` -- the only granularity
        Phase 3b's check_quota currently recognizes; v0.2 may add
        ``"daily"`` for finer-grained billing.
        """
        if max_queries < 0 or max_tokens < 0:
            raise ValueError(
                f"quota limits must be >= 0; got max_queries={max_queries}, max_tokens={max_tokens}"
            )
        with self._lock:
            self.conn.execute(
                """INSERT INTO quota (api_key_id, period, max_queries, max_tokens)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT (api_key_id, period) DO UPDATE SET
                       max_queries = excluded.max_queries,
                       max_tokens = excluded.max_tokens""",
                (api_key_id, period, max_queries, max_tokens),
            )
            self.conn.commit()

    # --- Usage metering (Phase 3 / Task 2.4) ------------------------------

    def record_usage(
        self,
        api_key_id: str,
        period: str,
        *,
        queries: int,
        tokens: int,
    ) -> None:
        """Atomic per-key per-period usage upsert (Phase 3 / Task 2.4).

        Called post-request by the /v1/query metering hook with the
        delta (default 1 query + the request's tokens_used). The
        composite primary key ``(api_key_id, period)`` makes this an
        idempotent UPSERT via SQLite's ``ON CONFLICT DO UPDATE`` --
        no read-modify-write race even under concurrent requests
        against the same key.

        ``period`` is the billing-period identifier (``YYYY-MM`` for
        monthly billing); the metering hook computes it from UTC so
        DST / timezone drift can't shift attribution.
        """
        import time

        now = int(time.time())
        with self._lock:
            self.conn.execute(
                """INSERT INTO usage_period
                       (api_key_id, period, queries, tokens, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (api_key_id, period) DO UPDATE SET
                       queries = queries + excluded.queries,
                       tokens = tokens + excluded.tokens,
                       updated_at = excluded.updated_at""",
                (api_key_id, period, queries, tokens, now),
            )
            self.conn.commit()

    # --- Quota enforcement (Phase 3 / Task 2.5) ---------------------------

    def check_quota(self, api_key_id: str, period: str) -> tuple[bool, str | None]:
        """Per-key per-period quota check.

        Returns ``(allowed, reason_if_blocked)``:

        - No ``quota`` row for the key -> ``(True, None)``. Hosted-tier
          operator can leave keys quota-less for "open billing"
          (track usage but never reject); the billing report's
          over_quota stays False because ``COALESCE(q.max_*, 0)`` is
          treated as "unset" by the >0 guards.
        - Quota set + ``queries >= max_queries`` or
          ``tokens >= max_tokens`` -> ``(False, "<reason>")``. The
          /v1/query handler turns this into a 429 with a
          ``Retry-After`` header pointing at the start of the next
          UTC month.

        Strict ``>=`` (not ``>``): the user gets EXACTLY ``max_queries``
        successful requests before the next one is rejected. Tokens
        may overshoot the limit by one request's worth (we don't
        know a request's tokens until it runs); that's acceptable
        slack for v0.1 and documented in
        ``docs/hosted/deploying.md``.
        """
        with self._lock:
            q_row = self.conn.execute(
                "SELECT max_queries, max_tokens FROM quota "
                "WHERE api_key_id = ? AND period = 'monthly'",
                (api_key_id,),
            ).fetchone()
            u_row = self.conn.execute(
                "SELECT queries, tokens FROM usage_period WHERE api_key_id = ? AND period = ?",
                (api_key_id, period),
            ).fetchone()
        if q_row is None:
            return True, None
        queries = u_row["queries"] if u_row else 0
        tokens = u_row["tokens"] if u_row else 0
        if queries >= q_row["max_queries"]:
            return False, "queries quota exceeded for period"
        if tokens >= q_row["max_tokens"]:
            return False, "tokens quota exceeded for period"
        return True, None

    # --- Billing report (Phase 3 / Task 2.6) ------------------------------

    def billing_report(self, period: str) -> list[dict]:
        """Per-key usage + quota + over-quota flag for a billing period.

        ``period`` is the usage-period identifier (``YYYY-MM`` for
        monthly). Joins:

        - ``api_key`` (all keys -- revoked included; we bill keys
          that were active during the period even if revoked since).
        - ``usage_period`` for that exact period (zero if no usage).
        - ``quota`` with granularity ``'monthly'`` (zero if unset --
          the over_quota flag is then False, no division-by-zero
          blow-up).

        Returns rows ordered by ``key_name`` so the CSV output is
        deterministic for diffing across periods.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT
                  ak.id            AS key_id,
                  ak.name          AS key_name,
                  COALESCE(up.queries, 0) AS queries,
                  COALESCE(up.tokens, 0)  AS tokens,
                  COALESCE(q.max_queries, 0) AS quota_queries,
                  COALESCE(q.max_tokens, 0)  AS quota_tokens
                FROM api_key ak
                LEFT JOIN usage_period up
                  ON up.api_key_id = ak.id AND up.period = ?
                LEFT JOIN quota q
                  ON q.api_key_id = ak.id AND q.period = 'monthly'
                ORDER BY ak.name ASC, ak.id ASC
                """,
                (period,),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            queries = int(r["queries"])
            tokens = int(r["tokens"])
            quota_queries = int(r["quota_queries"])
            quota_tokens = int(r["quota_tokens"])
            # over_quota = quota set AND any dimension exceeded.
            over_quota = (quota_queries > 0 and queries > quota_queries) or (
                quota_tokens > 0 and tokens > quota_tokens
            )
            out.append(
                {
                    "key_id": r["key_id"],
                    "key_name": r["key_name"],
                    "queries": queries,
                    "tokens": tokens,
                    "quota_queries": quota_queries,
                    "quota_tokens": quota_tokens,
                    "over_quota": bool(over_quota),
                }
            )
        return out

    # --- ROI summary (Phase 2 / Task 3.4) ---------------------------------

    # Estimated tokens saved per query vs naive RAG. Documented constant;
    # v0.2 plumbs per-query budget_tokens deltas through the audit log so
    # this becomes a real measurement.
    ROI_TOKENS_SAVED_PER_QUERY: int = 200

    def roi_summary(self, project_key: str | None = None) -> dict[str, float | int]:
        """Aggregate the v0.1 ROI fields from the existing telemetry.

        ``project_key`` is accepted for forward compatibility but is
        currently a no-op: the ``queries`` table has no project column
        (v0.2 of this endpoint plumbs it through).
        """
        _ = project_key  # forward-compat placeholder

        with self._lock:
            queries_total = self.conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
            thumbs_up = self.conn.execute(
                "SELECT COUNT(*) FROM feedback_event WHERE reason = 'thumbs_up'"
            ).fetchone()[0]
            thumbs_down = self.conn.execute(
                "SELECT COUNT(*) FROM feedback_event WHERE reason = 'thumbs_down'"
            ).fetchone()[0]

        explicit_total = thumbs_up + thumbs_down
        thumbs_up_ratio = (thumbs_up / explicit_total) if explicit_total > 0 else 0.0

        return {
            "queries_total": int(queries_total),
            # Proxy: each thumbs_up = "I didn't have to re-derive
            # this." v0.2 ties to the inferred-requery detector for
            # implicit avoidances too.
            "rederivations_avoided": int(thumbs_up),
            "tokens_saved_est": int(queries_total) * self.ROI_TOKENS_SAVED_PER_QUERY,
            "thumbs_up_ratio": float(thumbs_up_ratio),
            # No retune history table yet; v0.2 lands it.
            "auto_tune_iterations": 0,
        }

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

    # --- Chat (v3 phase 1) -------------------------------------------------

    @staticmethod
    def _now_ms() -> int:
        """Epoch milliseconds for chat timestamps. Like the ``workspaces``
        table (and unlike the second-resolution rest of the schema) the
        conversation rail must order rapid create / message cycles
        correctly within a single session or test run -- second
        precision collides and the newest-first sort goes
        non-deterministic. The UI consumes the value via
        ``new Date(ms)`` directly."""
        return int(time.time() * 1000)

    def create_conversation(
        self,
        *,
        name: str,
        provider: str,
        model: str,
        project_key: str | None = None,
        page_context: dict | None = None,
    ) -> Conversation:
        now = self._now_ms()
        conv = Conversation(
            id=uuid.uuid4().hex,
            name=name,
            project_key=project_key,
            page_context=page_context,
            provider=provider,
            model=model,
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO chat_conversations
                  (id, name, project_key, page_context, provider, model,
                   created_at, updated_at, archived_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conv.id,
                    conv.name,
                    conv.project_key,
                    json.dumps(page_context) if page_context is not None else None,
                    conv.provider,
                    conv.model,
                    conv.created_at,
                    conv.updated_at,
                    None,
                ),
            )
            self.conn.commit()
        return conv

    def get_conversation(self, conv_id: str) -> Conversation | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM chat_conversations WHERE id = ?", (conv_id,)
            ).fetchone()
        return self._row_to_conversation(row) if row else None

    def list_conversations(
        self,
        *,
        project_key: str | None = None,
        include_archived: bool = False,
    ) -> list[Conversation]:
        """Conversations sorted ``updated_at DESC``. Archived rows are
        hidden unless ``include_archived``. ``project_key`` filters to
        that project (None = every conversation)."""
        sql = "SELECT * FROM chat_conversations"
        clauses: list[str] = []
        params: list[object] = []
        if project_key is not None:
            clauses.append("project_key = ?")
            params.append(project_key)
        if not include_archived:
            clauses.append("archived_at IS NULL")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC, created_at DESC"
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_conversation(r) for r in rows]

    def archive_conversation(self, conv_id: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE chat_conversations SET archived_at = ? WHERE id = ?",
                (self._now_ms(), conv_id),
            )
            self.conn.commit()

    def rename_conversation(
        self,
        conv_id: str,
        *,
        name: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        page_context: dict | None = None,
    ) -> Conversation | None:
        """Patch the metadata fields present in the call (PATCH
        semantics) and bump ``updated_at``. Returns the refreshed row,
        or None if the conversation does not exist."""
        with self._lock:
            if (
                self.conn.execute(
                    "SELECT 1 FROM chat_conversations WHERE id = ?", (conv_id,)
                ).fetchone()
                is None
            ):
                return None
            sets: list[str] = ["updated_at = ?"]
            params: list[object] = [self._now_ms()]
            if name is not None:
                sets.append("name = ?")
                params.append(name)
            if provider is not None:
                sets.append("provider = ?")
                params.append(provider)
            if model is not None:
                sets.append("model = ?")
                params.append(model)
            if page_context is not None:
                sets.append("page_context = ?")
                params.append(json.dumps(page_context))
            params.append(conv_id)
            self.conn.execute(
                f"UPDATE chat_conversations SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            self.conn.commit()
        return self.get_conversation(conv_id)

    def purge_conversation(self, conv_id: str) -> None:
        """Hard-delete a conversation. ``chat_messages`` rows go with it
        via ``ON DELETE CASCADE`` (foreign_keys pragma is on)."""
        with self._lock:
            self.conn.execute("DELETE FROM chat_conversations WHERE id = ?", (conv_id,))
            self.conn.commit()

    def append_message(
        self,
        conv_id: str,
        *,
        role: str,
        content: dict,
        token_in: int | None = None,
        token_out: int | None = None,
        cache_read: int | None = None,
    ) -> ChatMessage:
        """Append a turn with the next monotonic ``seq`` and bump the
        owning conversation's ``updated_at`` so the rail re-sorts. The
        optional ``token_*`` args record the per-turn provider usage
        (v3.1); they stay NULL when the provider didn't surface usage."""
        if role not in CHAT_ROLES:
            raise ValueError(f"unknown chat role: {role!r}")
        now = self._now_ms()
        with self._lock:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS next FROM chat_messages "
                "WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()
            seq = int(row["next"])
            msg = ChatMessage(
                id=uuid.uuid4().hex,
                conversation_id=conv_id,
                seq=seq,
                role=role,
                content=content,
                created_at=now,
                token_in=token_in,
                token_out=token_out,
                cache_read=cache_read,
            )
            self.conn.execute(
                """
                INSERT INTO chat_messages
                  (id, conversation_id, seq, role, content_json, created_at,
                   token_in, token_out, cache_read)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.id,
                    conv_id,
                    seq,
                    role,
                    json.dumps(content),
                    now,
                    token_in,
                    token_out,
                    cache_read,
                ),
            )
            self.conn.execute(
                "UPDATE chat_conversations SET updated_at = ? WHERE id = ?",
                (now, conv_id),
            )
            self.conn.commit()
        return msg

    def list_messages(
        self,
        conv_id: str,
        *,
        before_seq: int | None = None,
        limit: int | None = None,
    ) -> list[ChatMessage]:
        """Messages for a conversation, always returned oldest-first.

        - ``list_messages(id)`` -> ALL turns (unchanged default; the agent
          loop + MCP rely on this).
        - ``limit=N`` -> the *last* N turns (the latest-window the /chat
          and dock surfaces open on).
        - ``before_seq=S, limit=N`` -> the N turns just before ``S`` (the
          lazy scroll-up page).

        Bounded queries page in SQL (``ORDER BY seq DESC LIMIT ?`` then
        reversed) per reference_mnemo_pagination.md -- never load-all."""
        params: list[object] = [conv_id]
        sql = "SELECT * FROM chat_messages WHERE conversation_id = ?"
        if before_seq is not None:
            sql += " AND seq < ?"
            params.append(before_seq)
        if limit is None:
            sql += " ORDER BY seq ASC"
            with self._lock:
                rows = self.conn.execute(sql, params).fetchall()
            return [self._row_to_message(r) for r in rows]
        # Bounded: take the newest ``limit`` rows in the window, then
        # flip to ascending so callers render without re-sorting.
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_message(r) for r in reversed(rows)]

    def count_messages(self, conv_id: str) -> int:
        """Scalar ``COUNT(*)`` (never a capped list scan -- see
        reference_mnemo_pagination.md). Drives ``total`` / ``has_more``."""
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM chat_messages WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()
        return int(row["n"])

    def bump_tokens(self, conv_id: str, *, delta: int) -> None:
        """Add ``delta`` to the conversation's running token counter
        (the header budget chip reads it)."""
        with self._lock:
            self.conn.execute(
                "UPDATE chat_conversations SET tokens_total = tokens_total + ? WHERE id = ?",
                (int(delta), conv_id),
            )
            self.conn.commit()

    def set_conversation_summary(self, conv_id: str, data: dict | None) -> None:
        """Persist the compaction state / running summary (v3.1 phase 3,
        the provider-agnostic fallback path). ``None`` clears it."""
        with self._lock:
            self.conn.execute(
                "UPDATE chat_conversations SET summary_json = ? WHERE id = ?",
                (json.dumps(data) if data is not None else None, conv_id),
            )
            self.conn.commit()

    # --- Bookmarks (v3.1) -------------------------------------------------

    def add_bookmark(
        self, conv_id: str, *, message_seq: int, label: str | None = None
    ) -> ChatBookmark:
        bm = ChatBookmark(
            id=uuid.uuid4().hex,
            conversation_id=conv_id,
            message_seq=message_seq,
            label=label,
            created_at=self._now_ms(),
        )
        with self._lock:
            self.conn.execute(
                "INSERT INTO chat_bookmarks "
                "(id, conversation_id, message_seq, label, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (bm.id, conv_id, message_seq, label, bm.created_at),
            )
            self.conn.commit()
        return bm

    def list_bookmarks(self, conv_id: str) -> list[ChatBookmark]:
        """Bookmarks for a conversation, ordered by target ``message_seq``
        so the jump-strip reads top-to-bottom like the thread."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM chat_bookmarks WHERE conversation_id = ? "
                "ORDER BY message_seq ASC, created_at ASC",
                (conv_id,),
            ).fetchall()
        return [
            ChatBookmark(
                id=r["id"],
                conversation_id=r["conversation_id"],
                message_seq=int(r["message_seq"]),
                label=r["label"],
                created_at=int(r["created_at"]),
            )
            for r in rows
        ]

    def delete_bookmark(self, bookmark_id: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM chat_bookmarks WHERE id = ?", (bookmark_id,))
            self.conn.commit()

    # --- Permissions (the always-allow allowlist) -------------------------

    def _permission_exists(self, project_key: str | None, tool_name: str) -> bool:
        if project_key is None:
            sql = "SELECT 1 FROM chat_permissions WHERE tool_name = ? AND project_key IS NULL"
            args: tuple[object, ...] = (tool_name,)
        else:
            sql = "SELECT 1 FROM chat_permissions WHERE tool_name = ? AND project_key = ?"
            args = (tool_name, project_key)
        return self.conn.execute(sql, args).fetchone() is not None

    def grant_permission(self, *, project_key: str | None, tool_name: str) -> None:
        """Persist an always-allow grant. Idempotent: a duplicate
        (project_key, tool_name) is a no-op (guards the NULL-in-PK
        quirk where SQLite would otherwise allow repeated NULL rows)."""
        with self._lock:
            if not self._permission_exists(project_key, tool_name):
                self.conn.execute(
                    "INSERT INTO chat_permissions "
                    "(project_key, tool_name, granted_at) VALUES (?, ?, ?)",
                    (project_key, tool_name, self._now_ms()),
                )
                self.conn.commit()

    def revoke_permission(self, *, project_key: str | None, tool_name: str) -> None:
        with self._lock:
            if project_key is None:
                self.conn.execute(
                    "DELETE FROM chat_permissions WHERE tool_name = ? AND project_key IS NULL",
                    (tool_name,),
                )
            else:
                self.conn.execute(
                    "DELETE FROM chat_permissions WHERE tool_name = ? AND project_key = ?",
                    (tool_name, project_key),
                )
            self.conn.commit()

    def is_permission_granted(self, *, project_key: str | None, tool_name: str) -> bool:
        """True if this tool is always-allowed for ``project_key`` --
        either via a project-scoped grant OR a global (NULL) grant."""
        with self._lock:
            if self._permission_exists(None, tool_name):
                return True
            if project_key is not None and self._permission_exists(project_key, tool_name):
                return True
        return False

    def list_permissions(self) -> list[ChatPermission]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM chat_permissions ORDER BY granted_at ASC, tool_name ASC"
            ).fetchall()
        return [
            ChatPermission(
                project_key=r["project_key"],
                tool_name=r["tool_name"],
                granted_at=int(r["granted_at"]),
            )
            for r in rows
        ]

    @staticmethod
    def _row_to_conversation(row: sqlite3.Row) -> Conversation:
        # summary_json / tokens_total are always present: _init_schema runs
        # _ensure_columns on every open and every read here is SELECT *.
        pc = row["page_context"]
        sj = row["summary_json"]
        tt = row["tokens_total"]
        return Conversation(
            id=row["id"],
            name=row["name"],
            project_key=row["project_key"],
            page_context=json.loads(pc) if pc else None,
            provider=row["provider"],
            model=row["model"],
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            archived_at=(int(row["archived_at"]) if row["archived_at"] is not None else None),
            summary_json=json.loads(sj) if sj else None,
            tokens_total=int(tt) if tt is not None else 0,
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> ChatMessage:
        def _opt_int(col: str) -> int | None:
            v = row[col]
            return int(v) if v is not None else None

        return ChatMessage(
            id=row["id"],
            conversation_id=row["conversation_id"],
            seq=int(row["seq"]),
            role=row["role"],
            content=json.loads(row["content_json"]),
            created_at=int(row["created_at"]),
            token_in=_opt_int("token_in"),
            token_out=_opt_int("token_out"),
            cache_read=_opt_int("cache_read"),
        )
