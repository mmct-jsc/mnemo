"""v2.0 phase 6: FastAPI framework extractor.

Detects route declarations of the shape
``@<app_or_router>.<method>(<path>, ...)`` decorating a top-level
function, where ``<method>`` is one of the HTTP-verb names FastAPI
recognises.

Output: one :class:`CodeUnit` per detected route, with
``type='code_route'``, ``framework='fastapi'``, the HTTP method
and path captured verbatim from the decorator, and
``handler_source_path`` pointing at the Tier 1 unit for the
decorated function. The reindex post-pass turns the pointer into
a ``routes_to`` edge.

Stacked decorators on the same handler each produce their own
``code_route`` -- both routes wire to the same handler, which is the
right behaviour for FastAPI's multi-path-per-handler idiom.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from mnemo.parsers.code import CodeUnit

if TYPE_CHECKING:  # pragma: no cover -- import-time only
    import tree_sitter


FASTAPI_METHODS = frozenset({"get", "post", "put", "delete", "patch", "head", "options", "trace"})
"""HTTP-method names FastAPI ``APIRouter`` / ``FastAPI`` apps expose
as decorator factories. ``app.<method>(...)`` and ``router.<method>(...)``
are the canonical idioms."""


def extract(
    tree: tree_sitter.Tree,
    source: bytes,
    file_path: str,
    tier1_units: list[CodeUnit],
) -> list[CodeUnit]:
    """Return one ``code_route`` :class:`CodeUnit` per detected
    FastAPI route declaration in ``tree``.

    Walks only top-level statements -- nested routes (a route
    decorator inside a class method) aren't a documented FastAPI
    idiom and the rare cases we'd miss are not worth the extra
    walking cost.
    """
    # Index Tier 1 function units by the (start_line, end_line) range
    # encoded in their source_path so the route can carry the
    # handler's source_path verbatim. Methods aren't valid FastAPI
    # handlers at the module top level, so we only index functions
    # here (route decorators on methods are a phase 6.x concern).
    handler_index = _index_handlers_by_line(tier1_units)

    routes: list[CodeUnit] = []
    for child in tree.root_node.children:
        if child.type != "decorated_definition":
            continue
        decorators = _decorators_of(child)
        inner = _inner_function_of(child)
        if inner is None:
            continue
        # Tree-sitter ``start_point`` is 0-indexed; our source_path
        # convention is 1-indexed, matching what editors show.
        start_line = inner.start_point[0] + 1
        end_line = inner.end_point[0] + 1
        handler = handler_index.get((start_line, end_line))
        if handler is None:
            continue
        for dec in decorators:
            route = _route_from_decorator(dec, source, file_path, handler)
            if route is not None:
                routes.append(route)
    return routes


# --- AST helpers ----------------------------------------------------------


def _index_handlers_by_line(
    tier1_units: list[CodeUnit],
) -> dict[tuple[int, int], CodeUnit]:
    """Build a ``(start_line, end_line) -> CodeUnit`` map from the
    line-range suffix on each unit's source_path. Only
    ``code_function`` units qualify -- module-level FastAPI routes
    decorate functions, not classes or methods."""
    out: dict[tuple[int, int], CodeUnit] = {}
    for u in tier1_units:
        if u.type != "code_function":
            continue
        rng = _line_range_of(u.source_path)
        if rng is not None:
            out[rng] = u
    return out


def _line_range_of(source_path: str) -> tuple[int, int] | None:
    """Parse the ``:<start>-<end>`` suffix on a declaration's
    source_path. Returns None for module nodes (no suffix) or any
    other shape mismatch."""
    if ":" not in source_path:
        return None
    _head, _, tail = source_path.rpartition(":")
    if "-" not in tail:
        return None
    a, _, b = tail.partition("-")
    if not (a.isdigit() and b.isdigit()):
        return None
    return int(a), int(b)


def _decorators_of(decorated: tree_sitter.Node) -> list[tree_sitter.Node]:
    """All ``decorator`` children of a ``decorated_definition`` node.

    Skips the trailing function / class definition (the decorated
    target). Returns decorators in source order.
    """
    return [c for c in decorated.children if c.type == "decorator"]


def _inner_function_of(decorated: tree_sitter.Node) -> tree_sitter.Node | None:
    """The function / class node wrapped by a ``decorated_definition``.

    Returns ``None`` if the wrapped target isn't a function -- class
    decorators are out of scope for route extraction.
    """
    for child in decorated.children:
        if child.type == "function_definition":
            return child
    return None


def _route_from_decorator(
    decorator: tree_sitter.Node,
    source: bytes,
    file_path: str,
    handler: CodeUnit,
) -> CodeUnit | None:
    """Parse a single ``decorator`` node. Returns a route CodeUnit
    if the decorator matches a FastAPI route shape; otherwise None
    (the decorator is something else -- ``@staticmethod``,
    ``@dataclass``, ``@validator``, ...)."""
    call = _decorator_call_node(decorator)
    if call is None:
        return None
    method = _fastapi_method_name(call)
    if method is None:
        return None
    path = _first_string_argument(call, source)
    if path is None:
        return None
    return _build_route_unit(
        decorator, source, file_path, handler, method=method.upper(), path=path
    )


def _decorator_call_node(decorator: tree_sitter.Node) -> tree_sitter.Node | None:
    """A decorator can be ``@name``, ``@name()``, or ``@name(args)``.
    The route shape requires the call form (the path is an
    argument). Return the call node or None.
    """
    for child in decorator.children:
        if child.type == "call":
            return child
    return None


def _fastapi_method_name(call: tree_sitter.Node) -> str | None:
    """Return the HTTP-method name if the call's function expression
    is ``<receiver>.<method>`` and ``<method>`` is a recognised
    FastAPI method. Otherwise None.

    Receiver-name matching is intentionally permissive -- we accept
    any identifier as the receiver since real codebases name their
    apps ``app`` / ``router`` / ``api`` / ``v1`` etc.
    """
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "attribute":
        return None
    attr = fn.child_by_field_name("attribute")
    if attr is None:
        return None
    name = _text(attr)
    return name if name in FASTAPI_METHODS else None


def _first_string_argument(call: tree_sitter.Node, source: bytes) -> str | None:
    """Return the unquoted first positional string argument of a call,
    or None if the first argument isn't a string literal."""
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type == "string":
            return _unquote(_slice(source, child))
        # Stop at the first non-comma non-paren that isn't a string --
        # the first positional argument must be the path.
        if child.type not in ("(", ")", ",", "comment"):
            return None
    return None


