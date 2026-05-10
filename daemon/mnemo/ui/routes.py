"""UI routes (HTML pages + HTMX fragments).

Mounted via :func:`mount_ui`. The UI is a thin client over the existing JSON
API: pages render templates; HTMX fragments return small HTML snippets for
search-as-you-type and partial reloads. All write operations still go through
the JSON endpoints in ``mnemo.server``.
"""

from __future__ import annotations

import time
from collections import Counter
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

PAGE_SIZE = 25
PAGE_SIZE_AUDIT = 25


def mount_ui(
    app: FastAPI,
    *,
    get_store: Any,
    get_embedder: Any,
) -> None:
    """Wire UI routes onto the FastAPI app."""
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _ctx(page: str, **extra: Any) -> dict[str, Any]:
        return {"page": page, **extra}

    def _paginate(total: int, page: int, page_size: int) -> dict[str, int]:
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, pages))
        return {
            "page": page,
            "pages": pages,
            "page_size": page_size,
            "total": total,
            "offset": (page - 1) * page_size,
            "has_prev": page > 1,
            "has_next": page < pages,
        }

    def _pagination_qs(request: Request) -> str:
        """Build the query string with the 'page' param stripped."""
        return "&".join(f"{k}={v}" for k, v in request.query_params.multi_items() if k != "page")

    # --- Pages -----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, s: Store = Depends(get_store)) -> Any:
        counts = s.count_nodes()
        sources = s.list_sources()
        recent_nodes = s.list_nodes(limit=8)
        recent_queries = s.recent_queries(limit=8)
        # Top connected: highest-degree nodes by outgoing edges.
        all_nodes = s.list_nodes(limit=10_000)
        ids = [n.id for n in all_nodes]
        edges = s.get_edges_for_nodes(ids) if ids else []
        deg: Counter[str] = Counter()
        for e in edges:
            deg[e.src_id] += 1
            deg[e.dst_id] += 1
        top = sorted(all_nodes, key=lambda n: -deg.get(n.id, 0))[:6]
        top_with_deg = [(n, deg.get(n.id, 0)) for n in top]

        # Source health: oldest and newest indexed.
        last_indexed = max(
            (s_.last_indexed_at for s_ in sources if s_.last_indexed_at), default=None
        )

        total_nodes = sum(counts.values())
        # Sort the type counts so the bar chart is deterministic.
        sorted_counts = sorted(counts.items(), key=lambda kv: -kv[1])

        return templates.TemplateResponse(
            request,
            "dashboard.html",
            _ctx(
                page="dashboard",
                counts=counts,
                sorted_counts=sorted_counts,
                total_nodes=total_nodes,
                source_count=len(sources),
                edge_count=len(edges),
                query_count=len(s.recent_queries(limit=10_000)),
                last_indexed=last_indexed,
                recent_nodes=recent_nodes,
                recent_queries=recent_queries,
                top_connected=top_with_deg,
                now=int(time.time()),
            ),
        )

    @app.get("/nodes-page", response_class=HTMLResponse)
    def nodes_page(
        request: Request,
        page: int = 1,
        type: str | None = None,
        project: str | None = None,
        s: Store = Depends(get_store),
    ) -> Any:
        # Cheap total: list ids only and count.
        all_for_filter = s.list_nodes(type=type, project_key=project, limit=10_000)
        pg = _paginate(len(all_for_filter), page, PAGE_SIZE)
        nodes = all_for_filter[pg["offset"] : pg["offset"] + pg["page_size"]]
        counts = s.count_nodes()
        # Available projects for the filter dropdown.
        projects = sorted({n.project_key for n in s.list_nodes(limit=10_000) if n.project_key})
        return templates.TemplateResponse(
            request,
            "nodes.html",
            _ctx(
                page="nodes",
                nodes=nodes,
                counts=counts,
                pagination=pg,
                pagination_qs=_pagination_qs(request),
                filter_type=type,
                filter_project=project,
                projects=projects,
            ),
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
        # Resolve neighbor names so the detail page can show them.
        neighbor_ids = list({e.dst_id for e in outgoing} | {e.src_id for e in incoming})
        neighbors = s.get_nodes_by_ids(neighbor_ids) if neighbor_ids else {}
        return templates.TemplateResponse(
            request,
            "node.html",
            _ctx(
                page="nodes",  # so the navbar highlights "Nodes"
                node=node,
                outgoing=outgoing,
                incoming=incoming,
                neighbors=neighbors,
            ),
        )

    @app.get("/sources-page", response_class=HTMLResponse)
    def sources_page(request: Request, s: Store = Depends(get_store)) -> Any:
        return templates.TemplateResponse(
            request,
            "sources.html",
            _ctx(page="sources", sources=s.list_sources()),
        )

    @app.get("/audit-page", response_class=HTMLResponse)
    def audit_page(request: Request, page: int = 1, s: Store = Depends(get_store)) -> Any:
        # Pull a generous slice and paginate in Python; audit log is bounded
        # in practice (per-user). For very large logs we'd add OFFSET to the
        # store layer, but it's fine here.
        all_q = s.recent_queries(limit=10_000)
        pg = _paginate(len(all_q), page, PAGE_SIZE_AUDIT)
        queries = all_q[pg["offset"] : pg["offset"] + pg["page_size"]]

        # Summary stats over the FULL audit window so the side cards stay
        # stable across pagination.
        total_hits = sum(len(q.retrieved_ids) for q in all_q)
        avg_hits = (total_hits / len(all_q)) if all_q else 0.0
        first_ts = min((q.ts for q in all_q), default=0)
        last_ts = max((q.ts for q in all_q), default=0)
        span_days = max(0, (last_ts - first_ts) // 86400) if all_q else 0
        tag_counter: Counter[str] = Counter()
        for q in all_q:
            for t in q.intent_tags or []:
                if t and t != "none":
                    tag_counter[t] += 1
        top_tags = tag_counter.most_common(6)

        return templates.TemplateResponse(
            request,
            "audit.html",
            _ctx(
                page="audit",
                queries=queries,
                pagination=pg,
                pagination_qs=_pagination_qs(request),
                total_hits=total_hits,
                avg_hits=avg_hits,
                first_ts=first_ts,
                last_ts=last_ts,
                span_days=span_days,
                top_tags=top_tags,
            ),
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
            _ctx(page="nodes", result=result),
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
                        "name": n.name,
                        "type": n.type,
                        "project": n.project_key,
                    }
                }
            )
            node_ids.add(n.id)
        # Pull edges only between nodes we surfaced.
        if node_ids:
            edges = s.get_edges_for_nodes(list(node_ids))
            for edge in edges:
                if edge.dst_id in node_ids and edge.src_id in node_ids:
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
