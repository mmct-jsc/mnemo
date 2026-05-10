"""FastAPI HTTP server for mnemo.

The server holds a single ``Store`` and ``Embedder`` for the lifetime of the
process. Both are constructed in ``lifespan`` so requests never pay setup cost.

Bind to ``127.0.0.1`` only. Never listen on ``0.0.0.0``.

v1.1 introduced URL versioning: every public endpoint lives under ``/v1/``.
Legacy paths (``/health``, ``/sources``, ``/reindex``, ``/nodes``, ``/query``,
``/audit``, ``/config``) return ``308 Permanent Redirect`` to their ``/v1/...``
equivalent so existing clients keep working through the v1.1 series. The
redirects are scheduled to be removed in v1.2.

The OpenAPI schema is filtered to v1-only paths and exposed at both
``/openapi.json`` (FastAPI default, used by the built-in /docs UI) and
``/v1/openapi.json`` (canonical, intentional URL adapters consume).
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from mnemo import __version__, config, ingest, paths, retrieve
from mnemo.api_schemas import (
    ActiveProjectOut,
    HealthOut,
    NodeOut,
    NodeUpdateIn,
    ProjectActivateIn,
    ProjectResolveIn,
    ProjectResolveOut,
    QueryAuditOut,
    QueryIn,
    QueryOut,
    ReindexReportOut,
    SourceIn,
    SourceOut,
)
from mnemo.embed import Embedder
from mnemo.store import Store

log = logging.getLogger(__name__)

# Roots that should 308 to their /v1/... equivalent. Each is matched as
# "exact" or "prefix-with-trailing-segment". UI HTML routes are NOT here --
# they live under their own paths (/, /nodes-page, /sources-page,
# /audit-page, /settings, /graph, /node/<id>, /static/, /ui/) and are never
# rewritten.
LEGACY_API_ROOTS = (
    "/health",
    "/sources",
    "/reindex",
    "/nodes",
    "/query",
    "/audit",
    "/config",
)


class _LegacyRedirectMiddleware(BaseHTTPMiddleware):
    """Translate legacy un-versioned API calls to /v1/... with 308.

    308 preserves the request method (POST stays POST, DELETE stays DELETE)
    and the body, which is what we need for adapters that haven't migrated.
    Browser fetch() follows 308 transparently when ``redirect: 'follow'`` is
    set (the default).
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        for root in LEGACY_API_ROOTS:
            # Match either exact (/health) or with a path tail (/nodes/<id>).
            if path == root or path.startswith(root + "/"):
                target = "/v1" + path
                if request.url.query:
                    target += "?" + request.url.query
                return RedirectResponse(url=target, status_code=308)
        return await call_next(request)


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
    # Starlette runs the LAST add_middleware OUTERMOST. We want the version
    # header to stamp every response, including 308 redirects from the
    # legacy-path middleware. So legacy-redirect goes in first (innermost)
    # and the version header goes in last (outermost), wrapping it.
    app.add_middleware(_LegacyRedirectMiddleware)
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
                body.path, body.kind, project_key=body.project_key, enabled=body.enabled
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        for src in s.list_sources():
            if src.path == body.path:
                return SourceOut.from_source(src)
        raise HTTPException(status_code=500, detail="register_source failed")

    @v1.delete("/sources")
    def remove_source(path: str, s: Store = Depends(get_store)) -> JSONResponse:
        s.remove_source(path)
        return JSONResponse({"ok": True})

    # --- Reindex ----------------------------------------------------------

    @v1.post("/reindex", response_model=ReindexReportOut)
    def do_reindex(
        embed: bool = True,
        s: Store = Depends(get_store),
        e: Embedder = Depends(get_embedder),
    ) -> ReindexReportOut:
        report = ingest.reindex(s, embedder=e if embed else None)
        return ReindexReportOut.from_report(report)

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

    # --- Audit ------------------------------------------------------------

    @v1.get("/audit", response_model=list[QueryAuditOut])
    def audit(limit: int = 50, s: Store = Depends(get_store)) -> list[QueryAuditOut]:
        return [QueryAuditOut.from_query(q) for q in s.recent_queries(limit=limit)]

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
