"""FastAPI HTTP server for mnemo.

The server holds a single ``Store`` and ``Embedder`` for the lifetime of the
process. Both are constructed in ``lifespan`` so requests never pay setup cost.

Bind to ``127.0.0.1`` only. Never listen on ``0.0.0.0``.

v1.1 introduced URL versioning: every public endpoint lives under ``/v1/``.
v1.1 also kept a 308 bridge from legacy un-versioned paths
(``/health`` -> ``/v1/health``, etc.) for a single minor version of
backward compat. v1.2 phase 7 housekeeping **removed** that bridge --
legacy paths now return 404. The ``X-Mnemo-Api-Version`` header was
the standing signal telling adapters to migrate.

The OpenAPI schema is filtered to v1-only paths and exposed at both
``/openapi.json`` (FastAPI default, used by the built-in /docs UI) and
``/v1/openapi.json`` (canonical, intentional URL adapters consume).
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

from mnemo import (
    __version__,
    auto_router,
    chat,
    config,
    ingest,
    keys,
    paths,
    providers,
    retrieve,
    workspaces,
)
from mnemo.api_schemas import (
    ActivateWorkspaceOut,
    ActiveProjectOut,
    ActiveWorkspaceOut,
    ChatBookmarkIn,
    ChatBookmarkOut,
    ChatCreateIn,
    ChatPatchIn,
    ChatPermitIn,
    CompanionPatchIn,
    ConversationDetailOut,
    ConversationOut,
    FeedbackIn,
    FeedbackOut,
    FsSuggestOut,
    HealthOut,
    KnownProjectItem,
    KnownProjectsOut,
    MessageAcceptedOut,
    MessageCreateIn,
    MessagesPageOut,
    NodeCreateIn,
    NodeOut,
    NodeUpdateIn,
    ProjectActivateIn,
    ProjectResolveIn,
    ProjectResolveOut,
    ProviderOut,
    ProvidersPatchIn,
    QueryAuditOut,
    QueryIn,
    QueryOut,
    ReindexReportOut,
    ReindexReportSectionsOut,
    RetuneIn,
    RetuneReportOut,
    SettingsOut,
    SourceIn,
    SourceOut,
    SourceOverrideBatchIn,
    SourceOverrideOut,
    SourcePreviewBreakdownOut,
    SourcePreviewIn,
    SourcePreviewOut,
    SourceProposalOut,
    SourceProposeIn,
    SourceProposeOut,
    SourceUpdateIn,
    WorkspaceCreateIn,
    WorkspaceOut,
    WorkspaceUpdateIn,
)
from mnemo.embed import Embedder
from mnemo.store import FEEDBACK_REASONS, Node, Store, signal_for_reason

log = logging.getLogger(__name__)

# v1.2 phase 7 removed the legacy 308 redirect bridge. The
# ``_LegacyRedirectMiddleware`` previously living here translated
# un-versioned paths like ``/health`` -> ``/v1/health``. It was a
# one-version-only bridge and the ``X-Mnemo-Api-Version`` header has
# been telling adapters to migrate throughout the v1.1 cycle.


def _language_for_path(path) -> str:  # noqa: ANN001 -- accepts Path-like
    """Map a file extension to a syntax-hint string for the UI's code
    viewer. Returns ``"text"`` for unknown extensions so the renderer
    can fall back to a plain ``<pre>`` block."""
    ext = str(path).lower().rsplit(".", 1)[-1] if "." in str(path) else ""
    return {
        "py": "python",
        "pyi": "python",
        "js": "javascript",
        "mjs": "javascript",
        "cjs": "javascript",
        "jsx": "javascript",
        "ts": "typescript",
        "tsx": "tsx",
        "go": "go",
        "rs": "rust",
        "java": "java",
        "rb": "ruby",
        "php": "php",
        "cs": "csharp",
        "kt": "kotlin",
        "swift": "swift",
        "c": "c",
        "h": "c",
        "cpp": "cpp",
        "hpp": "cpp",
        "json": "json",
        "yaml": "yaml",
        "yml": "yaml",
        "toml": "toml",
        "md": "markdown",
        "sh": "bash",
        "bash": "bash",
    }.get(ext, "text")


class _ApiVersionHeaderMiddleware(BaseHTTPMiddleware):
    """Stamp ``X-Mnemo-Api-Version`` on every response.

    Lets adapters sanity-check they're talking to a daemon they understand.
    Cheap and read-only.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Mnemo-Api-Version"] = "1"
        return response


@dataclass
class AppState:
    store: Store | None = None
    embedder: Embedder | None = None
    owns_store: bool = False
    # v1.1.1: serialize concurrent reindex requests. Without this, two
    # POST /v1/reindex calls (e.g. from a stale UI tab and a fresh tab)
    # both run, racing on SQLite (serialized internally but emitting
    # duplicate effects + a conflated report). The lock is per-app so
    # tests that build their own FastAPI instance via create_app() get
    # an independent lock.
    reindex_lock: threading.Lock = field(default_factory=threading.Lock)
    reindex_started_at: int | None = None
    # v2.6 phase 3+6: the most recent ReindexReportSectionsOut payload
    # cached in memory so GET /v1/reindex/report returns the three-
    # section bucket from the last run. None until the first reindex
    # completes this daemon session. Replaced on every run.
    last_reindex_report: dict | None = None
    # v2.6 phase 5: subscriber queues for the SSE broadcast channel on
    # /v1/events. Each connected client gets a Queue; broadcast_event
    # fan-outs to every queue. Slow consumers drop frames (Full).
    event_subscribers: list[queue.Queue] = field(default_factory=list)
    event_subscribers_lock: threading.Lock = field(default_factory=threading.Lock)
    # v3 phase 3: agentic chat. One in-flight AgentLoop per conversation
    # -- the per-conv lock is acquired by GET /v1/chat/<id>/events for
    # the duration of the run; POST .../message 409s if it's held.
    # ``chat_pending`` stashes the user's text between POST .../message
    # and the SSE GET that actually runs the loop. ``chat_cancel`` is a
    # per-conv Event the loop checks between iterations.
    # ``chat_provider_factory`` defaults to providers.get_provider;
    # tests inject a scripted provider here (same pattern as the
    # injectable store/embedder).
    chat_locks: dict[str, threading.Lock] = field(default_factory=dict)
    chat_pending: dict[str, str] = field(default_factory=dict)
    chat_cancel: dict[str, threading.Event] = field(default_factory=dict)
    chat_provider_factory: object | None = None
    # v3 phase 4: permission pause/resume. The /events permission_cb
    # blocks on chat_permit_event[conv] after the loop yields a
    # permission_request frame; POST .../permit records the decision
    # here and signals the Event so the loop resumes the same iteration.
    chat_permit: dict[str, dict] = field(default_factory=dict)
    chat_permit_event: dict[str, threading.Event] = field(default_factory=dict)


# v2.6 phase 5: workspace activation caps.
# - soft: warn the UI (yellow chip) but allow activation.
# - hard: refuse activation with 409 WorkspaceTooLarge.
# Tests override via query params; production defaults match the v2.6
# Settings.workspaces section.
DEFAULT_SOFT_CAP_NODES = 75_000
DEFAULT_HARD_CAP_NODES = 200_000

