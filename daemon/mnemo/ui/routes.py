"""UI routes (HTML pages + HTMX fragments).

Mounted via :func:`mount_ui`. The UI is a thin client over the existing JSON
API: pages render templates; HTMX fragments return small HTML snippets for
search-as-you-type and partial reloads. All write operations still go through
the JSON endpoints in ``mnemo.server``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mnemo import config, retrieve
from mnemo.embed import Embedder
from mnemo.store import Store

UI_DIR = Path(__file__).parent
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"


def mount_ui(
    app: FastAPI,
    *,
    get_store: Any,
    get_embedder: Any,
) -> None:
    """Wire UI routes onto the FastAPI app.

    The two ``get_*`` callables come from the server's lifespan-managed state.
    Passing them in (rather than re-creating Store/Embedder here) keeps the UI
    pinned to the same instances the JSON API uses, so writes are visible
    immediately.
    """
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _ctx(page: str, **extra: Any) -> dict[str, Any]:
        return {"page": page, **extra}

    # --- Pages -----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, s: Store = Depends(get_store)) -> Any:
        nodes = s.list_nodes(limit=20)
        counts = s.count_nodes()
        return templates.TemplateResponse(
            request,
            "index.html",
            _ctx(page="index", nodes=nodes, counts=counts),
        )

    @app.get("/graph", response_class=HTMLResponse)
    def graph_page(request: Request) -> Any:
        return templates.TemplateResponse(request, "graph.html", _ctx(page="graph"))

    @app.get("/node/{node_id}", response_class=HTMLResponse)
    def node_page(node_id: str, request: Request, s: Store = Depends(get_store)) -> Any:
        node = s.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="node not found")
        outgoing = s.get_edges(src_id=node_id)
        incoming = s.get_edges(dst_id=node_id)
        return templates.TemplateResponse(
            request,
            "node.html",
            _ctx(page="node", node=node, outgoing=outgoing, incoming=incoming),
        )

    @app.get("/sources-page", response_class=HTMLResponse)
    def sources_page(request: Request, s: Store = Depends(get_store)) -> Any:
        # ``/sources-page`` so we don't collide with the JSON API at /sources.
        return templates.TemplateResponse(
            request,
            "sources.html",
            _ctx(page="sources", sources=s.list_sources()),
        )

    @app.get("/audit-page", response_class=HTMLResponse)
    def audit_page(request: Request, s: Store = Depends(get_store)) -> Any:
        return templates.TemplateResponse(
            request,
            "audit.html",
            _ctx(page="audit", queries=s.recent_queries(limit=50)),
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> Any:
        cfg = config.load()
        return templates.TemplateResponse(
            request,
            "settings.html",
            _ctx(
                page="settings",
                weights={
                    "alpha": cfg.scoring.alpha,
                    "beta": cfg.scoring.beta,
                    "gamma": cfg.scoring.gamma,
                    "delta": cfg.scoring.delta,
                    "epsilon": cfg.scoring.epsilon,
                    "zeta": cfg.scoring.zeta,
                },
                budget=cfg.defaults.budget_tokens,
                k=cfg.defaults.k,
                recency_half_life_days=cfg.recency_half_life_days,
            ),
        )

    # --- HTMX fragments --------------------------------------------------

    @app.get("/ui/search", response_class=HTMLResponse)
    def search_fragment(
        request: Request,
        q: str = "",
        s: Store = Depends(get_store),
        e: Embedder = Depends(get_embedder),
    ) -> Any:
        if not q.strip():
            return HTMLResponse("")
        result = retrieve.query(s, e, q, k=10, budget_tokens=600, update_graph=False)
        return templates.TemplateResponse(
            request,
            "_search_results.html",
            _ctx(page="index", result=result),
        )

    @app.get("/ui/graph-data")
    def graph_data(s: Store = Depends(get_store)) -> JSONResponse:
        nodes = s.list_nodes(limit=1000)
        elements: list[dict[str, Any]] = []
        node_ids: set[str] = set()
        for n in nodes:
            elements.append(
                {
                    "data": {
                        "id": n.id,
                        "label": n.name[:40],
                        "type": n.type,
                        "project": n.project_key,
                    }
                }
            )
            node_ids.add(n.id)
        # Pull edges only between nodes we surfaced, so the canvas isn't a hairball.
        for src_id in node_ids:
            for edge in s.get_edges(src_id=src_id):
                if edge.dst_id in node_ids:
                    elements.append(
                        {
                            "data": {
                                "source": edge.src_id,
                                "target": edge.dst_id,
                                "relation": edge.relation,
                                "weight": edge.weight,
                            }
                        }
                    )
        return JSONResponse({"elements": elements})
