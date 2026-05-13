"""v2.0 phase 6: Flask framework extractor.

Detects route declarations of the shape
``@<app_or_blueprint>.route(<path>, methods=[...])`` decorating a
top-level function. Flask defaults to ``methods=['GET']`` if the
kwarg isn't supplied, mirroring the framework's own runtime.

Output: one :class:`CodeUnit` per detected route. When the
``methods`` kwarg lists multiple HTTP verbs we emit one route per
verb -- the user sees ``POST /api/users`` and ``PUT /api/users`` as
separate nodes wired to the same handler, which lines up with how
the FastAPI extractor models stacked decorators.
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
    """Walk top-level ``decorated_definition`` nodes and emit a
    ``code_route`` :class:`CodeUnit` for each ``@<X>.route(...)``
    decorator. Multiple ``methods=[...]`` entries fan out to multiple
    routes."""
    handler_index = _index_handlers_by_line(tier1_units)

    routes: list[CodeUnit] = []
    # v2.0 phase 6.1: walk the whole tree so we catch the
    # ``create_app()`` factory idiom in Flask too.
    _walk_for_decorated_functions(tree.root_node, source, file_path, handler_index, routes)
    return routes


def _walk_for_decorated_functions(
    node: tree_sitter.Node,
    source: bytes,
    file_path: str,
    handler_index: dict[tuple[int, int], CodeUnit],
    routes: list[CodeUnit],
) -> None:
    if node.type == "decorated_definition":
        inner = _inner_function_of(node)
        if inner is not None:
            start_line = inner.start_point[0] + 1
            end_line = inner.end_point[0] + 1
            handler = handler_index.get((start_line, end_line))
            if handler is None:
                handler = _synthesize_handler_unit(inner, file_path)
            for dec in _decorators_of(node):
                routes.extend(_routes_from_decorator(dec, source, file_path, handler))
    for child in node.children:
        _walk_for_decorated_functions(child, source, file_path, handler_index, routes)


def _synthesize_handler_unit(inner: tree_sitter.Node, file_path: str) -> CodeUnit:
    name_node = inner.child_by_field_name("name")
    name = ""
    if name_node is not None:
        text = getattr(name_node, "text", None)
        if isinstance(text, bytes):
            name = text.decode("utf-8", errors="replace")
        elif isinstance(text, str):
            name = text
    start_line = inner.start_point[0] + 1
    end_line = inner.end_point[0] + 1
    return CodeUnit(
        type="code_function",
        name=name or "<anonymous>",
        body="",
        source_path=f"{file_path}:{start_line}-{end_line}",
        description=None,
        hash="",
    )


def _routes_from_decorator(
    decorator: tree_sitter.Node,
    source: bytes,
    file_path: str,
    handler: CodeUnit,
) -> list[CodeUnit]:
    """Parse a single ``decorator`` and return zero or more route
    units (one per HTTP method covered)."""
    call = _decorator_call_node(decorator)
    if call is None:
        return []
    if not _is_flask_route_call(call):
        return []
    path = _first_string_argument(call, source)
    if path is None:
        return []
    methods = _methods_kwarg(call, source) or ["GET"]
    return [
        _build_route_unit(decorator, source, file_path, handler, method=m.upper(), path=path)
        for m in methods
    ]


def _is_flask_route_call(call: tree_sitter.Node) -> bool:
    """True if the call's function expression is ``<receiver>.route``.

    The Flask idiom requires literally the attribute name ``route``
    (FastAPI uses HTTP-method names like ``get`` / ``post`` directly);
    the receiver can be anything (``app`` / ``api`` / ``blueprint`` /
    ``bp`` / ...).
    """
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "attribute":
        return False
    attr = fn.child_by_field_name("attribute")
    return attr is not None and _text(attr) == "route"


def _first_string_argument(call: tree_sitter.Node, source: bytes) -> str | None:
    """Return the first positional string argument of a call,
    unquoted, or None if the first positional argument isn't a
    string literal."""
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type == "string":
            return _unquote(_slice(source, child))
        if child.type == "keyword_argument":
            # Hit a kwarg before any positional string -- malformed
            # call for our purposes.
            return None
        if child.type not in ("(", ")", ",", "comment"):
            return None
    return None


def _methods_kwarg(call: tree_sitter.Node, source: bytes) -> list[str] | None:
    """Pull HTTP-method names from a ``methods=[...]`` kwarg.

    Returns None if the kwarg isn't present (caller falls back to
    ``["GET"]``). Returns an empty list only when the user wrote
    ``methods=[]`` literally, in which case we treat that as "no
    routes" -- malformed code that Flask itself would reject.
    """
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type != "keyword_argument":
            continue
        name = child.child_by_field_name("name")
        value = child.child_by_field_name("value")
        if name is None or value is None:
            continue
        if _text(name) != "methods":
            continue
        if value.type != "list":
            continue
        return [_unquote(_slice(source, item)) for item in value.children if item.type == "string"]
    return None


# --- Shared AST helpers (mirrors fastapi.py) ----------------------------


def _decorators_of(decorated: tree_sitter.Node) -> list[tree_sitter.Node]:
    return [c for c in decorated.children if c.type == "decorator"]


def _inner_function_of(decorated: tree_sitter.Node) -> tree_sitter.Node | None:
    for child in decorated.children:
        if child.type == "function_definition":
            return child
    return None


def _decorator_call_node(decorator: tree_sitter.Node) -> tree_sitter.Node | None:
    for child in decorator.children:
        if child.type == "call":
            return child
    return None


def _index_handlers_by_line(
    tier1_units: list[CodeUnit],
) -> dict[tuple[int, int], CodeUnit]:
    out: dict[tuple[int, int], CodeUnit] = {}
    for u in tier1_units:
        if u.type != "code_function":
            continue
        rng = _line_range_of(u.source_path)
        if rng is not None:
            out[rng] = u
    return out


def _line_range_of(source_path: str) -> tuple[int, int] | None:
    if ":" not in source_path:
        return None
    _head, _, tail = source_path.rpartition(":")
    if "-" not in tail:
        return None
    a, _, b = tail.partition("-")
    if not (a.isdigit() and b.isdigit()):
        return None
    return int(a), int(b)


def _build_route_unit(
    decorator: tree_sitter.Node,
    source: bytes,
    file_path: str,
    handler: CodeUnit,
    *,
    method: str,
    path: str,
) -> CodeUnit:
    start_line = decorator.start_point[0] + 1
    end_line = decorator.end_point[0] + 1
    body_bytes = _slice(source, decorator)
    return CodeUnit(
        type="code_route",
        name=f"{method} {path}",
        body=body_bytes.decode("utf-8", errors="replace"),
        source_path=f"{file_path}:{start_line}-{end_line}#{method}",
        description=f"Flask route {method} {path} -> {handler.name}",
        hash=hashlib.sha256(body_bytes + f"|{method}|{path}".encode()).hexdigest(),
        framework="flask",
        route_method=method,
        route_path=path,
        handler_source_path=handler.source_path,
    )


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
