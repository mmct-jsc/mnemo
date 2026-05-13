"""v2.0 phase 6: Express framework extractor.

Detects route registrations of the shape
``<app_or_router>.<method>(<path>, <handler>)`` at the top level of
a JavaScript / TypeScript module. ``<method>`` is one of the HTTP-
verb names Express exposes as instance methods on an Express app
or router.

Output: one :class:`CodeUnit` per detected route with
``framework='express'``, the HTTP method (uppercased), and the
path. Express handler resolution is intentionally shallow in phase
6 -- JS / TS Tier 1 function extraction lands in phase 7 alongside
the React / Next.js extractors, so ``handler_source_path`` stays
``None`` here. Once Tier 1 produces ``code_function`` nodes for JS,
the existing ``routes_to`` post-pass picks them up automatically.

The walker iterates top-level statements only. ``expression_statement``
nodes wrap the bare call form (the canonical Express idiom);
``variable_declaration`` / ``lexical_declaration`` wrappers around
the call (e.g. ``const route = app.get(...)``) are rare and out of
scope for now.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from mnemo.parsers.code import CodeUnit

if TYPE_CHECKING:  # pragma: no cover -- import-time only
    import tree_sitter


EXPRESS_METHODS = frozenset(
    {
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "options",
        "all",
        "use",
    }
)
"""HTTP-method names Express exposes plus ``all`` (any verb) and
``use`` (middleware mount). ``use`` is occasionally used to mount a
sub-router at a path prefix; we emit a route for it so the graph
captures the cross-stack endpoint association even though the
"method" is conceptually 'any'."""


def extract(
    tree: tree_sitter.Tree,
    source: bytes,
    file_path: str,
    tier1_units: list[CodeUnit],  # noqa: ARG001 -- handler resolution waits for phase 7
) -> list[CodeUnit]:
    """Walk top-level statements collecting Express route calls.

    Currently doesn't thread ``handler_source_path`` because JS Tier 1
    function units don't exist yet -- phase 7 will revisit once
    React / Next extractors land and JS Tier 1 ships alongside them.
    """
    routes: list[CodeUnit] = []
    program = tree.root_node
    for child in program.children:
        call = _top_level_call(child)
        if call is None:
            continue
        spec = _express_call_spec(call, source)
        if spec is None:
            continue
        method, path = spec
        routes.append(_build_route_unit(call, source, file_path, method=method, path=path))
    return routes


def _top_level_call(node: tree_sitter.Node) -> tree_sitter.Node | None:
    """Unwrap the trivial wrappers JS / TS uses around top-level calls.

    ``app.get(...)``           -> ``expression_statement`` -> ``call_expression``
    ``await app.get(...)``     -> ``expression_statement`` -> ``await_expression`` -> ``call_expression``

    Returns the call node, or ``None`` for any other shape.
    """
    if node.type != "expression_statement":
        return None
    if not node.children:
        return None
    inner = node.children[0]
    if inner.type == "call_expression":
        return inner
    if inner.type == "await_expression":
        for c in inner.children:
            if c.type == "call_expression":
                return c
    return None


def _express_call_spec(call: tree_sitter.Node, source: bytes) -> tuple[str, str] | None:
    """If ``call`` is an Express route registration, return
    ``(METHOD, path)``. Otherwise return None."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return None
    prop = fn.child_by_field_name("property")
    if prop is None:
        return None
    method_name = _text(prop)
    if method_name not in EXPRESS_METHODS:
        return None
    # We use the property name itself as the canonical HTTP method
    # in upper-case. ``use`` and ``all`` are kept verbatim so the
    # user can tell which Express idiom produced the route.
    method = method_name.upper()

    path = _first_string_arg(call, source)
    if path is None:
        return None
    return method, path


def _first_string_arg(call: tree_sitter.Node, source: bytes) -> str | None:
    """Return the first positional string argument of a
    ``call_expression``, unquoted."""
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type == "string":
            return _unquote_js_string(child, source)
        # Stop at the first non-trivial token that isn't a string.
        if child.type not in ("(", ")", ",", "comment"):
            return None
    return None


def _unquote_js_string(node: tree_sitter.Node, source: bytes) -> str:
    """JS string literals come in two AST shapes:

    - ``string`` with a top-level pair of quote characters wrapping
      ``string_fragment`` children (the canonical case).
    - Template literals (``template_string``) -- we don't try those
      here; routes aren't typically declared with backticks.

    We pull the concatenation of any ``string_fragment`` children;
    fall back to slicing the whole node and stripping outer quotes
    if no fragments are present.
    """
    fragments = [
        source[c.start_byte : c.end_byte].decode("utf-8", errors="replace")
        for c in node.children
        if c.type == "string_fragment"
    ]
    if fragments:
        return "".join(fragments)
    text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace").strip()
    for q in ('"', "'", "`"):
        if text.startswith(q) and text.endswith(q) and len(text) >= 2:
            return text[1:-1]
    return text


def _build_route_unit(
    call: tree_sitter.Node,
    source: bytes,
    file_path: str,
    *,
    method: str,
    path: str,
) -> CodeUnit:
    start_line = call.start_point[0] + 1
    end_line = call.end_point[0] + 1
    body_bytes = source[call.start_byte : call.end_byte]
    return CodeUnit(
        type="code_route",
        name=f"{method} {path}",
        body=body_bytes.decode("utf-8", errors="replace"),
        source_path=f"{file_path}:{start_line}-{end_line}#{method}",
        description=f"Express route {method} {path}",
        hash=hashlib.sha256(body_bytes + f"|{method}|{path}".encode()).hexdigest(),
        framework="express",
        route_method=method,
        route_path=path,
        # Handler resolution lands in phase 7 once JS Tier 1 extraction
        # produces ``code_function`` nodes for JavaScript.
        handler_source_path=None,
    )


def _text(node: tree_sitter.Node) -> str:
    text = getattr(node, "text", None)
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    if isinstance(text, str):
        return text
    return ""
