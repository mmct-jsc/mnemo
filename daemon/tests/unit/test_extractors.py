"""v2.0 phase 6: Tier 3 backend framework extractors.

Three extractors at launch -- FastAPI + Flask (Python) and Express
(JavaScript / TypeScript). Each pattern-matches over the tree-sitter
AST for a language's typical framework idioms and emits one
``code_route`` :class:`CodeUnit` per route, with the handler
function's source_path threaded through for the post-pass to wire
a ``routes_to`` edge.

The extractors run on the same parsed tree that Tier 1 / 2 already
walked, so the per-file overhead is just one extra dispatch + a
shallow walk of the top-level statements.
"""

from __future__ import annotations

from pathlib import Path

# --- FastAPI extractor ----------------------------------------------------


def test_fastapi_get_decorator_emits_code_route() -> None:
    """``@app.get("/api/users") def list_users(): ...`` produces one
    ``code_route`` unit with method=GET path=/api/users and a handler
    pointer at ``list_users``."""
    from mnemo.parsers import code

    src = (
        b"from fastapi import FastAPI\n"
        b"app = FastAPI()\n"
        b'@app.get("/api/users")\n'
        b"def list_users():\n"
        b"    return []\n"
    )
    units = code.extract(Path("/repo/api.py"), src, language="python")
    routes = [u for u in units if u.type == "code_route"]
    assert len(routes) == 1
    r = routes[0]
    assert r.framework == "fastapi"
    assert r.route_method == "GET"
    assert r.route_path == "/api/users"
    # The handler's source_path is the Tier 1 unit for ``list_users``.
    handler = next(u for u in units if u.type == "code_function" and u.name == "list_users")
    assert r.handler_source_path == handler.source_path


def test_fastapi_post_decorator_emits_post_route() -> None:
    from mnemo.parsers import code

    src = (
        b"from fastapi import FastAPI\n"
        b"app = FastAPI()\n"
        b'@app.post("/api/users")\n'
        b"def create_user():\n"
        b"    return {}\n"
    )
    units = code.extract(Path("/repo/api.py"), src, language="python")
    routes = [u for u in units if u.type == "code_route"]
    assert len(routes) == 1
    assert routes[0].route_method == "POST"


def test_fastapi_router_decorator_emits_route() -> None:
    """``@router.get`` is the APIRouter idiom; same shape as ``@app.get``."""
    from mnemo.parsers import code

    src = (
        b"from fastapi import APIRouter\n"
        b"router = APIRouter()\n"
        b'@router.get("/api/items")\n'
        b"def list_items():\n"
        b"    return []\n"
    )
    units = code.extract(Path("/repo/api.py"), src, language="python")
    routes = [u for u in units if u.type == "code_route"]
    assert len(routes) == 1
    assert routes[0].route_method == "GET"
    assert routes[0].route_path == "/api/items"


def test_fastapi_name_is_method_plus_path() -> None:
    """``code_route`` display name is ``METHOD path``."""
    from mnemo.parsers import code

    src = (
        b"from fastapi import FastAPI\n"
        b"app = FastAPI()\n"
        b'@app.delete("/api/users/{id}")\n'
        b"def delete_user(id: int):\n"
        b"    return None\n"
    )
    units = code.extract(Path("/repo/api.py"), src, language="python")
    routes = [u for u in units if u.type == "code_route"]
    assert routes[0].name == "DELETE /api/users/{id}"


def test_fastapi_no_routes_in_file_without_decorators() -> None:
    """A Python file with no FastAPI decorators must not produce any
    ``code_route`` units. Sanity guard against false positives."""
    from mnemo.parsers import code

    src = b"def hello():\n    return 'world'\n"
    units = code.extract(Path("/repo/x.py"), src, language="python")
    assert all(u.type != "code_route" for u in units)


def test_fastapi_each_decorator_is_independent() -> None:
    """Two decorators on the same handler produce two routes (FastAPI
    supports stacked decorators for multiple paths). The post-pass
    will wire ``routes_to`` to the same handler from both routes."""
    from mnemo.parsers import code

    src = (
        b"from fastapi import FastAPI\n"
        b"app = FastAPI()\n"
        b'@app.get("/api/users")\n'
        b'@app.get("/api/v2/users")\n'
        b"def list_users():\n"
        b"    return []\n"
    )
    units = code.extract(Path("/repo/api.py"), src, language="python")
    routes = [u for u in units if u.type == "code_route"]
    paths = {r.route_path for r in routes}
    assert paths == {"/api/users", "/api/v2/users"}