# v3.1: chat history is paginated -- GET /chat/<id> returns the latest
# window, /chat/<id>/messages pages older turns (the model context is
# bounded separately by compaction).
CHAT_PAGE_DEFAULT = 30


def _broadcast_event(state: AppState, name: str, payload: dict) -> None:
    """Push an SSE frame to every /v1/events subscriber.

    Drops the frame for slow consumers (queue full). Frame format
    mirrors the reindex_events SSE shape: ``event: <name>\\ndata: <json>``.
    """
    with state.event_subscribers_lock:
        subscribers = list(state.event_subscribers)
    for q in subscribers:
        try:
            q.put_nowait((name, payload))
        except queue.Full:
            log.debug("dropping event %s for slow subscriber", name)


def _resolve_query_project(store: Store, body: object) -> str | None:
    """v2.6 phase 10.1: resolve the effective ``project_key`` for a query.

    Precedence:

    1. Explicit ``body.project_key`` (caller override)
    2. Legacy ``body.active_project`` (pre-1.1 clients)
    3. **Active workspace's first project_key** (v2.6 default)
    4. Persisted ``active_project`` pointer (legacy CLI compat)

    Returns ``None`` when no scope is set anywhere -- retrieval then
    sees BASE-only nodes per the v2.6 BASE-only mode contract.

    Accepts any object with ``project_key`` + ``active_project`` attrs;
    ``QueryIn`` is the production shape but tests pass a stand-in.
    """
    explicit = getattr(body, "project_key", None)
    if explicit:
        return explicit
    legacy_field = getattr(body, "active_project", None)
    if legacy_field:
        return legacy_field
    ws = workspaces.get_active_workspace(store)
    if ws is not None and ws.project_keys:
        return ws.project_keys[0]
    active = store.get_active_project()
    return active.project_key if active is not None else None


def _workspace_node_count(store: Store, project_keys: list[str]) -> int:
    """Sum the node count across all project_keys in a workspace.

    BASE-flagged nodes are included via count_nodes(include_base=True)
    which mirrors the retrieval contract: BASE knowledge applies to
    every workspace.
    """
    if not project_keys:
        # Empty workspaces only ever see BASE-flagged nodes. Walk every
        # node once and count just the BASE ones -- count_nodes can't
        # filter on the base flag directly.
        return sum(1 for n in store.list_nodes(limit=1_000_000) if n.base)
    total = 0
    for key in project_keys:
        per = store.count_nodes(project_key=key, include_base=True)
        total += sum(per.values())
    return total


def _chat_lock(state: AppState, conv_id: str) -> threading.Lock:
    """Per-conversation lock so only one AgentLoop runs at a time for a
    given conversation (design S5 concurrency). Lazily created; held by
    GET /v1/chat/<id>/events for the run's duration."""
    with state.event_subscribers_lock:  # reuse the existing mutex as the registry guard
        lock = state.chat_locks.get(conv_id)
        if lock is None:
            lock = threading.Lock()
            state.chat_locks[conv_id] = lock
    return lock


def _chat_provider(state: AppState, name: str):
    """Construct a provider, honoring an injected test factory."""
    factory = state.chat_provider_factory
    if factory is not None:
        return factory(name)
    api_key = keys.resolve_api_key(name)
    return providers.get_provider(name, api_key=api_key)


