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
from mnemo.store import (
    ActiveProject,
    AuditFinding,
    ChatBookmark,
    ChatMessage,
    Conversation,
    FeedbackEvent,
    Node,
    Query,
    Source,
)
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
    # v5.26.0: the caller's working directory. When no explicit key is
    # given, the server derives the project from it and scopes the query
    # IF that project is indexed (see retrieve.resolve_auto_scope). Sent
    # by the UserPromptSubmit hook so IDE queries stop leaking
    # cross-project nodes.
    cwd: str | None = None
    # v6.1.0 governance: optional context for surfacing applicable rules.
    # ``file_paths`` (buffers being edited) drive glob triggers; ``tool_name``
    # / ``tool_arg`` drive tool triggers (used by the PreToolUse gate). All
    # default None -> byte-stable for existing callers.
    file_paths: list[str] | None = None
    tool_name: str | None = None
    tool_arg: str | None = None


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


class RuleOut(BaseModel):
    """A governance rule that BINDS for the request context (v6.1.0)."""

    node_id: str
    rule_id: str
    modality: str  # MUST | MUST_NOT | SHOULD
    enforcement: str  # inform | warn | require-ack | block
    text: str
    citation: str


class QueryOut(BaseModel):
    hits: list[HitOut]
    intent_tags: list[str]
    tokens_used: int
    query_id: str
    # v6.1.0 governance: binding rules for this context, surfaced separately
    # from the ranked hits (they bypass the injection budget). Default [] ->
    # byte-stable for existing consumers.
    rules: list[RuleOut] = []

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


# --- ROI summary (Phase 2 / Task 3.4) -------------------------------------


class RoiSummaryOut(BaseModel):
    """Aggregated ROI telemetry for the dashboard card + the
    open-benchmark case studies.

    All five fields are non-negative numerics so a fresh install
    (empty DB) returns a valid response the dashboard can render
    without special-casing.

    Field semantics (v0.1; v0.2 documented as TODO inline):

    - ``queries_total`` -- COUNT(*) of the ``queries`` audit log.
    - ``rederivations_avoided`` -- proxy: number of explicit
      thumbs_up signals (user said "this retrieval was useful, I
      didn't have to re-derive"). Refinement in v0.2 ties to the
      inferred-requery detector to capture implicit avoidances.
    - ``tokens_saved_est`` -- queries_total * 200 (rough per-query
      saving vs naive RAG). Documented constant; v0.2 plumbs the
      actual per-query budget_tokens deltas.
    - ``thumbs_up_ratio`` -- thumbs_up / (thumbs_up + thumbs_down).
      0.0 when no explicit feedback exists (no division-by-zero
      blow-up; dashboard renders as "No feedback yet").
    - ``auto_tune_iterations`` -- count of completed retune passes.
      v0.1: always 0 (no history table yet); v0.2 lands the
      ``retune_history`` table.
    """

    queries_total: int = Field(ge=0)
    rederivations_avoided: int = Field(ge=0)
    tokens_saved_est: int = Field(ge=0)
    thumbs_up_ratio: float = Field(ge=0.0, le=1.0)
    auto_tune_iterations: int = Field(ge=0)


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


# --- Chat (v3) ------------------------------------------------------------


class ChatCreateIn(BaseModel):
    """``POST /v1/chat`` body. Everything optional -- provider/model
    fall back to the design-S4 defaults when omitted."""

    name: str | None = None
    project_key: str | None = None
    page_context: dict | None = None
    provider: str | None = None
    model: str | None = None


class ChatPatchIn(BaseModel):
    """``PATCH /v1/chat/<id>`` -- rename / change provider or model."""

    name: str | None = None
    provider: str | None = None
    model: str | None = None
    page_context: dict | None = None


class MessageCreateIn(BaseModel):
    """``POST /v1/chat/<id>/message`` body.

    v5 phase 3: ``use_skill`` lets the dock prime the agent with a
    named skill's guidance BEFORE the model sees the user text. Used
    by the prompt-architect dock surface (set to
    ``"mnemo-prompt-architect"``); legacy chat callers omit it.
    """

    text: str = Field(min_length=1)
    use_skill: str | None = None


class ChatMessageOut(BaseModel):
    id: str
    conversation_id: str
    seq: int
    role: str
    content: dict
    created_at: int
    # v3.1: per-turn provider usage (NULL on legacy/unmeasured rows)
    # + whether the user has bookmarked this turn.
    token_in: int | None = None
    token_out: int | None = None
    cache_read: int | None = None
    bookmarked: bool = False

    @classmethod
    def from_message(cls, m: ChatMessage, *, bookmarked: bool = False) -> ChatMessageOut:
        return cls(
            id=m.id,
            conversation_id=m.conversation_id,
            seq=m.seq,
            role=m.role,
            content=m.content,
            created_at=m.created_at,
            token_in=m.token_in,
            token_out=m.token_out,
            cache_read=m.cache_read,
            bookmarked=bookmarked,
        )


