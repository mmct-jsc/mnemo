"""v2.4.0 phase 8: Django framework extractor.

Detects Django URL configuration patterns:

    urlpatterns = [
        path("users/", views.user_list, name="user-list"),
        path("users/<int:pk>/", UserDetail.as_view(), name="user-detail"),
        re_path(r"^archive/(?P<year>[0-9]{4})/$", views.archive_year),
    ]

Output: one :class:`CodeUnit` of type ``code_route`` per ``path()``
or ``re_path()`` call THAT IS A LIST ELEMENT of an assignment whose
LHS is ``urlpatterns``. This anchoring prevents false positives from
helper modules that happen to call ``path()`` for non-routing
purposes.

Key differences from the Flask extractor:

- No decorator pattern -- everything is a function call in a
  module-level list literal.
- HTTP method is NOT declared at the URL layer (Django views
  implement methods themselves). We emit ``method = "*"`` to mean
  "any method this view handles".
- Views typically live in a different file than ``urls.py``, so
  cross-file handler resolution is the common case. v2.4.0 records
  the view name in the route description; same-file resolution
  works when ``views.py`` and ``urls.py`` happen to be the same
  module.

Design source: ``docs/plans/2026-05-11-mnemo-v2.0-design.md`` row 8
of the phase table + Tier 3 row for ``django.py`` in §4.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from mnemo.parsers.code import CodeUnit

if TYPE_CHECKING:  # pragma: no cover -- import-time only
    import tree_sitter


def extract(
    tree: tree_sitter.Tree,
    source: bytes,
    file_path: str,
    tier1_units: list[CodeUnit],
) -> list[CodeUnit]:
    """Find every ``path(...)`` / ``re_path(...)`` call that is a
    list element of an ``urlpatterns = [...]`` assignment and emit
    one ``code_route`` per match.
    """
    handler_index = _index_handlers_by_name(tier1_units)
    routes: list[CodeUnit] = []
    for url_call in _find_url_calls_in_urlpatterns(tree.root_node):
        route = _route_from_url_call(url_call, source, file_path, handler_index)
        if route is not None:
            routes.append(route)
    return routes


# --- urlpatterns anchor + iteration ---------------------------------


def _find_url_calls_in_urlpatterns(root: tree_sitter.Node) -> list[tree_sitter.Node]:
    """Walk the module's top-level statements, find each
    assignment whose LHS is ``urlpatterns``, then return the
    ``path()`` / ``re_path()`` call expressions inside the
    RHS list literal.
    """
    out: list[tree_sitter.Node] = []
    for stmt in root.children:
        # tree-sitter-python wraps top-level assignments inside
        # ``expression_statement`` nodes whose child is the actual
        # assignment.
        for inner in (stmt, *stmt.children):
            if inner.type != "assignment":
                continue
            lhs = inner.child_by_field_name("left")
            rhs = inner.child_by_field_name("right")
            if lhs is None or rhs is None:
                continue
            if _text(lhs) != "urlpatterns":
                continue
            if rhs.type != "list":
                continue
            for item in rhs.children:
                if item.type != "call":
                    continue
                fn = item.child_by_field_name("function")
                if fn is None:
                    continue
                fn_text = _text(fn)
                if fn_text in ("path", "re_path"):
                    out.append(item)
    return out


# --- Per-call -> route ----------------------------------------------


def _route_from_url_call(
    call: tree_sitter.Node,
    source: bytes,
    file_path: str,
    handler_index: dict[str, CodeUnit],
) -> CodeUnit | None:
    """Pull (url_pattern, view_expr) from a single ``path(...)`` /
    ``re_path(...)`` call and build the route unit.
    """
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    # First two positional arguments are (pattern, view).
    positional: list[tree_sitter.Node] = []
    for child in args.children:
        if child.type in ("(", ")", ",", "comment"):
            continue
        if child.type == "keyword_argument":
            continue
        positional.append(child)
        if len(positional) == 2:
            break
    if len(positional) < 2:
        return None
    pattern_node, view_node = positional[0], positional[1]
    pattern = _extract_url_pattern(pattern_node, source)
    if pattern is None:
        return None
    view_name = _view_name_of(view_node)
    if not view_name:
        view_name = "<unknown>"

    start_line = call.start_point[0] + 1
    end_line = call.end_point[0] + 1
    body_bytes = _slice(source, call)
    handler = handler_index.get(view_name)
    handler_source_path = handler.source_path if handler is not None else ""
    description = f"Django URL {pattern} -> {view_name}"

    return CodeUnit(
        type="code_route",
        name=f"* {pattern}",
        body=body_bytes.decode("utf-8", errors="replace"),
        # Django routes are method-agnostic; encode the route source
        # by line range without an HTTP-method suffix so a single
        # path declaration gets one source_path even when the view
        # implements multiple methods. The ``#*`` marker keeps the
        # shape parallel to the Flask / FastAPI suffix convention.
        source_path=f"{file_path}:{start_line}-{end_line}#*",
        description=description,
        hash=hashlib.sha256(body_bytes + f"|*|{pattern}".encode()).hexdigest(),
        framework="django",
        route_method="*",
        route_path=pattern,
        handler_source_path=handler_source_path,
    )


def _extract_url_pattern(node: tree_sitter.Node, source: bytes) -> str | None:
    """Pull the URL pattern out of the first positional arg.

    Accepts plain strings (``"users/"``) and raw / f-strings; the
    surrounding quotes (and r-prefix) are stripped.
    """
    if node.type != "string":
        # Could be a constant reference or expression -- not
        # something we resolve at extract time.
        return None
    return _unquote(_slice(source, node))


def _view_name_of(node: tree_sitter.Node) -> str:
    """Return a human-readable handler name from the view-position
    argument of a ``path()`` call:

    - bare identifier (``user_list``)            -> ``user_list``
    - module.attr (``views.user_list``)          -> ``user_list``
    - Class.as_view() (``UserDetail.as_view()``) -> ``UserDetail``
    - module.Class.as_view()                     -> ``Class``
    """
    if node.type == "call":
        # ``X.as_view()`` -- pull the receiver, then its rightmost
        # identifier.
        fn = node.child_by_field_name("function")
        if fn is None:
            return ""
        if fn.type == "attribute":
            attr = fn.child_by_field_name("attribute")
            if attr is not None and _text(attr) == "as_view":
                receiver = fn.child_by_field_name("object")
                if receiver is not None:
                    return _rightmost_identifier(receiver)
        return _rightmost_identifier(fn)
    return _rightmost_identifier(node)


def _rightmost_identifier(node: tree_sitter.Node) -> str:
    """For a chain like ``a.b.c``, return ``c``. For a bare
    identifier, return that identifier. For anything else, return
    the empty string.
    """
    if node.type == "identifier":
        return _text(node)
    if node.type == "attribute":
        attr = node.child_by_field_name("attribute")
        if attr is not None:
            return _text(attr)
    return ""


# --- Handler index --------------------------------------------------


def _index_handlers_by_name(
    tier1_units: list[CodeUnit],
) -> dict[str, CodeUnit]:
    """Build ``{name: CodeUnit}`` so we can resolve same-file
    function-based views or class-based-view classes by name.

    Django views typically live in a SEPARATE file from urls.py,
    so the same-file index will MISS in most cases. The post-pass
    resolver in ``ingest.py`` does the cross-file lookup; this
    index is a best-effort hot path for the small project / single
    file case.
    """
    out: dict[str, CodeUnit] = {}
    for u in tier1_units:
        if u.type in ("code_function", "code_class") and u.name and u.name not in out:
            out[u.name] = u
    return out


# --- AST helpers (parallel to flask.py + fastapi.py) ----------------


def _text(node: tree_sitter.Node) -> str:
    text = getattr(node, "text", None)
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    if isinstance(text, str):
        return text
    return ""


def _slice(source: bytes, node: tree_sitter.Node) -> bytes:
    return source[node.start_byte : node.end_byte]


def _unquote(s: str | bytes) -> str:
    if isinstance(s, bytes):
        s = s.decode("utf-8", errors="replace")
    s = s.strip()
    if len(s) >= 2 and s[0] in "rRbBfFuU" and s[1] in ("'", '"'):
        s = s[1:]
    for q in ('"""', "'''", '"', "'"):
        if s.startswith(q) and s.endswith(q) and len(s) >= 2 * len(q):
            return s[len(q) : -len(q)]
    return s
