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
from mnemo.store import ActiveProject, FeedbackEvent, Node, Query, Source
from mnemo.workspaces import SourceOverride, Workspace

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
    base: bool = False

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
            base=n.base,
        )


class NodeUpdateIn(BaseModel):
    body: str | None = None
    description: str | None = None
    type: str | None = None
    project_key: str | None = None
    base: bool | None = None


class NodeCreateIn(BaseModel):
    """v1.2 phase 7 housekeeping: HTTP-driven memory creation.

    Lets non-filesystem clients (the VS Code "Add Note" command,
    future SaaS ingesters, scripts) put a memory entry into the store
    without first writing a markdown file under the project's memory
    dir. ``source_path`` defaults to a synthetic ``http://api/<uuid>``
    so the filesystem watcher doesn't try to reconcile it, and
    ``source_kind`` defaults to ``memory_dir`` to match hand-written
    memory entries.
    """

    type: str = Field(min_length=1)
    name: str = Field(min_length=1)
    body: str = Field(min_length=1)
    description: str | None = None
    project_key: str | None = None
    base: bool = False
    source_path: str | None = None
    source_kind: str | None = None


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


class SourcePreviewIn(BaseModel):
    """v2.0 phase 2 body for ``POST /v1/sources/preview``.

    Side-effect-free: the endpoint scans the path on disk and returns
    a suggested kind plus a per-extension breakdown so the user can
    decide whether to call ``POST /v1/sources`` next. ``force=True``
    suppresses the 50k safety-ceiling flag.
    """

    path: str
    force: bool = False


class SourcePreviewBreakdownOut(BaseModel):
    by_ext: dict[str, int]
    total_files: int
    md_with_frontmatter: int
    md_without_frontmatter: int
    has_git: bool


class SourcePreviewOut(BaseModel):
    """Response shape for ``POST /v1/sources/preview``.

    Mirrors :class:`mnemo.auto_router.PreviewResult` so the UI can
    render the suggestion without remapping. ``proposed_kind`` is
    ``None`` when no heuristic matches and the user must pick the
    kind explicitly.
    """

    path: str
    proposed_kind: str | None
    confidence: str  # "high" | "medium" | "low"
    breakdown: SourcePreviewBreakdownOut
    exceeds_safety_ceiling: bool


# --- Queries --------------------------------------------------------------


class QueryIn(BaseModel):
    prompt: str
    # v1.2.1: ``ge=20`` instead of ``ge=1``. Each compressed hit's
    # description line is ``[mnemo:<32-uuid>] [<type>] <desc>`` which
    # already eats ~12-15 tokens. Below ~20 the first hit's line
    # exceeds the budget and ``compress_to_budget`` returns the empty
    # list -- the caller sees a silent zero. The floor stops that:
    # clients asking for a tiny budget get a 422 instead of an empty
    # response.
    budget_tokens: int = Field(default=800, ge=20, le=10000)
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
    # v2.1.x: surface the originating source_path so the search-result
    # popover can pick a Prism language hint per hit (code nodes get
    # syntax highlighting, markdown nodes get marked rendering).
    source_path: str | None = None

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
            source_path=h.source_path,
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


class KnownProjectItem(BaseModel):
    project_key: str
    sample_path: str | None
    node_count: int
    source_count: int


# --- Feedback (v1.2 phase 1) ----------------------------------------------


class FeedbackIn(BaseModel):
    """Body for ``POST /v1/feedback``.

    The four `reason` values map to canonical `signal` magnitudes
    (+1 / -1 / +0.5 / -0.5). Callers can omit `signal` to accept the
    default; explicit values let the inferred-re-query detector use a
    different magnitude than the corresponding explicit thumb.

    Idempotent on ``(query_id, node_id, reason)`` -- re-POSTing the
    same triple updates the existing row rather than inserting a
    duplicate.
    """

    query_id: str
    node_id: str
    reason: str = Field(
        description="thumbs_up | thumbs_down | cite_copied | inferred_requery",
    )
    # Range matches the magnitudes we actually emit. The validator on
    # ``reason`` happens before this so a bad reason short-circuits.
    signal: float | None = Field(default=None, ge=-1.0, le=1.0)


class FeedbackOut(BaseModel):
    id: int
    query_id: str
    node_id: str
    signal: float
    reason: str
    created_at: int

    @classmethod
    def from_event(cls, e: FeedbackEvent) -> FeedbackOut:
        return cls(
            id=e.id,
            query_id=e.query_id,
            node_id=e.node_id,
            signal=e.signal,
            reason=e.reason,
            created_at=e.created_at,
        )


# --- Retune (v1.2 phase 6) -------------------------------------------------


class RetuneIn(BaseModel):
    """Body for ``POST /v1/retune``.

    Both fields are optional. ``min_queries`` overrides
    ``config.retune_min_queries`` for this run (useful for seeding new
    repos with sparse feedback). When omitted, the daemon uses the
    on-disk value.
    """

    min_queries: int | None = Field(default=None, ge=1)