class ChatBookmarkOut(BaseModel):
    id: str
    conversation_id: str
    message_seq: int
    label: str | None
    created_at: int

    @classmethod
    def from_bookmark(cls, b: ChatBookmark) -> ChatBookmarkOut:
        return cls(
            id=b.id,
            conversation_id=b.conversation_id,
            message_seq=b.message_seq,
            label=b.label,
            created_at=b.created_at,
        )


class ChatBookmarkIn(BaseModel):
    message_seq: int = Field(ge=0)
    label: str | None = None


class ConversationOut(BaseModel):
    id: str
    name: str
    project_key: str | None
    page_context: dict | None
    provider: str
    model: str
    created_at: int
    updated_at: int
    archived_at: int | None
    tokens_total: int = 0  # v3.1: running token counter (budget chip)

    @classmethod
    def from_conversation(cls, c: Conversation) -> ConversationOut:
        return cls(
            id=c.id,
            name=c.name,
            project_key=c.project_key,
            page_context=c.page_context,
            provider=c.provider,
            model=c.model,
            created_at=c.created_at,
            updated_at=c.updated_at,
            archived_at=c.archived_at,
            tokens_total=c.tokens_total,
        )


class ConversationDetailOut(ConversationOut):
    """``GET /v1/chat/<id>`` -- metadata + the LATEST window of messages
    (v3.1: paginated; older turns load via /messages). ``total`` /
    ``has_more`` describe the full log so the UI can lazy scroll-up."""

    messages: list[ChatMessageOut]
    total: int = 0
    has_more: bool = False

    @classmethod
    def from_conversation_and_messages(
        cls,
        c: Conversation,
        messages: list[ChatMessage],
        *,
        total: int | None = None,
        has_more: bool = False,
        bookmarked_seqs: set[int] | None = None,
    ) -> ConversationDetailOut:
        base = ConversationOut.from_conversation(c).model_dump()
        bset = bookmarked_seqs or set()
        return cls(
            **base,
            total=len(messages) if total is None else total,
            has_more=has_more,
            messages=[ChatMessageOut.from_message(m, bookmarked=m.seq in bset) for m in messages],
        )


class MessagesPageOut(BaseModel):
    """``GET /v1/chat/<id>/messages?before=&limit=`` -- one older page,
    oldest-first, plus the full-log counters."""

    messages: list[ChatMessageOut]
    total: int
    has_more: bool

    @classmethod
    def build(
        cls,
        messages: list[ChatMessage],
        *,
        total: int,
        has_more: bool,
        bookmarked_seqs: set[int] | None = None,
    ) -> MessagesPageOut:
        bset = bookmarked_seqs or set()
        return cls(
            total=total,
            has_more=has_more,
            messages=[ChatMessageOut.from_message(m, bookmarked=m.seq in bset) for m in messages],
        )


class MessageAcceptedOut(BaseModel):
    """``POST /v1/chat/<id>/message`` response -- where to stream."""

    stream_url: str
    conversation_id: str


class ChatPermitIn(BaseModel):
    """``POST /v1/chat/<id>/permit`` -- grant or deny a pending
    permission request (design S4)."""

    permission_id: str
    decision: str = Field(pattern="^(allow_once|allow_always|deny)$")


class ProvidersPatchIn(BaseModel):
    """``POST /v1/settings/providers``. ``providers[name].key`` (if
    present) goes to the keychain and is dropped before persisting;
    ``model`` persists in settings.json."""

    default_provider: str | None = None
    providers: dict | None = None


class CompanionPatchIn(BaseModel):
    """``POST /v1/settings/companion`` -- Mnem personality + dock."""

    name: str | None = None
    tone: str | None = None
    dock_state: str | None = None
    proactive: bool | None = None
    proactive_pages: list[str] | None = None
    proactive_frequency: str | None = None
    chat_history_retention_days: int | None = None


class ProviderOut(BaseModel):
    """``GET /v1/providers`` -- the C2 (v4.1) registry, exposed for the
    C4 (v4.2) settings UI. No key material; the registry only knows
    capabilities, never secrets."""

    name: str
    display_name: str
    env_var: str | None
    requires_key: bool
    default_model: str
    known_models: list[str]
    supports_compaction_models: list[str]


class SettingsOut(BaseModel):
    """``GET /v1/settings`` -- never includes key material; per-provider
    reports ``has_key`` + ``model`` only."""

    default_provider: str
    providers: dict
    companion: dict
    chat_history_retention_days: int | None


# --- Knowledge auditor (v5.12.0) -----------------------------------------


