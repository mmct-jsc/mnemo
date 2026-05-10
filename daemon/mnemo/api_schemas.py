"""Pydantic models for the HTTP API.

Kept in their own module so the dataclasses in ``store``/``compress``/``retrieve``
stay free of HTTP-validation concerns. Each ``*Out`` model has a ``from_*``
constructor that maps from the corresponding internal dataclass.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from mnemo.compress import CompressedHit
from mnemo.ingest import ReindexReport
from mnemo.retrieve import RetrievalResult
from mnemo.store import ActiveProject, Node, Query, Source

# --- Nodes ----------------------------------------------------------------


class NodeOut(BaseModel):
    id: str
    type: str
    name: str
    description: str | None
    body: str
    source_path: str
    source_kind: str
    project_key: str | None
    hash: str
    created_at: int
    updated_at: int

    @classmethod
    def from_node(cls, n: Node) -> NodeOut:
        return cls(
            id=n.id,
            type=n.type,
            name=n.name,
            description=n.description,
            body=n.body,
            source_path=n.source_path,
            source_kind=n.source_kind,
            project_key=n.project_key,
            hash=n.hash,
            created_at=n.created_at,
            updated_at=n.updated_at,
        )


class NodeUpdateIn(BaseModel):
    body: str | None = None
    description: str | None = None
    type: str | None = None
    project_key: str | None = None


# --- Sources --------------------------------------------------------------


class SourceOut(BaseModel):
    path: str
    kind: str
    project_key: str | None
    last_indexed_at: int | None
    enabled: bool
    include: str | None = None
    exclude: str | None = None

    @classmethod
    def from_source(cls, s: Source) -> SourceOut:
        return cls(
            path=s.path,
            kind=s.kind,
            project_key=s.project_key,
            last_indexed_at=s.last_indexed_at,
            enabled=s.enabled,
            include=s.include,
            exclude=s.exclude,
        )


class SourceIn(BaseModel):
    path: str
    kind: str
    project_key: str | None = None
    enabled: bool = True
    include: str | None = None
    exclude: str | None = None


class SourceUpdateIn(BaseModel):
    """PATCH body. Identifies the source by ``path``; any other fields
    sent are applied. Send ``null`` to explicitly clear ``project_key``,
    ``include``, or ``exclude``."""

    path: str
    project_key: str | None = None
    enabled: bool | None = None
    include: str | None = None
    exclude: str | None = None


# --- Queries --------------------------------------------------------------


class QueryIn(BaseModel):
    prompt: str
    budget_tokens: int = Field(default=800, ge=1, le=10000)
    k: int = Field(default=20, ge=1, le=200)
    # v1.1 added ``project_key`` as the canonical name. ``active_project`` is
    # kept for one minor version of backward-compat with pre-1.1 clients.
    # If both are sent, ``project_key`` wins.
    project_key: str | None = None
    active_project: str | None = None


class HitOut(BaseModel):
    node_id: str
    type: str
    name: str
    description: str
    body: str | None
    score: float
    chunk_idx: int | None
    citation: str

    @classmethod
    def from_hit(cls, h: CompressedHit) -> HitOut:
        return cls(
            node_id=h.node_id,
            type=h.type,
            name=h.name,
            description=h.description,
            body=h.body,
            score=h.score,
            chunk_idx=h.chunk_idx,
            citation=h.citation,
        )


class QueryOut(BaseModel):
    hits: list[HitOut]
    intent_tags: list[str]
    tokens_used: int
    query_id: str

    @classmethod
    def from_result(cls, r: RetrievalResult) -> QueryOut:
        return cls(
            hits=[HitOut.from_hit(h) for h in r.hits],
            intent_tags=r.intent_tags,
            tokens_used=r.tokens_used,
            query_id=r.query_id,
        )


# --- Reindex --------------------------------------------------------------


class ReindexReportOut(BaseModel):
    added: int
    updated: int
    unchanged: int
    removed: int
    errors: list[tuple[str, str]]

    @classmethod
    def from_report(cls, r: ReindexReport) -> ReindexReportOut:
        return cls(
            added=r.added,
            updated=r.updated,
            unchanged=r.unchanged,
            removed=r.removed,
            errors=list(r.errors),
        )


# --- Audit ---------------------------------------------------------------


class QueryAuditOut(BaseModel):
    id: str
    prompt: str
    intent_tags: list[str]
    retrieved_ids: list[str]
    scores: dict[str, float]
    ts: int

    @classmethod
    def from_query(cls, q: Query) -> QueryAuditOut:
        return cls(
            id=q.id,
            prompt=q.prompt,
            intent_tags=q.intent_tags,
            retrieved_ids=q.retrieved_ids,
            scores=q.scores,
            ts=q.ts,
        )


# --- Health ---------------------------------------------------------------


class HealthOut(BaseModel):
    ok: bool
    version: str
    node_count: int
    source_count: int
    counts_by_type: dict[str, int]
    embedding_loaded: bool


# --- Project resolution + active project (v1.1) ---------------------------


class ProjectResolveIn(BaseModel):
    """Input for ``POST /v1/projects/resolve``: the path to derive a key for."""

    path: str


class ProjectResolveOut(BaseModel):
    """Output: the canonical project key for the supplied path."""

    project_key: str
    path: str


class ProjectActivateIn(BaseModel):
    """Input for ``POST /v1/projects/active``: a workspace path. The daemon
    resolves it to a canonical project key and persists it as the active
    project. Subsequent queries without an explicit ``project_key`` use this.
    """

    path: str


class ActiveProjectOut(BaseModel):
    """The currently-active project (or ``null`` body when none is set)."""

    project_key: str
    path: str
    since: int

    @classmethod
    def from_active(cls, a: ActiveProject) -> ActiveProjectOut:
        return cls(project_key=a.project_key, path=a.path, since=a.since)
