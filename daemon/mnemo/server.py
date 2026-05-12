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

import logging
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from mnemo import __version__, config, ingest, paths, retrieve
from mnemo.api_schemas import (
    ActiveProjectOut,
    FeedbackIn,
    FeedbackOut,
    FsSuggestOut,
    HealthOut,
    KnownProjectItem,
    KnownProjectsOut,
    NodeCreateIn,
    NodeOut,
    NodeUpdateIn,
    ProjectActivateIn,
    ProjectResolveIn,
    ProjectResolveOut,
    QueryAuditOut,
    QueryIn,
    QueryOut,
    ReindexReportOut,
    RetuneIn,
    RetuneReportOut,
    SourceIn,
    SourceOut,
    SourceUpdateIn,
)
from mnemo.embed import Embedder
from mnemo.store import FEEDBACK_REASONS, Node, Store, signal_for_reason

log = logging.getLogger(__name__)

# v1.2 phase 7 removed the legacy 308 redirect bridge. The
# ``_LegacyRedirectMiddleware`` previously living here translated
# un-versioned paths like ``/health`` -> ``/v1/health``. It was a
# one-version-only bridge and the ``X-Mnemo-Api-Version`` header has
# been telling adapters to migrate throughout the v1.1 cycle.


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
            report = ingest.reindex(s, embedder=e if embed else None)
            return ReindexReportOut.from_report(report)
        finally:
            state.reindex_started_at = None
            state.reindex_lock.release()

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

    # --- Query ------------------------------------------------------------

    @v1.post("/query", response_model=QueryOut)
    def query(
        body: QueryIn,
        s: Store = Depends(get_store),
        e: Embedder = Depends(get_embedder),
    ) -> QueryOut:
        # v1.1 hybrid: explicit project_key in the request body wins, then
        # legacy 'active_project' field, then the persisted active project.
        proj = body.project_key or body.active_project
        if proj is None:
            active = s.get_active_project()
            if active is not None:
                proj = active.project_key
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