class AnalyzeIn(BaseModel):
    """``POST /v1/analyze`` body. All fields optional.

    ``types`` filter: any subset of
    ``{"stale", "duplicates", "orphan_references", "contradictions",
    "semantic_orphans"}``. Omit / None for "run all detectors".
    Unknown values are silently ignored so the daemon can add
    detectors without breaking pre-existing callers.

    ``propose_actions`` (v5.15.0): opt-in refactor_actions
    enrichment. ``True`` proposes one concrete action per high/medium
    finding (requires the proposer env opt-in + API key); ``False``
    disables it; ``None`` (default) enables only when the env-derived
    proposer exists. Backward-compatible: pre-existing callers omit
    it and get the byte-stable deterministic response.
    """

    types: list[str] | None = None
    project_key: str | None = None  # reserved for future scoping
    propose_actions: bool | None = None  # v5.15.0 Phase 2c
    # v5.16.0 Phase 3: optional domain lens. None = agnostic suite;
    # a known lens (e.g. "code") replaces it with domain-specific
    # detectors (e.g. dead_code). Unknown lens runs nothing.
    lens: str | None = None


class AnalyzeFinding(BaseModel):
    """One row in the audit report."""

    type: str
    node_ids: list[str]
    description: str
    severity: str
    missing_targets: list[str] | None = None
    # v5.14.0: the concept a semantic_orphan finding flags as
    # referenced-but-undefined. Declared here so it survives HTTP
    # serialization (pydantic strips undeclared fields).
    concept: str | None = None
    # v5.15.0: an LLM-proposed refactor action for this finding.
    # Present only when the opt-in enrichment ran AND this finding
    # was eligible (high/medium) + within the cap.
    action: dict | None = None
    # v5.16.0: the symbol name a dead_code (code lens) finding flags.
    # Declared so it survives HTTP serialization (parity with the MCP
    # raw-dict path).
    symbol: str | None = None


class NodeLabel(BaseModel):
    """v5.21.0: per-node display info for the /analyze table so a finding
    shows WHERE the problem is (name + file) without a click. HTTP-
    response convenience only -- NOT emitted on the MCP raw-dict path."""

    name: str
    type: str
    source_path: str | None = None


class AnalyzeOut(BaseModel):
    """``POST /v1/analyze`` response envelope."""

    ran_at: str
    node_count_scanned: int
    findings: list[AnalyzeFinding]
    summary: dict[str, int]
    # v5.21.0: id -> {name, type, source_path} for every cited node so
    # the UI renders the name + path inline. Additive; defaults empty for
    # pre-existing callers + the MCP path.
    node_labels: dict[str, NodeLabel] = {}


# --- Proactive audit queue (v5.22.0, Phase 4a) ---------------------------


class QueueFinding(BaseModel):
    """One persisted, status-tracked row of the proactive audit queue.

    Mirrors :class:`mnemo.store.AuditFinding`. ``locus`` is the problem
    locus the fingerprint keys on (joined missing targets / concept /
    symbol) or ``None``. ``status`` is one of open / dismissed / resolved.
    """

    fingerprint: str
    type: str
    severity: str
    node_ids: list[str]
    description: str
    locus: str | None = None
    status: str
    first_seen: int
    last_seen: int

    @classmethod
    def from_finding(cls, f: AuditFinding) -> QueueFinding:
        return cls(
            fingerprint=f.fingerprint,
            type=f.type,
            severity=f.severity,
            node_ids=list(f.node_ids),
            description=f.description,
            locus=f.locus,
            status=f.status,
            first_seen=f.first_seen,
            last_seen=f.last_seen,
        )


class AnalyzeQueueOut(BaseModel):
    """``GET /v1/analyze/queue`` envelope. Read-only.

    ``counts`` is the full-queue ``{open, dismissed, resolved}`` tally
    (feeds the nav badge); ``total`` is the count for the requested status
    filter (drives pagination). ``node_labels`` resolves cited ids to
    name / type / source_path so the UI renders WHERE inline."""

    findings: list[QueueFinding]
    total: int
    counts: dict[str, int]
    node_labels: dict[str, NodeLabel] = {}


class QueueStatusIn(BaseModel):
    """``POST /v1/analyze/queue/{fingerprint}/status`` body. The flip is
    queue metadata (the user's ignore / restore), NOT a node edit."""

    status: str = Field(pattern="^(open|dismissed|resolved)$")


# --- Confirm-then-apply (v5.23.0, Phase 4b -- the first node mutation) ----


class ApplyPreviewOut(BaseModel):
    """``POST /v1/analyze/queue/{fingerprint}/apply/preview`` -- the
    READ-ONLY preview of the deterministic orphan-fix. ``node_hash`` is the
    confirm token for the apply handshake; ``applyable=false`` (+``reason``)
    when the finding can't be auto-fixed (placeholders / already fixed /
    unsupported type)."""

    fingerprint: str
    node_id: str | None
    node_name: str | None = None
    before: str
    after: str
    removed: list[str]
    applyable: bool
    reason: str | None = None
    node_hash: str
    finding_type: str


class ApplyConfirmIn(BaseModel):
    """``POST /v1/analyze/queue/{fingerprint}/apply`` body -- the
    ``node_hash`` the caller got from the preview. The apply refuses (409)
    if it no longer matches the live node body."""

    node_hash: str


class ApplyResultOut(BaseModel):
    """``POST .../apply`` 200 body. The finding is now ``resolved``."""

    applied: bool
    fingerprint: str
    node_id: str | None
    removed: list[str]
    status: str