# --- CodeUnit construction -----------------------------------------------


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
        # Routes need a unique source_path even when two decorators
        # stack on the same handler. The line range is unique per
        # decorator (each decorator occupies its own line); we tack
        # on the method as a tiebreaker for the rare case where the
        # line range collides (decorator on the same line as another).
        source_path=f"{file_path}:{start_line}-{end_line}#{method}",
        description=f"FastAPI route {method} {path} -> {handler.name}",
        hash=hashlib.sha256(body_bytes + f"|{method}|{path}".encode()).hexdigest(),
        framework="fastapi",
        route_method=method,
        route_path=path,
        handler_source_path=handler.source_path,
    )


# --- Local helpers -------------------------------------------------------


def _text(node: tree_sitter.Node) -> str:
    """Read source text for a node via ``node.text`` (tree-sitter 0.20+)."""
    text = getattr(node, "text", None)
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    if isinstance(text, str):
        return text
    return ""


def _slice(source: bytes, node: tree_sitter.Node) -> bytes:
    return source[node.start_byte : node.end_byte]


def _unquote(s: str | bytes) -> str:
    """Strip a Python string literal's surrounding quotes (single,
    double, or triple-quoted variants). Handles ``b"..."`` /
    ``r"..."`` / ``f"..."`` prefixes by stripping the prefix
    character before the quote-strip."""
    if isinstance(s, bytes):
        s = s.decode("utf-8", errors="replace")
    s = s.strip()
    # Strip leading single-char prefix if it's a known one and a
    # quote follows.
    if len(s) >= 2 and s[0] in "rRbBfFuU" and s[1] in ("'", '"'):
        s = s[1:]
    for q in ('"""', "'''", '"', "'"):
        if s.startswith(q) and s.endswith(q) and len(s) >= 2 * len(q):
            return s[len(q) : -len(q)]
    return s