def create_app(*, store: Store | None = None, embedder: Embedder | None = None) -> FastAPI:
    """Build a FastAPI app. ``store`` and ``embedder`` may be injected for tests.

    When both are ``None``, the app constructs them in lifespan and tears down
    only what it owns on shutdown.
    """
    state = AppState(store=store, embedder=embedder, owns_store=store is None)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if state.store is None:
            paths.ensure_runtime_dirs()
            state.store = Store(paths.db_path())
        if state.embedder is None:
            state.embedder = Embedder()
        try:
            yield
        finally:
            if state.owns_store and state.store is not None:
                state.store.close()

    app = FastAPI(title="mnemo", version=__version__, lifespan=lifespan)
    # Expose the per-app state on app.state so tests (and any other
    # caller that gets a handle on the FastAPI instance) can reach
    # the reindex lock + started_at counter without monkey-patching
    # internals. The closure-captured ``state`` above is the same
    # object, so any mutation here is visible to every route.
    app.state.mnemo_state = state
    # The version header stamps every response (including 404s from
    # now-removed legacy paths) so adapters introspecting failed
    # requests still see the daemon version.
    app.add_middleware(_ApiVersionHeaderMiddleware)

    def get_store() -> Store:
        assert state.store is not None
        return state.store

    def get_embedder() -> Embedder:
        assert state.embedder is not None
        return state.embedder

    # ------------------------------------------------------------------
    # v1 router -- every public HTTP endpoint lives here.
    # ------------------------------------------------------------------
    v1 = APIRouter(prefix="/v1", tags=["v1"])

    # --- Health -----------------------------------------------------------

    @v1.get("/health", response_model=HealthOut)
    def health(s: Store = Depends(get_store)) -> HealthOut:
        counts = s.count_nodes()
        return HealthOut(
            ok=True,
            version=__version__,
            node_count=sum(counts.values()),
            source_count=len(s.list_sources()),
            counts_by_type=counts,
            embedding_loaded=getattr(state.embedder, "_model", None) is not None,
        )

    # --- Sources ----------------------------------------------------------

    @v1.get("/sources", response_model=list[SourceOut])
    def list_sources(s: Store = Depends(get_store)) -> list[SourceOut]:
        return [SourceOut.from_source(src) for src in s.list_sources()]

    @v1.post("/sources", response_model=SourceOut)
    def add_source(body: SourceIn, s: Store = Depends(get_store)) -> SourceOut:
        try:
            s.register_source(
                body.path,
                body.kind,
                project_key=body.project_key,
                enabled=body.enabled,
                include=body.include,
                exclude=body.exclude,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        src = s.get_source(body.path)
        if src is None:
            raise HTTPException(status_code=500, detail="register_source failed")
        return SourceOut.from_source(src)

    @v1.post("/sources/preview", response_model=SourcePreviewOut)
    def preview_source(body: SourcePreviewIn) -> SourcePreviewOut:
        """v2.0 phase 2: dry-run preview for a candidate source path.

        Side-effect-free: scans the filesystem, proposes a kind via
        :func:`mnemo.auto_router.preview`, returns the result.
        Clients (CLI, UI) call this BEFORE ``POST /v1/sources`` so
        the user sees what would be indexed before any DB write.
        """
        try:
            result = auto_router.preview(body.path, force=body.force)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return SourcePreviewOut(
            path=result.path,
            proposed_kind=result.proposed_kind,
            confidence=result.confidence,
            breakdown=SourcePreviewBreakdownOut(
                by_ext=result.breakdown.by_ext,
                total_files=result.breakdown.total_files,
                md_with_frontmatter=result.breakdown.md_with_frontmatter,
                md_without_frontmatter=result.breakdown.md_without_frontmatter,
                has_git=result.breakdown.has_git,
            ),
            exceeds_safety_ceiling=result.exceeds_safety_ceiling,
        )

    @v1.patch("/sources", response_model=SourceOut)
    def patch_source(body: SourceUpdateIn, s: Store = Depends(get_store)) -> SourceOut:
        # Only forward fields the client actually sent. exclude_unset=True
        # keeps "field omitted" distinct from "field explicitly null".
        patch = body.model_dump(exclude_unset=True)
        patch.pop("path", None)
        kwargs: dict[str, object] = {}
        if "project_key" in patch:
            kwargs["project_key"] = patch["project_key"]
        if "enabled" in patch:
            kwargs["enabled"] = patch["enabled"]
        if "include" in patch:
            kwargs["include"] = patch["include"]
        if "exclude" in patch:
            kwargs["exclude"] = patch["exclude"]
        src = s.update_source(body.path, **kwargs)  # type: ignore[arg-type]
        if src is None:
            raise HTTPException(status_code=404, detail="source not found")
        return SourceOut.from_source(src)

    @v1.delete("/sources")
    def remove_source(path: str, s: Store = Depends(get_store)) -> JSONResponse:
        # v1.1.1: cascade-delete the source's nodes inside Store.remove_source.
        # Return the count so the UI can show "Source removed (N nodes cleaned
        # up)" rather than the old lie that nodes would be removed "on the
        # next reindex" (they wouldn't -- see Store.remove_source docstring).
        removed = s.remove_source(path)
        return JSONResponse({"ok": True, "removed": removed})

    # --- Reindex ----------------------------------------------------------

    @v1.post("/reindex", response_model=ReindexReportOut)
    def do_reindex(
        embed: bool = True,
        s: Store = Depends(get_store),
        e: Embedder = Depends(get_embedder),
    ) -> ReindexReportOut:
        # v1.1.1: refuse concurrent reindex calls with HTTP 409 so a stale
        # UI tab (or a script that didn't await the previous response)
        # can't kick off a parallel run. The first caller proceeds; later
        # callers get the start timestamp so they can poll /v1/reindex/status.
        if not state.reindex_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "reindex_in_progress",
                    "started_at": state.reindex_started_at,
                },
            )
        try:
            state.reindex_started_at = int(time.time())
            _broadcast_event(
                state,
                "reindex_started",
                {"at": int(time.time() * 1000)},
            )
            # v2.6 phase 6: cache the three-section report into app.state.
            # ingest.reindex returns the legacy aggregate; drain
            # reindex_events here so we can intercept the report event.
            from mnemo.ingest import ReindexReport

            report = ReindexReport()
            captured_report: dict | None = None
            for name, payload in ingest.reindex_events(s, embedder=e if embed else None):
                if name == "report":
                    captured_report = payload
                elif name == "done":
                    report.added = payload["added"]
                    report.updated = payload["updated"]
                    report.unchanged = payload["unchanged"]
                    report.removed = payload["removed"]
                    report.errors = [(err["path"], err["error"]) for err in payload["errors"]]
            if captured_report is not None:
                state.last_reindex_report = {
                    **captured_report,
                    "finished_at": int(time.time() * 1000),
                }
            _broadcast_event(
                state,
                "reindex_done",
                {
                    "at": int(time.time() * 1000),
                    "added": report.added,
                    "updated": report.updated,
                    "unchanged": report.unchanged,
                    "removed": report.removed,
                },
            )
            return ReindexReportOut.from_report(report)
        finally:
            state.reindex_started_at = None
            state.reindex_lock.release()

    @v1.get("/reindex/events")
    def reindex_events_route(
        embed: bool = True,
        s: Store = Depends(get_store),
        e: Embedder = Depends(get_embedder),
    ) -> StreamingResponse:
        """Server-Sent Events stream of reindex progress.

        Wire format: ``event: <name>\\ndata: <json>\\n\\n`` repeated.
        Event names: ``start`` (once), ``file`` (per-file), ``done``
        (once at the end). If another reindex is already in flight,
        emits a single ``event: busy`` frame and closes.

        Design: docs/plans/2026-05-14-ux-progressive-design.md § 2.
        """

        def encode(name: str, payload: dict) -> bytes:
            # Each frame is "event: <name>\ndata: <json>\n\n" -- two
            # newlines terminate the frame per the SSE spec.
            return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()

        def stream() -> Iterator[bytes]:
            # Same lock semantics as POST /v1/reindex -- one in-flight
            # reindex at a time. A second client gets a single busy
            # frame so it knows to fall back to polling /v1/reindex/status.
            if not state.reindex_lock.acquire(blocking=False):
                yield encode("busy", {"started_at": state.reindex_started_at})
                return
            try:
                state.reindex_started_at = int(time.time())
                _broadcast_event(state, "reindex_started", {"at": int(time.time() * 1000)})
                final_payload: dict | None = None
                captured_report: dict | None = None
                # ingest.reindex_events yields (name, payload) tuples.
                # We encode each one as an SSE frame and flush.
                for name, payload in ingest.reindex_events(s, embedder=e if embed else None):
                    if name == "report":
                        captured_report = payload
                    elif name == "done":
                        final_payload = payload
                    yield encode(name, payload)
                if captured_report is not None:
                    state.last_reindex_report = {
                        **captured_report,
                        "finished_at": int(time.time() * 1000),
                    }
                _broadcast_event(
                    state,
                    "reindex_done",
                    {
                        "at": int(time.time() * 1000),
                        "added": (final_payload or {}).get("added", 0),
                        "updated": (final_payload or {}).get("updated", 0),
                        "unchanged": (final_payload or {}).get("unchanged", 0),
                        "removed": (final_payload or {}).get("removed", 0),
                    },
                )
            finally:
                state.reindex_started_at = None
                state.reindex_lock.release()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            # Event streams must NEVER be cached -- proxies + the
            # browser would otherwise serve stale events on reconnect.
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",  # nginx hint: stream immediately
            },
        )

    @v1.get("/reindex/status")
    def reindex_status() -> JSONResponse:
        """Report whether a reindex is currently running.

        UI uses this on page load so the Reindex button shows the right
        state across navigations -- without it, the client-only "running"
        flag is wiped every reload and a user can fire a second reindex
        while the first is still in-flight.
        """
        running = state.reindex_lock.locked()
        return JSONResponse(
            {
                "running": running,
                "started_at": state.reindex_started_at if running else None,
            }
        )

    # --- v2.6 phase 4 + 5: dual-source proposal + workspaces --------------

    @v1.post("/sources/propose", response_model=SourceProposeOut)
    def propose_source_route(body: SourceProposeIn) -> SourceProposeOut:
        """Dual-source proposal for the add-source UI.

        See :func:`mnemo.auto_router.propose_source`. Returns docs_dir +
        code_repo proposals (each, both, or neither), parsed .gitignore
        patterns, plus any safeguard warnings.
        """
        try:
            result = auto_router.propose_source(body.path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return SourceProposeOut(
            path=result.path,
            proposals=[
                SourceProposalOut(
                    kind=p.kind,
                    include_pattern=p.include_pattern,
                    include_count=p.include_count,
                    est_nodes=p.est_nodes,
                    sample=p.sample,
                )
                for p in result.proposals
            ],
            gitignore_excludes=result.gitignore_excludes,
            gitignore_files_found=result.gitignore_files_found,
            warnings=result.warnings,
        )

    # --- Workspaces -------------------------------------------------------

    @v1.get("/workspaces", response_model=list[WorkspaceOut])
    def list_workspaces_route(s: Store = Depends(get_store)) -> list[WorkspaceOut]:
        return [WorkspaceOut.from_workspace(w) for w in workspaces.list_workspaces(s)]

    @v1.post("/workspaces", response_model=WorkspaceOut)
    def create_workspace_route(
        body: WorkspaceCreateIn, s: Store = Depends(get_store)
    ) -> WorkspaceOut:
        try:
            ws = workspaces.create_workspace(
                s,
                name=body.name,
                project_keys=body.project_keys,
                filter_prefs=body.filter_prefs,
                page_state=body.page_state,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkspaceOut.from_workspace(ws)

    @v1.get("/workspaces/active", response_model=ActiveWorkspaceOut)
    def get_active_workspace_route(s: Store = Depends(get_store)) -> ActiveWorkspaceOut:
        ws = workspaces.get_active_workspace(s)
        return ActiveWorkspaceOut(
            active=WorkspaceOut.from_workspace(ws) if ws is not None else None
        )

    @v1.post("/workspaces/clear")
    def clear_active_workspace_route(s: Store = Depends(get_store)) -> JSONResponse:
        workspaces.clear_active_workspace(s)
        _broadcast_event(state, "workspace_cleared", {"at": int(time.time() * 1000)})
        return JSONResponse({"ok": True})

    @v1.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
    def get_workspace_route(workspace_id: str, s: Store = Depends(get_store)) -> WorkspaceOut:
        ws = workspaces.get_workspace(s, workspace_id)
        if ws is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        return WorkspaceOut.from_workspace(ws)

    @v1.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
    def patch_workspace_route(
        workspace_id: str,
        body: WorkspaceUpdateIn,
        s: Store = Depends(get_store),
    ) -> WorkspaceOut:
        patch = body.model_dump(exclude_unset=True)
        kwargs: dict[str, object] = {}
        if "name" in patch:
            kwargs["name"] = patch["name"]
        if "project_keys" in patch:
            kwargs["project_keys"] = patch["project_keys"]
        if "filter_prefs" in patch:
            kwargs["filter_prefs"] = patch["filter_prefs"]
        if "page_state" in patch:
            kwargs["page_state"] = patch["page_state"]
        try:
            ws = workspaces.update_workspace(s, workspace_id, **kwargs)  # type: ignore[arg-type]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if ws is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        return WorkspaceOut.from_workspace(ws)

    @v1.delete("/workspaces/{workspace_id}")
    def delete_workspace_route(workspace_id: str, s: Store = Depends(get_store)) -> JSONResponse:
        ok = workspaces.delete_workspace(s, workspace_id)
        if not ok:
            raise HTTPException(status_code=404, detail="workspace not found")
        _broadcast_event(
            state, "workspace_deleted", {"id": workspace_id, "at": int(time.time() * 1000)}
        )
        return JSONResponse({"ok": True})

    @v1.post("/workspaces/{workspace_id}/activate", response_model=ActivateWorkspaceOut)
    def activate_workspace_route(
        workspace_id: str,
        soft_cap_nodes: int = DEFAULT_SOFT_CAP_NODES,
        hard_cap_nodes: int = DEFAULT_HARD_CAP_NODES,
        s: Store = Depends(get_store),
    ) -> ActivateWorkspaceOut:
        """Activate a workspace, enforcing soft + hard node-count caps.

        Returns 409 ``workspace_too_large`` if the total node count
        across the workspace's project_keys exceeds the hard cap. The
        soft cap surfaces as ``soft_cap_exceeded: True`` on a 200
        response so the UI shows a yellow chip without blocking the
        switch.
        """
        ws = workspaces.get_workspace(s, workspace_id)
        if ws is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        total_nodes = _workspace_node_count(s, ws.project_keys)
        if total_nodes > hard_cap_nodes:
            per_project = {
                key: sum(s.count_nodes(project_key=key, include_base=False).values())
                for key in ws.project_keys
            }
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "workspace_too_large",
                    "total_nodes": total_nodes,
                    "hard_cap": hard_cap_nodes,
                    "projects": [{"key": k, "nodes": v} for (k, v) in per_project.items()],
                },
            )
        try:
            workspaces.set_active_workspace(s, workspace_id)
        except workspaces.WorkspaceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="workspace not found") from exc
        updated = workspaces.get_workspace(s, workspace_id)
        assert updated is not None
        _broadcast_event(
            state,
            "workspace_activated",
            {
                "id": workspace_id,
                "name": updated.name,
                "project_keys": updated.project_keys,
                "total_nodes": total_nodes,
                "at": int(time.time() * 1000),
            },
        )
        return ActivateWorkspaceOut(
            workspace=WorkspaceOut.from_workspace(updated),
            total_nodes=total_nodes,
            soft_cap_exceeded=total_nodes > soft_cap_nodes,
        )

    # --- v2.6 phase 6: source overrides + reindex report -----------------

    @v1.get("/source_overrides", response_model=list[SourceOverrideOut])
    def list_source_overrides_route(
        s: Store = Depends(get_store),
    ) -> list[SourceOverrideOut]:
        return [SourceOverrideOut.from_override(ov) for ov in workspaces.list_source_overrides(s)]

    @v1.post("/source_overrides", response_model=list[SourceOverrideOut])
    def upsert_source_overrides_route(
        body: SourceOverrideBatchIn,
        s: Store = Depends(get_store),
    ) -> list[SourceOverrideOut]:
        """Batch upsert overrides. Returns the written rows.

        Accepts ``always_skip`` / ``always_keep`` / ``retry`` decisions
        (see :data:`mnemo.workspaces.ALLOWED_DECISIONS`). Unknown
        decisions return 400.
        """
        try:
            written = workspaces.batch_upsert_source_overrides(
                s, [item.model_dump() for item in body.items]
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return [SourceOverrideOut.from_override(ov) for ov in written]

    @v1.delete("/source_overrides")
    def delete_source_override_route(
        source_path: str,
        s: Store = Depends(get_store),
    ) -> JSONResponse:
        ok = workspaces.delete_source_override(s, source_path)
        if not ok:
            raise HTTPException(status_code=404, detail="override not found")
        return JSONResponse({"ok": True})

    @v1.get("/reindex/report", response_model=ReindexReportSectionsOut)
    def get_reindex_report_route() -> ReindexReportSectionsOut:
        """Most recent reindex report (auto_skipped / malformed / suspicious).

        Returns 404 if no reindex has run this daemon session. The report
        is overwritten on every run.
        """
        rep = state.last_reindex_report
        if rep is None:
            raise HTTPException(status_code=404, detail="no reindex report yet")
        return ReindexReportSectionsOut(**rep)

    # --- /v1/events SSE broadcast channel ---------------------------------

    @v1.get("/events")
    def events_stream() -> StreamingResponse:
        """Server-Sent Events channel for daemon-wide notifications.

        Frame types:
        - ``workspace_activated`` / ``workspace_deleted`` /
          ``workspace_cleared`` -- from the workspace routes
        - ``reindex_started`` / ``reindex_done`` -- from the reindex
          generator (wired in phase 6)
        - ``heartbeat`` -- every ~15s to keep proxies from disconnecting
        """
        # Per-client bounded queue. 64 frames is plenty for a UI that
        # only consumes a handful per minute; slow consumers drop the
        # oldest frames via the put_nowait path in _broadcast_event.
        q: queue.Queue = queue.Queue(maxsize=64)
        with state.event_subscribers_lock:
            state.event_subscribers.append(q)

        def encode(name: str, payload: dict) -> bytes:
            return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()

        def stream() -> Iterator[bytes]:
            # Initial hello so the client knows the connection is live
            # (avoids hanging the iter_lines() loop in tests that activate
            # before the first frame arrives).
            yield encode("hello", {"version": __version__, "at": int(time.time() * 1000)})
            try:
                while True:
                    try:
                        # Short timeout so we periodically emit a heartbeat
                        # (SSE comment line) to keep proxies happy.
                        name, payload = q.get(timeout=15.0)
                    except queue.Empty:
                        yield b": heartbeat\n\n"
                        continue
                    yield encode(name, payload)
            finally:
                with state.event_subscribers_lock, contextlib.suppress(ValueError):
                    state.event_subscribers.remove(q)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    # --- Nodes ------------------------------------------------------------

    @v1.get("/nodes", response_model=list[NodeOut])
    def list_nodes(
        type: str | None = None,
        project_key: str | None = None,
        limit: int = 100,
        s: Store = Depends(get_store),
    ) -> list[NodeOut]:
        return [
            NodeOut.from_node(n)
            for n in s.list_nodes(type=type, project_key=project_key, limit=limit)
        ]

    @v1.post("/nodes", response_model=NodeOut)
    def create_node(
        body: NodeCreateIn,
        s: Store = Depends(get_store),
        e: Embedder = Depends(get_embedder),
    ) -> NodeOut:
        """v1.2 phase 7: HTTP-driven memory creation.

        Lets clients that aren't writing files (VS Code "Add Note",
        SaaS ingesters, scripts) create a memory entry directly. The
        new node is embedded synchronously so it's queryable on the
        next ``/v1/query`` -- matches the post-reindex contract that
        filesystem-written entries get for free.

        Validates ``type`` and ``source_kind`` against the store-level
        enums; surfaces unknowns as ``400``. ``source_path`` defaults
        to a synthetic ``http://api/<uuid>`` so the watcher never
        tries to read it from disk.
        """
        import hashlib
        import uuid as _uuid

        synthetic_path = body.source_path or f"http://api/{_uuid.uuid4().hex}"
        try:
            node = Node.new(
                type=body.type,
                name=body.name,
                body=body.body,
                source_path=synthetic_path,
                source_kind=body.source_kind or "memory_dir",
                description=body.description,
                project_key=body.project_key,
                hash=hashlib.sha256(body.body.encode("utf-8")).hexdigest(),
                base=body.base,
            )
        except ValueError as exc:
            # Surfaces "unknown node type" / "unknown source kind".
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        s.upsert_node(node)

        # Embed eagerly so the new node is searchable immediately.
        # Matches the ``ingest.reindex`` post-write behavior for
        # filesystem entries.
        try:
            from mnemo.ingest import _embed

            _embed(s, node, e)
        except Exception:
            # Embedding failure shouldn't sink the create -- the node
            # still exists; reindex (or a later edit) can backfill the
            # vector. Log and move on.
            log.exception("embed of HTTP-created node %s failed", node.id)

        return NodeOut.from_node(node)

    @v1.get("/nodes/{node_id}", response_model=NodeOut)
    def get_node(node_id: str, s: Store = Depends(get_store)) -> NodeOut:
        n = s.get_node(node_id)
        if n is None:
            raise HTTPException(status_code=404, detail="node not found")
        return NodeOut.from_node(n)

    @v1.put("/nodes/{node_id}", response_model=NodeOut)
    def update_node(
        node_id: str,
        body: NodeUpdateIn,
        s: Store = Depends(get_store),
    ) -> NodeOut:
        n = s.get_node(node_id)
        if n is None:
            raise HTTPException(status_code=404, detail="node not found")
        if body.body is not None:
            n.body = body.body
        if body.description is not None:
            n.description = body.description
        if body.type is not None:
            n.type = body.type
        if body.project_key is not None:
            n.project_key = body.project_key
        if body.base is not None:
            n.base = body.base
        n.updated_at = int(time.time())
        s.upsert_node(n)
        return NodeOut.from_node(n)

    @v1.delete("/nodes/{node_id}")
    def delete_node(node_id: str, s: Store = Depends(get_store)) -> JSONResponse:
        s.delete_node(node_id)
        return JSONResponse({"ok": True})

    @v1.get("/nodes/{node_id}/full_source")
    def get_node_full_source(node_id: str, s: Store = Depends(get_store)) -> JSONResponse:
        """v2.1: re-read the file from disk for code-typed nodes.

        Stored bodies are truncated to a 60-line head (per v2.0 design,
        for LLM token budget). When the user opens a /node detail page
        and asks for the full content, this endpoint returns the
        on-disk source for that exact line range.

        Returns 404 if:
        - The node doesn't exist.
        - The node isn't a code_* type (memory bodies are already full).
        - The file no longer exists at the stored path.
        """
        from pathlib import Path as _Path

        n = s.get_node(node_id)
        if n is None:
            raise HTTPException(status_code=404, detail="node not found")
        if not n.type.startswith("code_"):
            raise HTTPException(
                status_code=404,
                detail="full_source only available for code_* nodes",
            )

        # Parse the ``<file>:<start>-<end>(#METHOD)?`` source_path. For
        # modules the suffix is missing -- treat as the whole file.
        sp = n.source_path
        file_part = sp
        line_range: tuple[int, int] | None = None
        if ":" in sp:
            head, _, tail = sp.rpartition(":")
            if "-" in tail:
                a, _, b_with_meta = tail.partition("-")
                b = b_with_meta.split("#", 1)[0]
                if a.isdigit() and b.isdigit():
                    file_part = head
                    line_range = (int(a), int(b))

        path = _Path(file_part)
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"source file not found on disk: {file_part}",
            )
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"read failed: {exc}") from exc

        if line_range is None:
            return JSONResponse(
                {
                    "source_path": file_part,
                    "language": _language_for_path(path),
                    "lines": [1, len(text.splitlines())],
                    "body": text,
                }
            )
        # Slice to the recorded range. Line numbers are 1-indexed.
        lines = text.splitlines()
        a, b = line_range
        body = "\n".join(lines[a - 1 : b])
        return JSONResponse(
            {
                "source_path": file_part,
                "language": _language_for_path(path),
                "lines": [a, b],
                "body": body,
            }
        )

    # --- Query ------------------------------------------------------------

    @v1.post("/query", response_model=QueryOut)
    def query(
        body: QueryIn,
        s: Store = Depends(get_store),
        e: Embedder = Depends(get_embedder),
    ) -> QueryOut:
        # v2.6 phase 10.1: workspaces drive retrieval scope. The resolver
        # walks: explicit -> legacy field -> active workspace -> legacy
        # active_project pointer. See _resolve_query_project.
        proj = _resolve_query_project(s, body)
        result = retrieve.query(
            s,
            e,
            body.prompt,
            budget_tokens=body.budget_tokens,
            k=body.k,
            active_project=proj,
        )
        return QueryOut.from_result(result)

    # --- Projects (v1.1) --------------------------------------------------

    @v1.post("/projects/resolve", response_model=ProjectResolveOut)
    def resolve_project(body: ProjectResolveIn) -> ProjectResolveOut:
        """Canonical project-key derivation. Adapter clients use this to
        avoid drift with the daemon's algorithm."""
        key = paths.resolve_project_key(body.path)
        return ProjectResolveOut(project_key=key, path=body.path)

    @v1.get(
        "/projects/active",
        response_model=ActiveProjectOut | None,  # type: ignore[arg-type]
    )
    def get_active_project(s: Store = Depends(get_store)) -> ActiveProjectOut | None:
        active = s.get_active_project()
        return ActiveProjectOut.from_active(active) if active else None

    @v1.post("/projects/active", response_model=ActiveProjectOut)
    def set_active_project(
        body: ProjectActivateIn, s: Store = Depends(get_store)
    ) -> ActiveProjectOut:
        key = paths.resolve_project_key(body.path)
        active = s.set_active_project(project_key=key, path=body.path)
        return ActiveProjectOut.from_active(active)

    @v1.delete("/projects/active")
    def clear_active_project(s: Store = Depends(get_store)) -> JSONResponse:
        s.clear_active_project()
        return JSONResponse({"ok": True})

    @v1.get("/projects/known", response_model=KnownProjectsOut)
    def known_projects(s: Store = Depends(get_store)) -> KnownProjectsOut:
        """Distinct project keys derived from registered sources + indexed
        nodes. UI uses this to suggest project_key values in dropdowns
        instead of forcing free-text entry."""
        # Sources: each source row has at most one project_key.
        src_by_key: dict[str, str] = {}  # project_key -> sample path
        src_count: dict[str, int] = {}
        for src in s.list_sources():
            if src.project_key:
                src_by_key.setdefault(src.project_key, src.path)
                src_count[src.project_key] = src_count.get(src.project_key, 0) + 1
        # Nodes: aggregate by project_key.
        node_count: dict[str, int] = {}
        for node in s.list_nodes(limit=10_000):
            if node.project_key:
                node_count[node.project_key] = node_count.get(node.project_key, 0) + 1
                src_by_key.setdefault(node.project_key, node.source_path)
        all_keys = sorted(src_by_key.keys())
        items = [
            KnownProjectItem(
                project_key=k,
                sample_path=src_by_key.get(k),
                node_count=node_count.get(k, 0),
                source_count=src_count.get(k, 0),
            )
            for k in all_keys
        ]
        return KnownProjectsOut(items=items)

    # --- Filesystem suggestion (v1.1) -----------------------------------

    @v1.get("/fs/suggest", response_model=FsSuggestOut)
    def fs_suggest(prefix: str = "") -> FsSuggestOut:
        """List directories matching a partial path, for the UI's path
        autocomplete. Daemon is 127.0.0.1-only so this runs as the user
        and only returns dirs they can already see.

        The "prefix" is interpreted as a partial absolute path. We split
        into (parent_dir, leaf_fragment) and list parent_dir's children
        whose names start with leaf_fragment. Capped at 50 candidates.
        """
        # Normalize separators per the running platform but tolerate
        # forward-slash input even on Windows (VS Code style).
        from pathlib import Path

        if not prefix.strip():
            # Empty prefix: suggest top-level common workspace roots.
            roots = []
            home = Path.home()
            for cand in [home, home / "Documents", home / "Desktop", Path("/")]:
                try:
                    if cand.exists() and cand.is_dir():
                        roots.append(str(cand))
                except OSError:
                    pass
            return FsSuggestOut(candidates=roots)
        try:
            p = Path(prefix)
            # If `prefix` is itself an existing dir, list its children.
            # Otherwise treat last segment as a leaf fragment to filter.
            if p.is_dir():
                parent = p
                fragment = ""
            else:
                parent = p.parent if str(p.parent) else Path(".")
                fragment = p.name
        except (OSError, ValueError):
            return FsSuggestOut(candidates=[])
        if not parent.is_dir():
            return FsSuggestOut(candidates=[])
        out: list[str] = []
        try:
            for child in sorted(parent.iterdir()):
                # Only directories. Skip hidden unless the user typed `.`.
                try:
                    if not child.is_dir():
                        continue
                except OSError:
                    continue
                name = child.name
                if name.startswith(".") and not fragment.startswith("."):
                    continue
                if fragment and not name.lower().startswith(fragment.lower()):
                    continue
                out.append(str(child))
                if len(out) >= 50:
                    break
        except (OSError, PermissionError):
            return FsSuggestOut(candidates=[])
        return FsSuggestOut(candidates=out)

    # --- Audit ------------------------------------------------------------

    @v1.get("/audit", response_model=list[QueryAuditOut])
    def audit(limit: int = 50, s: Store = Depends(get_store)) -> list[QueryAuditOut]:
        return [QueryAuditOut.from_query(q) for q in s.recent_queries(limit=limit)]

    # --- Feedback (v1.2 phase 1) -----------------------------------------

    @v1.post("/feedback", response_model=FeedbackOut)
    def post_feedback(body: FeedbackIn, s: Store = Depends(get_store)) -> FeedbackOut:
        """Record a feedback signal on a retrieval hit.

        Idempotent on ``(query_id, node_id, reason)``. If ``signal`` is
        omitted, the daemon defaults it from ``reason`` via
        :func:`signal_for_reason` (thumbs_up -> 1.0, thumbs_down -> -1.0,
        cite_copied -> 0.5, inferred_requery -> -0.5).

        404 if ``query_id`` does not exist in the audit log; the FK
        constraint would also block the insert, but we check up-front
        for a cleaner error message.
        """
        if body.reason not in FEEDBACK_REASONS:
            raise HTTPException(
                status_code=400,
                detail=f"unknown feedback reason: {body.reason!r}",
            )
        # Verify the query_id exists. Cheaper + clearer than letting
        # the FK fire an IntegrityError that the user has to decode.
        recent_ids = {q.id for q in s.recent_queries(limit=100_000)}
        if body.query_id not in recent_ids:
            raise HTTPException(
                status_code=404,
                detail=f"query_id not in audit log: {body.query_id!r}",
            )
        # Resolve effective signal: explicit override > canonical default.
        sig = body.signal if body.signal is not None else signal_for_reason(body.reason)
        try:
            event = s.log_feedback_event(
                query_id=body.query_id,
                node_id=body.node_id,
                signal=sig,
                reason=body.reason,
            )
        except sqlite3.IntegrityError as exc:
            # FK violation on node_id (or unlikely query_id race) lands here.
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FeedbackOut.from_event(event)

    @v1.get("/feedback", response_model=list[FeedbackOut])
    def list_feedback(
        query_id: str | None = None,
        node_id: str | None = None,
        s: Store = Depends(get_store),
    ) -> list[FeedbackOut]:
        """List feedback events, newest first.

        Requires at least one of ``query_id`` / ``node_id`` -- without a
        filter the response could be enormous and accidentally
        expensive on a long-lived store.
        """
        if query_id is None and node_id is None:
            raise HTTPException(
                status_code=400,
                detail="provide ?query_id=... and/or ?node_id=...",
            )
        events = s.list_feedback_events(query_id=query_id, node_id=node_id)
        return [FeedbackOut.from_event(e) for e in events]

    # --- Retune (v1.2 phase 6) -------------------------------------------

    @v1.post("/retune", response_model=RetuneReportOut)
    def do_retune(
        body: RetuneIn | None = None,
        s: Store = Depends(get_store),
    ) -> RetuneReportOut:
        """Run the auto-tuner against the audit log + feedback_event
        table and return a ``RetuneReport``.

        Preview-only: never mutates ``/v1/config``. The UI's Apply
        button is responsible for persisting via ``PUT /v1/config``
        with the proposed scoring dict. This keeps the retune endpoint
        idempotent and lets the user discard the proposal cleanly.

        Below the labeled-query threshold the response is still 200
        with ``train_size=0`` + a "below threshold" log line so the UI
        can show a helpful empty state without special-casing 4xx.
        """
        # Local import to avoid the retune <-> server import cycle.
        from mnemo import config as cfg_mod
        from mnemo.retune import retune

        cfg = cfg_mod.load()
        if body is None or body.min_queries is None:
            threshold = cfg.retune_min_queries
        else:
            threshold = body.min_queries
        report = retune(s, min_queries=threshold)
        return RetuneReportOut.from_report(report)

    # --- Config ----------------------------------------------------------

    @v1.get("/config")
    def get_config() -> dict:
        cfg = config.load()
        return {
            "scoring": {
                "alpha": cfg.scoring.alpha,
                "beta": cfg.scoring.beta,
                "gamma": cfg.scoring.gamma,
                "delta": cfg.scoring.delta,
                "epsilon": cfg.scoring.epsilon,
                "zeta": cfg.scoring.zeta,
            },
            "defaults": {
                "k": cfg.defaults.k,
                "budget_tokens": cfg.defaults.budget_tokens,
            },
            "recency_half_life_days": cfg.recency_half_life_days,
        }

    @v1.put("/config")
    def put_config(patch: dict) -> dict:
        config.update(patch)
        return get_config()

    @v1.post("/config/reset")
    def reset_config() -> dict:
        config.reset()
        return get_config()

    # --- Chat (v3) --------------------------------------------------------

    @v1.get("/chat", response_model=list[ConversationOut])
    def list_chat(
        project_key: str | None = None,
        include_archived: bool = False,
        s: Store = Depends(get_store),
    ) -> list[ConversationOut]:
        return [
            ConversationOut.from_conversation(c)
            for c in s.list_conversations(
                project_key=project_key, include_archived=include_archived
            )
        ]

    @v1.post("/chat", response_model=ConversationOut)
    def create_chat(body: ChatCreateIn, s: Store = Depends(get_store)) -> ConversationOut:
        provider = body.provider or "anthropic"
        model = body.model or providers.DEFAULT_MODELS.get(
            provider, providers.DEFAULT_MODELS["anthropic"]
        )
        conv = s.create_conversation(
            name=body.name or "New chat",
            provider=provider,
            model=model,
            project_key=body.project_key,
            page_context=body.page_context,
        )
        return ConversationOut.from_conversation(conv)

    @v1.get("/chat/{conv_id}", response_model=ConversationDetailOut)
    def get_chat(conv_id: str, s: Store = Depends(get_store)) -> ConversationDetailOut:
        conv = s.get_conversation(conv_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        # v3.1: only the LATEST window (the model context is bounded
        # separately by compaction). Older turns load via /messages.
        total = s.count_messages(conv_id)
        window = s.list_messages(conv_id, limit=CHAT_PAGE_DEFAULT)
        bm = {b.message_seq for b in s.list_bookmarks(conv_id)}
        return ConversationDetailOut.from_conversation_and_messages(
            conv,
            window,
            total=total,
            has_more=total > len(window),
            bookmarked_seqs=bm,
        )

    @v1.get("/chat/{conv_id}/messages", response_model=MessagesPageOut)
    def get_chat_messages(
        conv_id: str,
        before: int | None = None,
        limit: int = CHAT_PAGE_DEFAULT,
        s: Store = Depends(get_store),
    ) -> MessagesPageOut:
        """One older page (oldest-first) for lazy scroll-up. ``before``
        = the oldest seq currently shown; omit for the latest window.
        Paginates in SQL per reference_mnemo_pagination.md."""
        if s.get_conversation(conv_id) is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        limit = max(1, min(limit, 200))
        page = s.list_messages(conv_id, before_seq=before, limit=limit)
        bm = {b.message_seq for b in s.list_bookmarks(conv_id)}
        # seq is contiguous (0..total-1; purge drops the whole conv), so
        # an older page exists iff the oldest returned seq is not 0.
        has_more = bool(page) and page[0].seq > 0
        return MessagesPageOut.build(
            page,
            total=s.count_messages(conv_id),
            has_more=has_more,
            bookmarked_seqs=bm,
        )

    @v1.get("/chat/{conv_id}/bookmarks", response_model=list[ChatBookmarkOut])
    def list_chat_bookmarks(conv_id: str, s: Store = Depends(get_store)) -> list[ChatBookmarkOut]:
        if s.get_conversation(conv_id) is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return [ChatBookmarkOut.from_bookmark(b) for b in s.list_bookmarks(conv_id)]

    @v1.post("/chat/{conv_id}/bookmarks", response_model=ChatBookmarkOut)
    def add_chat_bookmark(
        conv_id: str, body: ChatBookmarkIn, s: Store = Depends(get_store)
    ) -> ChatBookmarkOut:
        if s.get_conversation(conv_id) is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        bm = s.add_bookmark(conv_id, message_seq=body.message_seq, label=body.label)
        return ChatBookmarkOut.from_bookmark(bm)

    @v1.delete("/chat/{conv_id}/bookmarks/{bookmark_id}")
    def delete_chat_bookmark(conv_id: str, bookmark_id: str, s: Store = Depends(get_store)) -> dict:
        if s.get_conversation(conv_id) is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        s.delete_bookmark(bookmark_id)
        return {"ok": True, "deleted": bookmark_id}

    @v1.patch("/chat/{conv_id}", response_model=ConversationOut)
    def patch_chat(
        conv_id: str, body: ChatPatchIn, s: Store = Depends(get_store)
    ) -> ConversationOut:
        conv = s.rename_conversation(
            conv_id,
            name=body.name,
            provider=body.provider,
            model=body.model,
            page_context=body.page_context,
        )
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return ConversationOut.from_conversation(conv)

    @v1.delete("/chat/{conv_id}")
    def delete_chat(conv_id: str, s: Store = Depends(get_store)) -> dict:
        if s.get_conversation(conv_id) is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        s.archive_conversation(conv_id)
        return {"ok": True, "archived": conv_id}

    @v1.post("/chat/{conv_id}/message", response_model=MessageAcceptedOut)
    def post_message(
        conv_id: str, body: MessageCreateIn, s: Store = Depends(get_store)
    ) -> MessageAcceptedOut:
        if s.get_conversation(conv_id) is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        stream_url = f"/v1/chat/{conv_id}/events"
        lock = _chat_lock(state, conv_id)
        if lock.locked():
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "a run is already in flight for this conversation",
                    "stream_url": stream_url,
                },
            )
        # Stash the user's text; the SSE GET runs the loop (which
        # persists the user message + streams the run).
        state.chat_pending[conv_id] = body.text
        return MessageAcceptedOut(stream_url=stream_url, conversation_id=conv_id)

    @v1.get("/chat/{conv_id}/events")
    def chat_events(conv_id: str, s: Store = Depends(get_store)) -> StreamingResponse:
        """SSE of the in-flight agent run. Frame format mirrors the
        reindex stream: ``event: <type>\\ndata: <json>\\n\\n``. One run
        per conversation -- a second connection while one is live gets a
        single ``busy`` frame; no pending message gets ``idle``."""
        conv = s.get_conversation(conv_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        def encode(name: str, payload: dict) -> bytes:
            return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()

        def gen() -> Iterator[bytes]:
            lock = _chat_lock(state, conv_id)
            if not lock.acquire(blocking=False):
                yield encode("busy", {"conversation_id": conv_id})
                return
            cancel = state.chat_cancel.setdefault(conv_id, threading.Event())
            cancel.clear()
            try:
                pending = state.chat_pending.pop(conv_id, None)
                if pending is None:
                    yield encode("idle", {"conversation_id": conv_id})
                    return
                try:
                    provider = _chat_provider(state, conv.provider)
                except Exception as exc:  # key/construction failure
                    yield encode("error", {"type": "error", "message": str(exc)})
                    return

                def permission_cb(req: dict) -> str:
                    # The loop already yielded the permission_request
                    # frame; block here until POST .../permit records a
                    # decision (or cancel denies it). 5 min ceiling.
                    evt = state.chat_permit_event.setdefault(conv_id, threading.Event())
                    evt.clear()
                    if not evt.wait(timeout=300):
                        return "deny"
                    return (state.chat_permit.get(conv_id) or {}).get("decision", "deny")

                loop = chat.AgentLoop(
                    s,
                    provider,
                    embedder=state.embedder,
                    model=conv.model,
                    project_key=conv.project_key,
                    permission_cb=permission_cb,
                )
                for ev in loop.run(conv_id, pending):
                    yield encode(ev["type"], ev)
                    if cancel.is_set():
                        yield encode("cancelled", {"type": "cancelled"})
                        break
            finally:
                lock.release()

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @v1.post("/chat/{conv_id}/permit")
    def permit_chat(conv_id: str, body: ChatPermitIn, s: Store = Depends(get_store)) -> dict:
        """Grant or deny a pending permission request. The decision is
        delivered to the blocked agent loop; ``allow_always`` is
        persisted to ``chat_permissions`` by the loop itself (design
        S4)."""
        if s.get_conversation(conv_id) is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        state.chat_permit[conv_id] = {
            "permission_id": body.permission_id,
            "decision": body.decision,
        }
        state.chat_permit_event.setdefault(conv_id, threading.Event()).set()
        return {"ok": True, "decision": body.decision}

    @v1.post("/chat/{conv_id}/cancel")
    def cancel_chat(conv_id: str, s: Store = Depends(get_store)) -> dict:
        if s.get_conversation(conv_id) is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        state.chat_cancel.setdefault(conv_id, threading.Event()).set()
        # also release a blocked permission wait with an implicit deny
        state.chat_permit[conv_id] = {"permission_id": "", "decision": "deny"}
        state.chat_permit_event.setdefault(conv_id, threading.Event()).set()
        return {"ok": True, "cancelled": conv_id}

    # --- Settings (v3 phase 7) -------------------------------------------

    def _settings_out() -> SettingsOut:
        cfg = config.load()
        providers = {
            name: {
                "has_key": keys.has_key(name),
                "model": (cfg.providers.get(name) or {}).get("model"),
            }
            for name in cfg.providers
        }
        return SettingsOut(
            default_provider=cfg.default_provider,
            providers=providers,
            companion=cfg.companion,
            chat_history_retention_days=cfg.chat_history_retention_days,
        )

    @v1.get("/providers", response_model=list[ProviderOut])
    def list_providers() -> list[ProviderOut]:
        """C2 (v4.1): the provider registry, for the C4 settings UI.
        Every registered provider appears automatically -- no key
        material, only declared capabilities."""
        from mnemo.providers import PROVIDERS

        return [
            ProviderOut(
                name=d.name,
                display_name=d.display_name,
                env_var=d.env_var,
                requires_key=d.requires_key,
                default_model=d.default_model,
                known_models=list(d.known_models),
                supports_compaction_models=sorted(d.native_compaction_models),
            )
            for d in PROVIDERS.values()
        ]

    @v1.get("/settings", response_model=SettingsOut)
    def get_settings() -> SettingsOut:
        """Never returns key material -- per-provider ``has_key`` only."""
        return _settings_out()

    @v1.post("/settings/providers", response_model=SettingsOut)
    def post_settings_providers(body: ProvidersPatchIn) -> SettingsOut:
        patch: dict = {}
        if body.default_provider:
            patch["default_provider"] = body.default_provider
        clean_providers: dict = {}
        for name, pcfg in (body.providers or {}).items():
            if not isinstance(pcfg, dict):
                continue
            key = pcfg.get("key")
            if key:
                keys.set_api_key(name, key)  # -> keychain (never persisted)
            if "model" in pcfg:
                clean_providers[name] = {"model": pcfg["model"]}
        if clean_providers:
            patch["providers"] = clean_providers
        if patch:
            config.update(patch)
        return _settings_out()

    @v1.post("/settings/companion", response_model=SettingsOut)
    def post_settings_companion(body: CompanionPatchIn) -> SettingsOut:
        comp = {
            k: v
            for k, v in {
                "name": body.name,
                "tone": body.tone,
                "dock_state": body.dock_state,
                "proactive": body.proactive,
                "proactive_pages": body.proactive_pages,
                "proactive_frequency": body.proactive_frequency,
            }.items()
            if v is not None
        }
        patch: dict = {}
        if comp:
            patch["companion"] = comp
        if body.chat_history_retention_days is not None:
            patch["chat_history_retention_days"] = body.chat_history_retention_days
        if patch:
            config.update(patch)
        return _settings_out()

    app.include_router(v1)

    # --------------------------------------------------------------------
    # OpenAPI: filter to v1-tagged paths only. This makes /openapi.json
    # (and the built-in /docs page) reflect the public contract, not the
    # internal UI/HTMX routes.
    # --------------------------------------------------------------------

    def _v1_openapi_schema() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title="mnemo",
            version=__version__,
            description=(
                "Local-first knowledge memory for Claude Code and other "
                "IDE / SDK clients. All public endpoints live under /v1/. "
                "Bind to 127.0.0.1 only."
            ),
            routes=app.routes,
        )
        # Keep only paths that start with /v1/. The default get_openapi
        # already drops include_in_schema=False routes, but we additionally
        # want to hide the legacy redirect handlers (which are middleware,
        # so they don't appear) and any incidental future leakage.
        schema["paths"] = {p: ops for p, ops in schema["paths"].items() if p.startswith("/v1/")}
        app.openapi_schema = schema
        return schema

    app.openapi = _v1_openapi_schema  # type: ignore[method-assign]

    @app.get("/v1/openapi.json", include_in_schema=False)
    def v1_openapi() -> JSONResponse:
        return JSONResponse(_v1_openapi_schema())

    # UI is mounted last so JSON endpoints take precedence over any wildcards.
    from mnemo.ui.routes import mount_ui

    mount_ui(app, get_store=get_store, get_embedder=get_embedder)

    return app
