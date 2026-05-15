"""UI routes (HTML pages + HTMX fragments).

Mounted via :func:`mount_ui`. The UI is a thin client over the existing JSON
API: pages render templates; HTMX fragments return small HTML snippets for
search-as-you-type and partial reloads. All write operations still go through
the JSON endpoints in ``mnemo.server``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mnemo import config, retrieve
from mnemo import workspaces as ws_mod
from mnemo.embed import Embedder
from mnemo.store import Node, Store

UI_DIR = Path(__file__).parent

# v2.6.0: type-priority order for the workspace canvas. Used by the
# /ui/graph-data route when the in-scope set exceeds the canvas cap.
# Module-level so ruff N806 stays happy + the constant is testable
# from outside.
_GRAPH_TYPE_PRIORITY: dict[str, int] = {
    "code_module": 0,
    "code_class": 1,
    "code_route": 2,
    "code_endpoint": 3,
    "code_component": 4,
    "memory_reference": 5,
    "memory_project": 6,
    "memory_feedback": 7,
    "memory_user": 8,
    "session_summary": 9,
    "project_doc": 10,
    "plan_doc": 11,
    "code_function": 12,
    "code_method": 13,
    "commit": 14,
}
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"

PAGE_SIZE = 25
PAGE_SIZE_AUDIT = 25


def _graph_scope_key(*, project: str | None, project_keys: str | None, base_only: bool) -> str:
    """Stable cache key for one Nebula scope. Mirrors the scope
    precedence the graph-data endpoint + client use so the layout
    cache is partitioned per workspace / deep-link / base-only view.
    """
    if project:
        return f"project:{project}"
    if project_keys:
        keys = sorted(k.strip() for k in project_keys.split(",") if k.strip())
        return "keys:" + ",".join(keys)
    if base_only:
        return "base_only"
    return "global"


def _graph_fingerprint(node_ids: list[str], edge_count: int) -> str:
    """Content fingerprint of the in-scope graph. Changes iff the
    node set or edge count changes -- which is exactly what a reindex
    / node-write does. mnemo node ids are content hashes already, so
    a hash of the sorted id set + edge count is a strong, cheap
    invalidation key for the cached force layout.
    """
    h = hashlib.sha1()
    for nid in sorted(node_ids):
        h.update(nid.encode("utf-8"))
        h.update(b"\x00")
    h.update(f"|edges={edge_count}".encode())
    return h.hexdigest()


def mount_ui(
    app: FastAPI,
    *,
    get_store: Any,
    get_embedder: Any,
) -> None:
    """Wire UI routes onto the FastAPI app."""
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    # Expose the daemon's version globally to every template so the
    # base layout can append it as a cache-bust query string on
    # /static/app.css. Without this, browser cache pins old CSS
    # across version bumps and small UI fixes look like they're
    # "still broken" until a hard reload.
    from mnemo import __version__ as _mnemo_version

    templates.env.globals["mnemo_version"] = _mnemo_version
    # Share the single-source node-type palette with every template
    # (and indirectly with JS via base.html). Adding a new node type
    # means editing palette.py only -- badges, bar fills, graph nodes,
    # and the audit chips all pick up the new color automatically.
    from mnemo.ui import palette as _palette

    templates.env.globals["type_colors"] = _palette.TYPE_COLORS
    templates.env.globals["type_color_fallback"] = _palette.FALLBACK_COLOR
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
                query_count=s.count_queries(),
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
        # Form submissions yield "" for un-selected dropdowns. Normalize to
        # None so the store doesn't WHERE project_key = '' (which matches
        # nothing) instead of "no filter".
        type = type or None
        project = project or None
        # v2.6.7: real SQL pagination. Scalar COUNT(*) for the (never
        # capped) total + LIMIT/OFFSET fetch of exactly one page --
        # was list_nodes(limit=10_000) + len()/slice which capped the
        # shown total at 10000 and loaded 10k rows to render 25.
        total = s.count_nodes_total(type=type, project_key=project)
        pg = _paginate(total, page, PAGE_SIZE)
        nodes = s.list_nodes(
            type=type,
            project_key=project,
            limit=pg["page_size"],
            offset=pg["offset"],
        )
        # Type counts respect the active project filter -- showing the global
        # count for "project (29)" while filtered to one project misleads.
        counts = s.count_nodes(project_key=project)
        # Available projects for the filter dropdown (full set, distinct
        # via SQL -- was a capped list_nodes scan that dropped projects
        # beyond the first 10 000 rows).
        projects = s.list_project_keys()
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

    @app.get("/settings/chat", response_class=HTMLResponse)
    def chat_settings_page(request: Request) -> Any:
        # v3 phase 7: companion / providers / permissions live on their
        # own page so the existing /settings retrieval-tuning view is
        # untouched (no regression). Cross-linked from there.
        return templates.TemplateResponse(request, "chat_settings.html", _ctx(page="settings"))

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

    @app.get("/workspaces", response_class=HTMLResponse)
    def workspaces_page(request: Request) -> Any:
        """v2.6 phase 8: workspace management page.

        Lists every workspace with per-card actions (Activate / Edit /
        Duplicate / Delete). Live node-count + warnings come from
        ``GET /v1/workspaces/<id>`` per-card; the page itself is a
        thin shell that hosts the Alpine factory and lets it fetch.
        """
        return templates.TemplateResponse(
            request,
            "workspaces.html",
            _ctx(page="workspaces"),
        )

    # --- /code UI family (v2.0 phase 11+) --------------------------------

    @app.get("/code", response_class=HTMLResponse)
    def code_landing(request: Request, s: Store = Depends(get_store)) -> Any:
        """Landing page: one card per code_repo project + a cross-stack
        endpoint summary.

        v2.6.0 polish: scopes to the active workspace's ``project_keys``.
        When the workspace has no code-typed nodes in its set the page
        shows an empty state (mentioning the workspace name) instead
        of listing every code project on disk. With no active workspace
        the page drops into BASE-only mode and shows no code projects
        (code nodes are always project-scoped, never BASE-flagged).
        """
        # v2.6.0 polish: resolve the active workspace's project_keys.
        # Three states:
        #   active + project_keys non-empty -> filter to that set
        #   active + project_keys empty     -> BASE-only (no code)
        #   no active                       -> show all (back-compat)
        active_ws = ws_mod.get_active_workspace(s)
        if active_ws is None or not active_ws.project_keys:
            # No active workspace OR active workspace with empty
            # project_keys -> BASE-only UI mode per the v2.6 design.
            # /code in BASE-only mode is empty (code nodes are always
            # project-scoped, never BASE-flagged) so the page shows
            # a "Pick a workspace" empty state.
            scope_mode = "base_only"
            scope_keys: set[str] = set()
            scope_name: str | None = active_ws.name if active_ws else None
        else:
            scope_mode = "workspace"
            scope_keys = set(active_ws.project_keys)
            scope_name = active_ws.name

        # Aggregate code-typed nodes by project_key. A "project" here is
        # whatever project_key value the code nodes carry (auto-derived
        # from the source path during ingest).
        code_types = (
            "code_module",
            "code_function",
            "code_class",
            "code_method",
            "code_route",
            "code_component",
        )
        # One big read; small projects keep this cheap.
        all_code = []
        for t in code_types:
            all_code.extend(s.list_nodes(type=t, limit=100_000))

        # v2.6.0 polish: drop nodes outside the workspace scope BEFORE
        # the aggregation. base_only collapses to an empty list since
        # code nodes are never BASE-flagged.
        if scope_mode == "workspace":
            all_code = [n for n in all_code if n.project_key in scope_keys]
        elif scope_mode == "base_only":
            all_code = [n for n in all_code if n.base]

        # Group by project_key.
        by_project: dict[str | None, dict[str, int]] = {}
        sample_module_for_project: dict[str | None, str] = {}
        for n in all_code:
            key = n.project_key
            counts = by_project.setdefault(key, {})
            counts[n.type] = counts.get(n.type, 0) + 1
            if n.type == "code_module" and key not in sample_module_for_project:
                sample_module_for_project[key] = n.source_path

        projects = []
        for key, counts in by_project.items():
            # Cross-cutting code nodes (rare) -- show under a synthetic
            # "(unassigned)" bucket so they don't vanish.
            display_key = "(unassigned)" if key is None else key
            projects.append(
                {
                    "project_key": display_key,
                    "by_type": counts,
                    "total": sum(counts.values()),
                    "source_path": sample_module_for_project.get(key, ""),
                }
            )
        projects.sort(key=lambda p: -p["total"])

        # Cross-stack endpoint summary: every code_endpoint with the
        # number of incoming ``at_endpoint`` edges (the "fanout").
        # v2.6.0 polish: skip endpoints whose only at_endpoint edges
        # come from out-of-scope projects.
        endpoints = []
        for ep in s.list_nodes(type="code_endpoint", limit=10_000):
            in_edges = s.get_edges(dst_id=ep.id, relation="at_endpoint")
            if scope_mode in ("workspace", "base_only"):
                # Only count edges whose src node is in-scope.
                src_ids = [e.src_id for e in in_edges]
                if not src_ids:
                    continue
                src_nodes = s.get_nodes_by_ids(src_ids)
                if scope_mode == "workspace":
                    in_scope_count = sum(
                        1 for n in src_nodes.values() if n.project_key in scope_keys
                    )
                else:  # base_only
                    in_scope_count = sum(1 for n in src_nodes.values() if n.base)
                if in_scope_count == 0:
                    continue
            method, _, path = ep.source_path.removeprefix("endpoint:").partition(":")
            endpoints.append(
                {
                    "id": ep.id,
                    "method": method or "?",
                    "path": path or ep.name,
                    "fanout": len(in_edges),
                }
            )
        endpoints.sort(key=lambda e: -e["fanout"])

        return templates.TemplateResponse(
            request,
            "code_landing.html",
            _ctx(
                page="code",
                projects=projects,
                endpoints=endpoints,
                workspace_scope_mode=scope_mode,
                workspace_scope_name=scope_name,
            ),
        )

    @app.get("/code/{project_key}", response_class=HTMLResponse)
    def code_project(project_key: str, request: Request, s: Store = Depends(get_store)) -> Any:
        """Project drill-down: list modules + top-referenced
        functions / classes / routes within the project."""
        # Normalize the synthetic "(unassigned)" key from the landing
        # page back into a real None lookup.
        pk: str | None = None if project_key == "(unassigned)" else project_key

        modules = list(s.list_nodes(type="code_module", project_key=pk, limit=10_000))
        functions = list(s.list_nodes(type="code_function", project_key=pk, limit=10_000))
        classes = list(s.list_nodes(type="code_class", project_key=pk, limit=10_000))
        methods = list(s.list_nodes(type="code_method", project_key=pk, limit=10_000))
        routes = list(s.list_nodes(type="code_route", project_key=pk, limit=10_000))
        components = list(s.list_nodes(type="code_component", project_key=pk, limit=10_000))

        # Rank functions by call-fanout (incoming + outgoing) so the
        # central ones surface. Cheap on a few-thousand-function repo.
        ids = [n.id for n in functions + methods + classes]
        edges = s.get_edges_for_nodes(ids, relations=("calls",)) if ids else []
        deg: dict[str, int] = {}
        for e in edges:
            deg[e.src_id] = deg.get(e.src_id, 0) + 1
            deg[e.dst_id] = deg.get(e.dst_id, 0) + 1
        ranked_functions = sorted(functions + methods, key=lambda n: -deg.get(n.id, 0))[:25]

        # v2.1: group declarations by their parent module so the page
        # renders as a file-by-file breakdown instead of one giant
        # flat list. Each module gets the functions / classes /
        # methods whose source_path's bare-file-portion matches its
        # source_path. Module display name strips the longest common
        # repo prefix so deep Windows paths don't dominate.
        def _file_of(sp: str) -> str:
            # Strip ``:<start>-<end>`` and ``#METHOD`` suffixes.
            from re import sub

            return sub(r"(:\d+-\d+)(#.+)?$", "", sp.replace("\\", "/"))

        # Longest common prefix across modules' source_paths -- used
        # purely for display. The actual lookup uses full paths.
        norm_paths = [_file_of(m.source_path) for m in modules]
        common_prefix = ""
        if norm_paths:
            parts_lists = [p.split("/") for p in norm_paths]
            min_len = min(len(p) for p in parts_lists)
            for i in range(min_len):
                first = parts_lists[0][i]
                if all(pl[i] == first for pl in parts_lists):
                    common_prefix += first + "/"
                else:
                    break

        # Index declarations by their owning module path.
        decls_by_module: dict[str, dict[str, list[Any]]] = {}
        for d in functions + classes + methods + routes:
            mp = _file_of(d.source_path)
            bucket = decls_by_module.setdefault(
                mp, {"function": [], "class": [], "method": [], "route": []}
            )
            kind = d.type.removeprefix("code_")
            if kind in bucket:
                bucket[kind].append(d)

        # Build a sorted module breakdown with degree info.
        module_rows = []
        for m in sorted(modules, key=lambda n: n.source_path):
            mp = _file_of(m.source_path)
            decls = decls_by_module.get(mp, {})
            decl_count = sum(len(v) for v in decls.values())
            display_path = mp[len(common_prefix) :] if common_prefix else mp
            module_rows.append(
                {
                    "node": m,
                    "display_path": display_path or m.name,
                    "decls": decls,
                    "decl_count": decl_count,
                    "deg": deg.get(m.id, 0),
                }
            )
        module_rows.sort(key=lambda r: -r["decl_count"])

        # Lessons learned: pull memory_feedback nodes scoped to the
        # project so the page surfaces the "why is this here" digest.
        feedback_nodes = []
        if pk is not None:
            feedback_nodes = s.list_nodes(type="memory_feedback", project_key=pk, limit=20)

        return templates.TemplateResponse(
            request,
            "code_project.html",
            _ctx(
                page="code",
                project_key=project_key,
                resolved_key=pk,
                modules=modules,
                module_rows=module_rows,
                common_prefix=common_prefix,
                ranked_functions=ranked_functions,
                classes=classes,
                routes=routes,
                components=components,
                feedback_nodes=feedback_nodes,
                stats={
                    "modules": len(modules),
                    "functions": len(functions),
                    "classes": len(classes),
                    "methods": len(methods),
                    "routes": len(routes),
                    "components": len(components),
                    "endpoints": len(s.list_nodes(type="code_endpoint", limit=10_000)),
                },
            ),
        )

    @app.get("/code/{project_key}/sitemap", response_class=HTMLResponse)
    def code_sitemap(project_key: str, request: Request, s: Store = Depends(get_store)) -> Any:
        """Cross-stack sitemap: per endpoint, show every code_route +
        code_component pointing at it, and (when wired) the route's
        handler function.

        v2.0 phase 13 minimum: a flat list per endpoint, grouped by
        URI. The interactive force-directed graph view sits one phase
        out -- the data here is enough to verify the cross-stack join
        works end-to-end.
        """
        endpoints = s.list_nodes(type="code_endpoint", limit=10_000)
        rows: list[dict[str, Any]] = []
        for ep in endpoints:
            in_edges = s.get_edges(dst_id=ep.id, relation="at_endpoint")
            attached_ids = [e.src_id for e in in_edges]
            attached = s.get_nodes_by_ids(attached_ids) if attached_ids else {}
            routes = []
            components = []
            handlers: list[Any] = []
            for nid in attached_ids:
                node = attached.get(nid)
                if node is None:
                    continue
                if node.type == "code_route":
                    routes.append(node)
                    # Look up the route's handler via routes_to.
                    for re in s.get_edges(src_id=node.id, relation="routes_to"):
                        h = s.get_node(re.dst_id)
                        if h is not None:
                            handlers.append(h)
                elif node.type == "code_component":
                    components.append(node)
            if routes or components:
                method, _, path = ep.source_path.removeprefix("endpoint:").partition(":")
                rows.append(
                    {
                        "id": ep.id,
                        "method": method,
                        "path": path,
                        "routes": routes,
                        "components": components,
                        "handlers": handlers,
                    }
                )
        rows.sort(key=lambda r: (r["path"], r["method"]))

        return templates.TemplateResponse(
            request,
            "code_sitemap.html",
            _ctx(page="code", project_key=project_key, rows=rows),
        )

    @app.get("/code/{project_key}/function/{node_id}", response_class=HTMLResponse)
    def code_function_detail(
        project_key: str,
        node_id: str,
        request: Request,
        s: Store = Depends(get_store),
    ) -> Any:
        """Function detail with 2-hop ego-network: callers, callees,
        defining module, and any feedback nodes linked via ``mentions``."""
        node = s.get_node(node_id)
        if node is None or not node.type.startswith("code_"):
            raise HTTPException(status_code=404, detail="code node not found")

        callers = s.get_edges(dst_id=node_id, relation="calls")
        callees = s.get_edges(src_id=node_id, relation="calls")
        defines_in = s.get_edges(dst_id=node_id, relation="defines")
        method_of = s.get_edges(src_id=node_id, relation="method_of")
        # ``routes_to`` incoming: any routes that wire to this function.
        served_by = s.get_edges(dst_id=node_id, relation="routes_to")
        # ``references_function`` incoming: commits that touched this
        # function (decision provenance, when phase 9 lands).
        commits = s.get_edges(dst_id=node_id, relation="references_function")

        all_ids = list(
            {e.src_id for e in callers}
            | {e.dst_id for e in callees}
            | {e.src_id for e in defines_in}
            | {e.dst_id for e in method_of}
            | {e.src_id for e in served_by}
            | {e.src_id for e in commits}
        )
        neighbors = s.get_nodes_by_ids(all_ids) if all_ids else {}

        return templates.TemplateResponse(
            request,
            "code_function.html",
            _ctx(
                page="code",
                project_key=project_key,
                node=node,
                callers=callers,
                callees=callees,
                defines_in=defines_in,
                method_of=method_of,
                served_by=served_by,
                commits=commits,
                neighbors=neighbors,
            ),
        )

    @app.get("/audit-page", response_class=HTMLResponse)
    def audit_page(request: Request, page: int = 1, s: Store = Depends(get_store)) -> Any:
        # v2.6.7: real SQL pagination (the OFFSET the old comment said
        # "we'd add ... but it's fine here" -- it wasn't: the log
        # crossed 10 000 and the count + page both broke). Scalar
        # COUNT(*) total + LIMIT/OFFSET page fetch.
        total = s.count_queries()
        pg = _paginate(total, page, PAGE_SIZE_AUDIT)
        queries = s.recent_queries(limit=pg["page_size"], offset=pg["offset"])

        # v2.6.0 polish: resolve hit IDs to {name, description, type}
        # so the audit log shows what was actually returned instead of
        # bare 12-char ID prefixes. Walks every hit ID across the
        # rendered page, batches the lookup with get_nodes_by_ids
        # (single SELECT), and exposes the map to the template as
        # ``hit_meta``. Nodes that have since been removed by a
        # reindex resolve to ``None`` and the template renders them
        # as "[removed]" so the log row still makes sense.
        all_hit_ids: set[str] = set()
        for q in queries:
            for nid in q.retrieved_ids or []:
                all_hit_ids.add(nid)
        hit_nodes = s.get_nodes_by_ids(list(all_hit_ids)) if all_hit_ids else {}
        hit_meta: dict[str, dict[str, str | None]] = {}
        for nid in all_hit_ids:
            node = hit_nodes.get(nid)
            if node is None:
                hit_meta[nid] = {"name": None, "description": None, "type": None}
            else:
                hit_meta[nid] = {
                    "name": node.name,
                    "description": node.description or "",
                    "type": node.type,
                }

        # v2.6.7: side-card stats over the FULL log via a single SQL
        # aggregate pass (was a 10 000-row load-all that the
        # pagination rewrite removed -- and which silently capped the
        # stats too). Stable across pagination, uncapped, cheap.
        stats = s.query_audit_stats()
        total_queries = int(stats["total_queries"])
        total_hits = int(stats["total_hits"])
        avg_hits = (total_hits / total_queries) if total_queries else 0.0
        first_ts = int(stats["first_ts"])
        last_ts = int(stats["last_ts"])
        span_days = max(0, (last_ts - first_ts) // 86400) if total_queries else 0
        top_tags = [(t, n) for t, n in stats["top_tags"] if t and t != "none"][:6]

        return templates.TemplateResponse(
            request,
            "audit.html",
            _ctx(
                page="audit",
                queries=queries,
                hit_meta=hit_meta,
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
                # v1.2 phase 6: needed by the retune panel for the
                # "needs at least N labeled queries" hint.
                retune_min_queries=cfg.retune_min_queries,
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
    def graph_data(
        project: str | None = None,
        project_keys: str | None = None,
        base_only: bool = False,
        node: str | None = None,
        hops: int = 2,
        s: Store = Depends(get_store),
    ) -> JSONResponse:
        """v2.1 Nebula graph data feed.

        Backwards-compatible: no query string returns the full graph
        (same shape the v1 page consumed). Scoping params (v2.6.0
        polish so Nebula mirrors the active workspace):

        - ``?project=<key>``: legacy single-project filter (used by
          /code deep-links). Nodes with that ``project_key`` plus
          BASE / NULL nodes that connect to them.
        - ``?project_keys=k1,k2,...``: workspace multi-project filter.
          Returns nodes whose ``project_key`` is in the comma-separated
          set OR is NULL (global) OR has ``base=True``.
        - ``?base_only=1``: no-workspace UI mode. Returns ONLY nodes
          with ``base=True``. The Nebula page lands here when no
          workspace is active.
        - ``?node=<id>&hops=<n>``: ego-network of ``<id>`` out to
          ``n`` hops (default 2). Combine with any of the above
          scope params to constrain the expansion.

        Each node carries ``type`` / ``project`` / ``source_path``
        / ``description`` so the file-tree panel can group and the
        detail panel can render without a second round-trip.

        Each edge carries ``relation`` + ``weight`` + ``confidence``
        so the canvas can encode uncertainty in line style.
        """
        # Parse the new project_keys CSV up front so the seed-by-node
        # and global-scan branches share the same filter set.
        keys_set: set[str] | None = None
        if project_keys:
            keys_set = {k.strip() for k in project_keys.split(",") if k.strip()}
        # v2.6.0 polish: surfaced in the response so the UI can render
        # a "showing X of Y" truncation banner. The workspace path
        # below sets this to the pre-cap size; other paths leave it
        # at zero (no banner).
        total_in_scope_total: int = 0
        # v2.6.0 polish: every in-scope code_module returned UNBOUNDED
        # so the Nebula left-panel file tree shows the full project
        # layout even when the canvas is cap'd. The tree is the
        # navigation surface -- truncating it would hide modules
        # entirely. tree_modules is a flat list of {id, name,
        # source_path, type, project} dicts; the client builds the
        # tree from this side-channel instead of from the canvas
        # elements.
        tree_modules: list[dict[str, Any]] = []

        # v2.6.0 polish: tighten scope semantics for workspaces. Two
        # passes:
        #   pass 1 (strict)   -- accept nodes whose project_key is IN
        #                        the scope set OR who are BASE-flagged.
        #                        NULL-project nodes do NOT pass in pass 1.
        #   pass 2 (boundary) -- accept NULL-project nodes that share
        #                        an edge with any pass-1 node. This is
        #                        the "cross-cutting NULL nodes they
        #                        connect to" contract from the docstring.
        def _passes_scope_strict(node_obj: Node) -> bool:
            if base_only:
                return bool(node_obj.base)
            if keys_set is not None:
                return node_obj.project_key in keys_set or node_obj.base
            if project:
                return node_obj.project_key == project or node_obj.base
            return True

        # Step 1: collect the seed node set.
        if node:
            seed = s.get_node(node)
            if seed is None:
                return JSONResponse({"elements": []})
            seed_ids: set[str] = {seed.id}
            # BFS along edges (any relation) up to ``hops`` hops.
            frontier: set[str] = {seed.id}
            for _ in range(max(1, min(hops, 4))):
                if not frontier:
                    break
                edges = s.get_edges_for_nodes(list(frontier))
                next_frontier: set[str] = set()
                for e in edges:
                    for nid in (e.src_id, e.dst_id):
                        if nid not in seed_ids:
                            seed_ids.add(nid)
                            next_frontier.add(nid)
                frontier = next_frontier
            nodes = list(s.get_nodes_by_ids(list(seed_ids)).values())
            # Scope filter only when a scope is set. Ego-network deep-links
            # without scope return the entire BFS (matches pre-v2.6 contract).
            if base_only or keys_set is not None or project:
                in_scope = [n for n in nodes if _passes_scope_strict(n)]
                in_scope_ids = {n.id for n in in_scope}
                # pass 2: NULL-project boundary nodes EDGE-connected to in-scope.
                boundary: list[Node] = []
                if not base_only:
                    null_candidates = [
                        n
                        for n in nodes
                        if n.project_key is None and not n.base and n.id not in in_scope_ids
                    ]
                    if null_candidates and in_scope_ids:
                        edge_set = s.get_edges_for_nodes(
                            [n.id for n in null_candidates] + list(in_scope_ids)
                        )
                        connected: set[str] = set()
                        for e in edge_set:
                            if e.src_id in in_scope_ids and e.dst_id not in in_scope_ids:
                                connected.add(e.dst_id)
                            elif e.dst_id in in_scope_ids and e.src_id not in in_scope_ids:
                                connected.add(e.src_id)
                        boundary = [n for n in null_candidates if n.id in connected]
                nodes = in_scope + boundary
        else:
            # v2.6.0 polish: when a workspace project_keys scope is set,
            # query per-key (uses the project_key index) so the canvas
            # gets EVERY in-scope node. The chat-companion (v3) will
            # use the graph as a reference flow tree -- the user
            # explicitly asked for "always display all", so no
            # truncation cap. fcose layout choice + the client-side
            # progressive layout strategy handle large graphs without
            # blocking the main thread.
            if keys_set is not None:
                seen_ids: set[str] = set()
                collected: list[Node] = []
                # v2.6.0 polish: every in-scope node returned; the
                # tree_modules side-channel still captures every module
                # by source_path for the file tree navigation surface.
                for key in keys_set:
                    for n in s.list_nodes(
                        project_key=key,
                        limit=100_000,
                        include_base=False,
                    ):
                        if n.id not in seen_ids:
                            seen_ids.add(n.id)
                            collected.append(n)
                        if n.type == "code_module" and n.source_path:
                            tree_modules.append(
                                {
                                    "id": n.id,
                                    "name": n.name,
                                    "source_path": n.source_path,
                                    "type": n.type,
                                    "project": n.project_key,
                                }
                            )
                # BASE-flagged nodes apply to every workspace -- list them
                # once and dedupe.
                for n in s.list_nodes(limit=100_000):
                    if n.base and n.id not in seen_ids:
                        seen_ids.add(n.id)
                        collected.append(n)
                total_in_scope_total = len(collected)
                # Type-priority sort so the architecture-shaping nodes
                # land first in the rendered list -- when the client
                # picks a layout the high-priority types sit near the
                # center. No truncation.
                collected.sort(key=lambda n: (_GRAPH_TYPE_PRIORITY.get(n.type, 99), -n.updated_at))
                in_scope = collected
                in_scope_ids = {n.id for n in in_scope}
                # pass 2: NULL-project boundary nodes EDGE-connected to
                # in-scope.
                boundary: list[Node] = []
                if in_scope_ids:
                    null_candidates = [
                        n
                        for n in s.list_nodes(limit=100_000)
                        if n.project_key is None and not n.base and n.id not in in_scope_ids
                    ]
                    if null_candidates:
                        edge_set = s.get_edges_for_nodes(
                            [n.id for n in null_candidates] + list(in_scope_ids)
                        )
                        connected: set[str] = set()
                        for e in edge_set:
                            if e.src_id in in_scope_ids and e.dst_id not in in_scope_ids:
                                connected.add(e.dst_id)
                            elif e.dst_id in in_scope_ids and e.src_id not in in_scope_ids:
                                connected.add(e.src_id)
                        boundary = [n for n in null_candidates if n.id in connected]
                nodes = in_scope + boundary
            elif base_only or project:
                nodes = s.list_nodes(limit=100_000)
                in_scope = [n for n in nodes if _passes_scope_strict(n)]
                in_scope_ids = {n.id for n in in_scope}
                tree_modules.extend(
                    {
                        "id": n.id,
                        "name": n.name,
                        "source_path": n.source_path,
                        "type": n.type,
                        "project": n.project_key,
                    }
                    for n in in_scope
                    if n.type == "code_module" and n.source_path
                )
                boundary = []
                if not base_only:
                    null_candidates = [
                        n
                        for n in nodes
                        if n.project_key is None and not n.base and n.id not in in_scope_ids
                    ]
                    if null_candidates and in_scope_ids:
                        edge_set = s.get_edges_for_nodes(
                            [n.id for n in null_candidates] + list(in_scope_ids)
                        )
                        connected = set()
                        for e in edge_set:
                            if e.src_id in in_scope_ids and e.dst_id not in in_scope_ids:
                                connected.add(e.dst_id)
                            elif e.dst_id in in_scope_ids and e.src_id not in in_scope_ids:
                                connected.add(e.src_id)
                        boundary = [n for n in null_candidates if n.id in connected]
                nodes = in_scope + boundary
            else:
                # No scope param at all -- legacy behavior, cap at 2000
                # to protect canvas rendering across the entire store.
                nodes = s.list_nodes(limit=2000)

        # v2.6.0 polish: no isolate drop. The user wants the full
        # graph so v3 chat can reference any node + flow. Isolated
        # nodes get a grid layout cluster on the canvas; the file
        # tree on the left renders them too. The chat companion can
        # still cite them. The layout choice (force vs grid) is the
        # client's decision -- not ours to filter for it.
        elements: list[dict[str, Any]] = []
        candidate_ids: set[str] = {n.id for n in nodes}
        edges_for_render: list[Any] = []
        if candidate_ids:
            for edge in s.get_edges_for_nodes(list(candidate_ids)):
                if edge.dst_id in candidate_ids and edge.src_id in candidate_ids:
                    edges_for_render.append(edge)

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
                        "source_path": n.source_path,
                        "description": n.description,
                    }
                }
            )
            node_ids.add(n.id)

        # Step 2: edges. Only between nodes we surfaced.
        for edge in edges_for_render:
            if edge.dst_id in node_ids and edge.src_id in node_ids:
                elements.append(
                    {
                        "data": {
                            "source": edge.src_id,
                            "target": edge.dst_id,
                            "relation": edge.relation,
                            "weight": edge.weight,
                            "confidence": getattr(edge, "confidence", 1.0),
                        }
                    }
                )
        # v2.6.0 polish: expose how many nodes were available vs how
        # many we returned so the canvas can show a "showing X of Y --
        # drill into a module for the full ego-network" banner.
        # ``total_in_scope`` is set inside the keys_set branch where
        # priority-cap may have truncated; other branches leave it at 0
        # which the client treats as "no truncation".
        shown_count = len(node_ids)
        # v2.6.0 polish: truncated is always False after the cap was
        # dropped -- kept in the schema for back-compat with clients
        # still reading the field. total_in_scope still useful so
        # the UI can show "rendering 10,762 nodes" once.
        # v2.6.3: layout-cache coordination. The client GETs
        # /ui/graph-layout?scope_key=&fingerprint= -- a hit means the
        # settled force-layout positions are already cached (instant
        # render, no GPU re-simulation). The fingerprint changes iff
        # the in-scope node set / edge count changes (reindex), so the
        # cache self-invalidates exactly on "impact actions".
        edge_count = len(elements) - len(node_ids)
        scope_key = _graph_scope_key(
            project=project, project_keys=project_keys, base_only=base_only
        )
        fingerprint = _graph_fingerprint(list(node_ids), edge_count)
        return JSONResponse(
            {
                "elements": elements,
                "shown_node_count": shown_count,
                "total_in_scope": total_in_scope_total or shown_count,
                "truncated": False,
                # v2.6.0 polish: unbounded module list for the
                # left-panel file tree. Lets the user navigate to a
                # module that may not be rendered on the canvas.
                "tree_modules": tree_modules,
                # v2.6.3: Nebula layout cache coordination.
                "scope_key": scope_key,
                "fingerprint": fingerprint,
            }
        )

    # --- Nebula layout cache (v2.6.3) -------------------------------------

    @app.get("/ui/graph-layout")
    def get_graph_layout(
        scope_key: str,
        fingerprint: str,
        s: Store = Depends(get_store),
    ) -> JSONResponse:
        """Return the cached settled force-layout for ``scope_key`` IFF
        the stored fingerprint still matches the live graph
        fingerprint the client just got from /ui/graph-data. A miss
        (no row, or a stale fingerprint after a reindex) tells the
        client to run the GPU simulation + PUT the result back.

        Positions are a flat JSON array ``[x0, y0, x1, y1, ...]`` in
        the same point order the client built (node id -> index), so
        the client applies them with ``setPointPositions`` directly.
        """
        cached = s.get_graph_layout(scope_key)
        if cached is None:
            return JSONResponse({"hit": False, "reason": "no_layout"})
        stored_fp, positions_json = cached
        if stored_fp != fingerprint:
            return JSONResponse({"hit": False, "reason": "stale"})
        try:
            positions = json.loads(positions_json)
        except (ValueError, TypeError):
            return JSONResponse({"hit": False, "reason": "corrupt"})
        return JSONResponse({"hit": True, "positions": positions})

    @app.put("/ui/graph-layout")
    async def put_graph_layout(
        request: Request,
        s: Store = Depends(get_store),
    ) -> JSONResponse:
        """Persist the settled layout once the client's GPU
        simulation converges. Body:
        ``{scope_key, fingerprint, positions: [x0,y0,...]}``. One row
        per scope; a fresh fingerprint overwrites the prior layout so
        the cache always reflects the latest converged graph.
        """
        try:
            body = await request.json()
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="invalid JSON body") from None
        scope_key = body.get("scope_key")
        fingerprint = body.get("fingerprint")
        positions = body.get("positions")
        if not scope_key or not fingerprint or not isinstance(positions, list):
            raise HTTPException(
                status_code=400,
                detail="scope_key, fingerprint, positions[] required",
            )
        # Guard against absurd payloads (a 2-float-per-node array).
        if len(positions) > 4_000_000:
            raise HTTPException(status_code=413, detail="layout too large")
        s.put_graph_layout(
            scope_key=scope_key,
            fingerprint=fingerprint,
            positions_json=json.dumps(positions, separators=(",", ":")),
        )
        return JSONResponse({"ok": True})
