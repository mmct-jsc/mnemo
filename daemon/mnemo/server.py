"""FastAPI HTTP server for mnemo.

The server holds a single ``Store`` and ``Embedder`` for the lifetime of the
process. Both are constructed in ``lifespan`` so requests never pay setup cost.

Bind to ``127.0.0.1`` only. Never listen on ``0.0.0.0``.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from mnemo import __version__, config, ingest, paths, retrieve
from mnemo.api_schemas import (
    HealthOut,
    NodeOut,
    NodeUpdateIn,
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

    def get_store() -> Store:
        assert state.store is not None
        return state.store

    def get_embedder() -> Embedder:
        assert state.embedder is not None
        return state.embedder

    # --- Health -----------------------------------------------------------

    @app.get("/health", response_model=HealthOut)
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

    @app.get("/sources", response_model=list[SourceOut])
    def list_sources(s: Store = Depends(get_store)) -> list[SourceOut]:
        return [SourceOut.from_source(src) for src in s.list_sources()]

    @app.post("/sources", response_model=SourceOut)
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

    @app.delete("/sources")
    def remove_source(path: str, s: Store = Depends(get_store)) -> JSONResponse:
        s.remove_source(path)
        return JSONResponse({"ok": True})

    # --- Reindex ----------------------------------------------------------

    @app.post("/reindex", response_model=ReindexReportOut)
    def do_reindex(
        embed: bool = True,
        s: Store = Depends(get_store),
        e: Embedder = Depends(get_embedder),
    ) -> ReindexReportOut:
        report = ingest.reindex(s, embedder=e if embed else None)
        return ReindexReportOut.from_report(report)

    # --- Nodes ------------------------------------------------------------

    @app.get("/nodes", response_model=list[NodeOut])
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

    @app.get("/nodes/{node_id}", response_model=NodeOut)
    def get_node(node_id: str, s: Store = Depends(get_store)) -> NodeOut:
        n = s.get_node(node_id)
        if n is None:
            raise HTTPException(status_code=404, detail="node not found")
        return NodeOut.from_node(n)

    @app.put("/nodes/{node_id}", response_model=NodeOut)
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

    @app.delete("/nodes/{node_id}")
    def delete_node(node_id: str, s: Store = Depends(get_store)) -> JSONResponse:
        s.delete_node(node_id)
        return JSONResponse({"ok": True})

    # --- Query ------------------------------------------------------------

    @app.post("/query", response_model=QueryOut)
    def query(
        body: QueryIn,
        s: Store = Depends(get_store),
        e: Embedder = Depends(get_embedder),
    ) -> QueryOut:
        result = retrieve.query(
            s,
            e,
            body.prompt,
            budget_tokens=body.budget_tokens,
            k=body.k,
            active_project=body.active_project,
        )
        return QueryOut.from_result(result)

    # --- Audit ------------------------------------------------------------

    @app.get("/audit", response_model=list[QueryAuditOut])
    def audit(limit: int = 50, s: Store = Depends(get_store)) -> list[QueryAuditOut]:
        return [QueryAuditOut.from_query(q) for q in s.recent_queries(limit=limit)]

    # --- Config ----------------------------------------------------------

    @app.get("/config")
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

    @app.put("/config")
    def put_config(patch: dict) -> dict:
        config.update(patch)
        return get_config()

    @app.post("/config/reset")
    def reset_config() -> dict:
        config.reset()
        return get_config()

    # UI is mounted last so JSON endpoints take precedence over any wildcards.
    from mnemo.ui.routes import mount_ui

    mount_ui(app, get_store=get_store, get_embedder=get_embedder)

    return app