class RetuneReportOut(BaseModel):
    """v1.2 phase 6 HTTP shape of the auto-tuner result. Pure data
    transfer object -- the dataclass equivalent lives in
    :mod:`mnemo.retune`. The endpoint never persists; the UI's Apply
    button does that via the existing ``PUT /v1/config``.
    """

    proposed: dict[str, float]
    current: dict[str, float]
    diff: dict[str, float]
    train_mrr_before: float
    train_mrr_after: float
    val_mrr_before: float
    val_mrr_after: float
    iterations: int
    train_size: int
    val_size: int
    elapsed_seconds: float
    log: list[str]

    @classmethod
    def from_report(cls, r: object) -> RetuneReportOut:
        # `r` is mnemo.retune.RetuneReport. Avoid the import cycle by
        # ducktyping the fields.
        return cls(
            proposed=r.proposed,  # type: ignore[attr-defined]
            current=r.current,  # type: ignore[attr-defined]
            diff=r.diff,  # type: ignore[attr-defined]
            train_mrr_before=r.train_mrr_before,  # type: ignore[attr-defined]
            train_mrr_after=r.train_mrr_after,  # type: ignore[attr-defined]
            val_mrr_before=r.val_mrr_before,  # type: ignore[attr-defined]
            val_mrr_after=r.val_mrr_after,  # type: ignore[attr-defined]
            iterations=r.iterations,  # type: ignore[attr-defined]
            train_size=r.train_size,  # type: ignore[attr-defined]
            val_size=r.val_size,  # type: ignore[attr-defined]
            elapsed_seconds=r.elapsed_seconds,  # type: ignore[attr-defined]
            log=r.log,  # type: ignore[attr-defined]
        )


class KnownProjectsOut(BaseModel):
    """Distinct project keys + their representative paths, gathered from
    sources and nodes. Used by the UI to populate dropdowns."""

    items: list[KnownProjectItem]


class FsSuggestOut(BaseModel):
    """Filesystem directory suggestions for the path autocomplete in the UI.

    Returned candidates are absolute paths to directories that exist on the
    daemon's local machine. The daemon is bound to 127.0.0.1 so the
    listener is the same user, but we still cap the response size and
    reject paths that resolve outside reasonable roots (no expansion of
    ``..`` past the user's home, no following symlinks).
    """

    candidates: list[str]


# --- v2.6 phase 5: workspaces + source overrides + propose -----------------


class WorkspaceOut(BaseModel):
    """One workspace row, serialized for the HTTP API.

    Time columns are epoch milliseconds (matching the schema); the UI
    consumes them via ``new Date(ms)`` directly.
    """

    id: str
    name: str
    project_keys: list[str]
    filter_prefs: dict | None = None
    page_state: dict | None = None
    created_at: int
    updated_at: int
    last_activated_at: int | None = None

    @classmethod
    def from_workspace(cls, w: Workspace) -> WorkspaceOut:
        return cls(
            id=w.id,
            name=w.name,
            project_keys=list(w.project_keys),
            filter_prefs=w.filter_prefs,
            page_state=w.page_state,
            created_at=w.created_at,
            updated_at=w.updated_at,
            last_activated_at=w.last_activated_at,
        )


class WorkspaceCreateIn(BaseModel):
    name: str = Field(min_length=1)
    project_keys: list[str] = Field(default_factory=list)
    filter_prefs: dict | None = None
    page_state: dict | None = None


class WorkspaceUpdateIn(BaseModel):
    """PATCH body. Any unset field is left alone."""

    name: str | None = None
    project_keys: list[str] | None = None
    filter_prefs: dict | None = None
    page_state: dict | None = None


class ActiveWorkspaceOut(BaseModel):
    """``GET /v1/workspaces/active`` body. ``active`` is None when no
    workspace is active (BASE-only UI mode)."""

    active: WorkspaceOut | None


class ActivateWorkspaceOut(BaseModel):
    """``POST /v1/workspaces/<id>/activate`` body on 200.

    Carries the updated workspace plus the total node count + soft-cap
    flag so the UI can render the yellow "large workspace" chip without
    re-fetching.
    """

    workspace: WorkspaceOut
    total_nodes: int
    soft_cap_exceeded: bool = False


class SourceOverrideOut(BaseModel):
    source_path: str
    decision: str
    reason: str | None = None
    decided_at: int

    @classmethod
    def from_override(cls, ov: SourceOverride) -> SourceOverrideOut:
        return cls(
            source_path=ov.source_path,
            decision=ov.decision,
            reason=ov.reason,
            decided_at=ov.decided_at,
        )


class SourceOverrideItemIn(BaseModel):
    source_path: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    reason: str | None = None


class SourceOverrideBatchIn(BaseModel):
    """Body for ``POST /v1/source_overrides``. Items applied in order."""

    items: list[SourceOverrideItemIn]


class SourceProposalOut(BaseModel):
    kind: str
    include_pattern: str
    include_count: int
    est_nodes: int
    sample: list[str]


class SourceProposeIn(BaseModel):
    path: str = Field(min_length=1)


class SourceProposeOut(BaseModel):
    """``POST /v1/sources/propose`` body. Dual-source proposal + gitignore
    + warnings, ready for the add-source UI."""

    path: str
    proposals: list[SourceProposalOut]
    gitignore_excludes: list[str]
    gitignore_files_found: list[str]
    warnings: list[dict]


class ReindexReportSectionsOut(BaseModel):
    """``GET /v1/reindex/report`` body. Most recent report; 404 if no
    reindex has run yet this daemon session."""

    auto_skipped: list[dict]
    malformed: list[dict]
    suspicious: list[dict]
    indexed_count: int
    duration_ms: int
    finished_at: int  # epoch ms when the report event was emitted