# --- Flask extractor ------------------------------------------------------


def test_flask_route_decorator_emits_code_route() -> None:
    """``@app.route("/path")`` is Flask's basic shape -- defaults to GET."""
    from mnemo.parsers import code

    src = (
        b"from flask import Flask\n"
        b"app = Flask(__name__)\n"
        b'@app.route("/api/users")\n'
        b"def list_users():\n"
        b"    return []\n"
    )
    units = code.extract(Path("/repo/api.py"), src, language="python")
    routes = [u for u in units if u.type == "code_route"]
    assert len(routes) == 1
    assert routes[0].framework == "flask"
    assert routes[0].route_method == "GET"
    assert routes[0].route_path == "/api/users"


def test_flask_route_decorator_with_methods_kwarg() -> None:
    """``methods=["POST"]`` on ``@app.route`` overrides the default GET.

    For the simplest shape (a single method) we emit a single route
    with that method. Lists with multiple methods produce one route
    per method, mirroring how FastAPI works."""
    from mnemo.parsers import code

    src = (
        b"from flask import Flask\n"
        b"app = Flask(__name__)\n"
        b'@app.route("/api/users", methods=["POST"])\n'
        b"def create_user():\n"
        b"    return {}\n"
    )
    units = code.extract(Path("/repo/api.py"), src, language="python")
    routes = [u for u in units if u.type == "code_route"]
    assert len(routes) == 1
    assert routes[0].route_method == "POST"


def test_flask_blueprint_route_emits_route() -> None:
    """``@blueprint.route(...)`` is the same shape as ``@app.route``."""
    from mnemo.parsers import code

    src = (
        b"from flask import Blueprint\n"
        b"bp = Blueprint('api', __name__)\n"
        b'@bp.route("/api/items")\n'
        b"def list_items():\n"
        b"    return []\n"
    )
    units = code.extract(Path("/repo/api.py"), src, language="python")
    routes = [u for u in units if u.type == "code_route"]
    assert len(routes) == 1
    assert routes[0].route_method == "GET"
    assert routes[0].route_path == "/api/items"


# --- Express extractor ----------------------------------------------------


def test_express_app_get_call_emits_code_route() -> None:
    """``app.get('/api/users', handler)`` is the Express idiom -- a
    method call on an Express app, NOT a decorator. The extractor
    walks call expressions at module top level."""
    from mnemo.parsers import code

    src = (
        b"const express = require('express');\n"
        b"const app = express();\n"
        b"function listUsers(req, res) { res.json([]); }\n"
        b"app.get('/api/users', listUsers);\n"
    )
    units = code.extract(Path("/repo/api.js"), src, language="javascript")
    routes = [u for u in units if u.type == "code_route"]
    assert len(routes) == 1
    assert routes[0].framework == "express"
    assert routes[0].route_method == "GET"
    assert routes[0].route_path == "/api/users"


def test_express_router_post_emits_route() -> None:
    from mnemo.parsers import code

    src = (
        b"const express = require('express');\n"
        b"const router = express.Router();\n"
        b"router.post('/api/users', function (req, res) { res.json({}); });\n"
    )
    units = code.extract(Path("/repo/api.js"), src, language="javascript")
    routes = [u for u in units if u.type == "code_route"]
    assert len(routes) == 1
    assert routes[0].route_method == "POST"
    assert routes[0].route_path == "/api/users"


# --- Integration: routes_to edge wiring ----------------------------------


def test_reindex_creates_routes_to_edge(tmp_path: Path) -> None:
    """After reindex, a FastAPI route node has a ``routes_to`` edge to
    its handler function. The post-pass reads the route's
    ``handler_source_path`` and looks it up."""
    from mnemo import ingest
    from mnemo.store import Store

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "api.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        '@app.get("/api/users")\n'
        "def list_users():\n"
        "    return []\n",
        encoding="utf-8",
    )
    store = Store(tmp_path / "store.db")
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        route = next(n for n in store.list_nodes() if n.type == "code_route")
        handler = next(
            n for n in store.list_nodes() if n.type == "code_function" and n.name == "list_users"
        )
        edges = store.get_edges(src_id=route.id, relation="routes_to")
        dst_ids = {e.dst_id for e in edges}
        assert handler.id in dst_ids
    finally:
        store.close()
