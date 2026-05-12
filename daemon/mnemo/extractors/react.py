"""v2.0 phase 7: React framework extractor.

Detects React components + their data-fetching shapes. A React
component, for our purposes, is a top-level function whose name
starts with an uppercase letter AND whose body contains a JSX
element (``jsx_element`` or ``jsx_self_closing_element`` in the
tree-sitter TSX / JS grammars).

For each component we also walk its body for ``fetch("/api/...")``
calls -- the most common shape for "this component talks to this
backend endpoint". Each detected fetch path becomes a
``code_endpoint`` URI node (de-duplicated by path) plus an
``at_endpoint`` edge from the component to the endpoint. The
post-pass deduplicates endpoints across files so the same URL
called from React AND served by FastAPI / Express ends up on the
same node -- which is exactly the cross-stack sitemap join the
design promises.

Phase 7 is intentionally focused: only ``fetch()`` calls with a
string-literal URL are matched. React Query / SWR / axios shapes
are common in real codebases but each has its own pattern; they're
phase 7.x candidates.
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
    tier1_units: list[CodeUnit],  # noqa: ARG001 -- JS Tier 1 not in v2.0; see below
) -> list[CodeUnit]:
    """Walk the tree's top-level statements for React components.

    JS / TS Tier 1 extraction (one ``code_function`` per top-level
    function) doesn't exist yet, so a component's own node is
    constructed inline here. Phase 7.x or phase 4.x will fold these
    components into a shared JS Tier 1 path.
    """
    out: list[CodeUnit] = []
    seen_paths: set[tuple[str, str]] = set()
    for child in tree.root_node.children:
        comp = _component_from_node(child, source, file_path)
        if comp is not None:
            out.append(comp)
            # Walk this component's body for fetch() calls.
            for endpoint in _endpoints_from_function(child, source, comp):
                key = (endpoint.route_method or "", endpoint.route_path or "")
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                out.append(endpoint)
    return out


# --- Component detection -------------------------------------------------


def _component_from_node(node: tree_sitter.Node, source: bytes, file_path: str) -> CodeUnit | None:
    """If ``node`` is a top-level function whose name is PascalCase
    and whose body contains JSX, return a ``code_component`` unit.

    Handles two shapes:

    - ``function MyComp(props) { return <div/>; }`` ->
      ``function_declaration`` with a ``name`` field.
    - ``const MyComp = (props) => <div/>`` ->
      ``lexical_declaration`` with a ``variable_declarator``
      whose value is a ``arrow_function`` returning JSX.
    """
    if node.type == "function_declaration":
        return _component_from_function_declaration(node, source, file_path)
    if node.type in ("lexical_declaration", "variable_statement"):
        return _component_from_lexical_declaration(node, source, file_path)
    if node.type == "export_statement":
        # ``export function ...`` / ``export const ... = ...`` --
        # unwrap once and recurse for the underlying declaration.
        for child in node.children:
            if child.type in (
                "function_declaration",
                "lexical_declaration",
                "variable_statement",
            ):
                return _component_from_node(child, source, file_path)
    return None


def _component_from_function_declaration(
    fn_node: tree_sitter.Node, source: bytes, file_path: str
) -> CodeUnit | None:
    name_node = fn_node.child_by_field_name("name")
    if name_node is None:
        return None
    name = _text(name_node)
    if not _is_component_name(name):
        return None
    if not _contains_jsx(fn_node):
        return None
    return _build_component_unit(fn_node, source, file_path, name=name)


def _component_from_lexical_declaration(
    decl_node: tree_sitter.Node, source: bytes, file_path: str
) -> CodeUnit | None:
    """``const Foo = () => <div/>`` -> walk into the variable_declarator."""
    for child in decl_node.children:
        if child.type != "variable_declarator":
            continue
        name_node = child.child_by_field_name("name")
        value = child.child_by_field_name("value")
        if name_node is None or value is None:
            continue
        name = _text(name_node)
        if not _is_component_name(name):
            continue
        if value.type not in ("arrow_function", "function_expression", "function"):
            continue
        if not _contains_jsx(value):
            continue
        return _build_component_unit(decl_node, source, file_path, name=name)
    return None


def _is_component_name(name: str) -> bool:
    """PascalCase: starts with an uppercase ASCII letter. The React
    runtime uses the same check to distinguish components from
    plain function calls."""
    return bool(name) and name[0].isascii() and name[0].isupper()


def _contains_jsx(node: tree_sitter.Node) -> bool:
    """DFS through ``node`` looking for any JSX element. Stops at the
    first hit -- we only need a boolean."""
    if node.type in ("jsx_element", "jsx_self_closing_element"):
        return True
    return any(_contains_jsx(child) for child in node.children)


def _build_component_unit(
    node: tree_sitter.Node, source: bytes, file_path: str, *, name: str
) -> CodeUnit:
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    body_bytes = source[node.start_byte : node.end_byte]
    return CodeUnit(
        type="code_component",
        name=name,
        body=body_bytes.decode("utf-8", errors="replace")[:3000],
        source_path=f"{file_path}:{start_line}-{end_line}",
        description=f"React component {name}",
        hash=hashlib.sha256(body_bytes).hexdigest(),
        framework="react",
    )


# --- Endpoint detection (fetch calls) ------------------------------------


def _endpoints_from_function(
    fn_node: tree_sitter.Node, source: bytes, component: CodeUnit
) -> list[CodeUnit]:
    """Walk a component body collecting ``fetch("...")`` calls. Each
    produces a ``code_endpoint`` :class:`CodeUnit` whose source_path is
    the canonical ``endpoint:METHOD:path`` so callers across files
    converge on the same node when their paths match."""
    out: list[CodeUnit] = []
    for path in _walk_fetch_paths(fn_node, source):
        method = "GET"  # fetch() defaults to GET; refining requires
        # parsing the options object which is phase 7.x.
        endpoint = _endpoint_unit(method=method, path=path, parent=component)
        out.append(endpoint)
    return out


def _walk_fetch_paths(node: tree_sitter.Node, source: bytes) -> list[str]:
    """DFS for ``fetch(<str>, ...)`` call expressions; return the path
    arguments. Ignores ``fetch(variable)`` -- only string literals
    resolve cleanly at static-analysis time."""
    out: list[str] = []
    if node.type == "call_expression":
        fn = node.child_by_field_name("function")
        if fn is not None and _text(fn) == "fetch":
            args = node.child_by_field_name("arguments")
            if args is not None:
                for child in args.children:
                    if child.type == "string":
                        out.append(_unquote_js_string(child, source))
                        break
                    if child.type not in ("(", ")", ",", "comment"):
                        break
    for child in node.children:
        out.extend(_walk_fetch_paths(child, source))
    return out


def _endpoint_unit(*, method: str, path: str, parent: CodeUnit) -> CodeUnit:
    """Construct a code_endpoint unit. The endpoint's source_path uses
    the canonical ``endpoint:METHOD:path`` form so two emitters
    pointing at the same URL share the same node after dedupe."""
    sp = f"endpoint:{method}:{path}"
    return CodeUnit(
        type="code_endpoint",
        name=f"{method} {path}",
        body="",
        source_path=sp,
        description=f"Endpoint {method} {path}",
        hash=hashlib.sha256(sp.encode()).hexdigest(),
        route_method=method,
        route_path=path,
        # The parent_source_path is used by the ingest post-pass to
        # wire the ``at_endpoint`` edge from the component to this
        # endpoint -- a slight reuse of the field already used by
        # ``method_of``, but the post-pass disambiguates by the
        # caller node type.
        parent_source_path=parent.source_path,
    )


# --- Local helpers -------------------------------------------------------


def _text(node: tree_sitter.Node) -> str:
    text = getattr(node, "text", None)
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    if isinstance(text, str):
        return text
    return ""


def _unquote_js_string(node: tree_sitter.Node, source: bytes) -> str:
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
