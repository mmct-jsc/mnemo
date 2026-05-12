"""v2.0 phase 4: Tier 1 universal code extractor.

Walks a tree-sitter AST for a single source file and produces a list
of :class:`CodeUnit` records that the ingest pipeline turns into
``code_module`` / ``code_function`` / ``code_class`` / ``code_method``
graph nodes.

Output contract:

- ``units[0]`` is always a ``code_module`` unit representing the file
  itself. Module bodies are truncated to a head sample so retrieval
  hits don't blow the token budget on a 5,000-line file.
- Top-level declarations (``def`` / ``class`` at the file's top level
  for Python, equivalent for other languages) become
  ``code_function`` / ``code_class`` units.
- Class methods become ``code_method`` units; their
  ``parent_source_path`` points at the enclosing class. The post-pass
  in :mod:`mnemo.ingest` walks parent pointers to wire ``method_of``
  edges.

Edge intent is carried forward on the unit, not emitted directly:

- :attr:`CodeUnit.children_source_paths` (on the module) lists the
  top-level declarations the module ``defines``.
- :attr:`CodeUnit.parent_source_path` (on a method) names the class
  it ``method_of``.
- :attr:`CodeUnit.imports` (on the module) lists target module names
  the file imports. Cross-file resolution is the ingest post-pass's
  job and is best-effort -- unresolved targets simply don't get an
  edge.

Languages:

- **Python** has a complete extractor (top-level decls, class methods
  including decorated ones, docstring -> description, imports).
- **JavaScript / TypeScript / TSX / Go / JSON / YAML / Markdown** get
  a code_module-only fallback in phase 4. Later phases add per-
  language extractors that emit functions / classes / etc.

The extractor never raises on parser failures -- malformed source
is the user's code, not a configuration error, so the file still
yields a ``code_module`` node and we move on.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from mnemo.parsers import tree_sitter as ts_loader

if TYPE_CHECKING:  # pragma: no cover -- import-time only
    import tree_sitter


# Body truncation. Tuned to fit ~5 hits in a single 800-token retrieval
# without compressing the head past usefulness.
MODULE_HEAD_LINES = 60
FUNCTION_BODY_HEAD_LINES = 60


@dataclass
class CallSite:
    """v2.0 phase 5: a recorded call expression inside a function or
    method.

    The Tier 2 resolver consumes call sites to emit ``calls`` edges:

    - ``receiver=None``: free call, e.g. ``f()`` or ``Session()``. The
      resolver looks up the name in the enclosing module's scope, the
      file's imports, and as a class constructor.
    - ``receiver="self"`` (Python) / ``"this"`` (JS-TS): method call
      on the enclosing class. The resolver walks ``method_of`` edges
      to find the right method.
    - ``receiver=<other>``: qualified call, e.g. ``helper.f()``. The
      resolver tries to match the receiver against an imported
      module name first; falls back to attribute / instance shape
      heuristics in later phases.
    """

    callee_name: str
    receiver: str | None
    line: int  # 1-indexed for diagnostics


@dataclass
class CodeUnit:
    """One graph node's worth of extracted code.

    ``source_path`` is the cross-language join key. For modules it's
    the file path; for functions / classes / methods it's
    ``<file>:<start_line>-<end_line>`` so the IDE can jump to the
    exact range and so two same-name functions in the same file
    (overloads, conditionally-defined) get distinct keys.
    """

    type: str
    # "code_module" | "code_function" | "code_class" | "code_method"
    # | "code_route" (Tier 3)
    name: str
    body: str
    source_path: str
    description: str | None
    hash: str
    imports: list[str] = field(default_factory=list)
    children_source_paths: list[str] = field(default_factory=list)
    parent_source_path: str | None = None
    # v2.0 phase 5: call sites recorded inside this function / method.
    # Module-level units never populate this -- only enclosing
    # functions / methods do, since Tier 2 emits caller-function
    # ``calls`` edges (not "module calls function" edges).
    call_sites: list[CallSite] = field(default_factory=list)
    # v2.0 phase 6: Tier 3 backend framework routes carry their
    # handler pointer here so the reindex post-pass can wire a
    # ``routes_to`` edge by source_path lookup. ``framework``,
    # ``method``, ``path`` are framework-tagged metadata. All four
    # stay None on non-route units (the vast majority).
    framework: str | None = None
    route_method: str | None = None
    route_path: str | None = None
    handler_source_path: str | None = None


# Per-language declaration extractor. Receives the parsed tree + the
# raw source bytes + the file's normalized path (for source_path
# composition) and returns just the declarations (NOT the module unit
# -- :func:`extract` adds that as ``units[0]``).
LanguageExtractor = Callable[["tree_sitter.Tree", bytes, str], list[CodeUnit]]


# --- High-level dispatch --------------------------------------------------


def extract(path: Path, source: bytes, *, language: str) -> list[CodeUnit]:
    """Extract Tier 1 code units from ``source``.

    Always returns at least one element (the ``code_module`` unit).
    On parser failure, grammar unavailability, or unknown language,
    returns just the module node so the file stays queryable.

    v2.0 phase 6: after Tier 1 extraction, runs any registered
    Tier 3 framework extractors against the same tree and appends
    their output. The framework extractors get the Tier 1 unit list
    so they can thread ``handler_source_path`` pointers without
    re-walking the AST.
    """
    module = _module_unit(path, source)
    extractor = _LANGUAGE_EXTRACTORS.get(language)
    framework_extractors = _framework_extractors_for(language)

    if extractor is None and not framework_extractors:
        return [module]
    try:
        parser = ts_loader.get_parser(language)
    except ts_loader.GrammarNotAvailableError:
        return [module]
    try:
        tree = parser.parse(source)
    except Exception:  # noqa: BLE001 -- C-extension; be defensive
        return [module]

    declarations: list[CodeUnit] = []
    if extractor is not None:
        declarations = extractor(tree, source, module.source_path)
    module.children_source_paths = [
        u.source_path for u in declarations if u.type in ("code_function", "code_class")
    ]
    module.imports = _extract_imports(tree, language)

    framework_units: list[CodeUnit] = []
    for fx in framework_extractors:
        try:
            framework_units.extend(fx(tree, source, module.source_path, declarations))
        except Exception:  # noqa: BLE001 -- defensive: a broken extractor mustn't crash ingest
            continue
    return [module, *declarations, *framework_units]


def _framework_extractors_for(language: str) -> list[object]:
    """Late import to avoid the import cycle:
    ``parsers.code`` -> ``extractors`` -> ``parsers.code.CodeUnit``."""
    from mnemo.extractors import FRAMEWORK_EXTRACTORS

    return list(FRAMEWORK_EXTRACTORS.get(language, []))


# --- Module node ----------------------------------------------------------


def _module_unit(path: Path, source: bytes) -> CodeUnit:
    """Build the ``code_module`` unit for the whole file."""
    text = source.decode("utf-8", errors="replace")
    body = _truncate_lines(text, MODULE_HEAD_LINES)
    return CodeUnit(
        type="code_module",
        name=path.name,
        body=body,
        source_path=str(path).replace("\\", "/"),
        description=f"Module: {path.stem}",
        hash=_hash_bytes(source),
    )


# --- Python extractor -----------------------------------------------------


def _extract_python(tree: tree_sitter.Tree, source: bytes, file_path: str) -> list[CodeUnit]:
    """Top-level def / class + class methods. Decorated definitions
    are unwrapped to find the underlying function / class node."""
    units: list[CodeUnit] = []
    for child in tree.root_node.children:
        node = _unwrap_decorated(child)
        if node.type == "function_definition":
            units.append(_python_function_unit(node, source, file_path))
        elif node.type == "class_definition":
            cls_unit = _python_class_unit(node, source, file_path)
            units.append(cls_unit)
            units.extend(_python_methods(node, source, file_path, parent=cls_unit))
    return units


def _python_function_unit(
    node: tree_sitter.Node,
    source: bytes,
    file_path: str,
    *,
    is_method: bool = False,
) -> CodeUnit:
    name = _python_decl_name(node)
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    body_bytes = _slice_bytes(source, node)
    body = body_bytes.decode("utf-8", errors="replace")
    description = _python_docstring(node, source)
    return CodeUnit(
        type="code_method" if is_method else "code_function",
        name=name,
        body=_truncate_lines(body, FUNCTION_BODY_HEAD_LINES),
        source_path=f"{file_path}:{start_line}-{end_line}",
        description=description,
        hash=_hash_bytes(body_bytes),
        call_sites=_python_call_sites(node),
    )


def _python_class_unit(node: tree_sitter.Node, source: bytes, file_path: str) -> CodeUnit:
    name = _python_decl_name(node)
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    body_bytes = _slice_bytes(source, node)
    description = _python_docstring(node, source)
    return CodeUnit(
        type="code_class",
        name=name,
        body=_truncate_lines(body_bytes.decode("utf-8", errors="replace"), MODULE_HEAD_LINES),
        source_path=f"{file_path}:{start_line}-{end_line}",
        description=description,
        hash=_hash_bytes(body_bytes),
    )


def _python_methods(
    class_node: tree_sitter.Node,
    source: bytes,
    file_path: str,
    *,
    parent: CodeUnit,
) -> list[CodeUnit]:
    """Walk a class body, pulling out every ``function_definition``
    (including decorated ones). Each method's ``parent_source_path``
    points at the class so the ingest post-pass can wire
    ``method_of`` edges."""
    methods: list[CodeUnit] = []
    body = class_node.child_by_field_name("body")
    if body is None:
        return methods
    for child in body.children:
        node = _unwrap_decorated(child)
        if node.type != "function_definition":
            continue
        unit = _python_function_unit(node, source, file_path, is_method=True)
        unit.parent_source_path = parent.source_path
        methods.append(unit)
    return methods


def _python_decl_name(node: tree_sitter.Node) -> str:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return "<anonymous>"
    return _slice_text(name_node)


def _python_call_sites(fn_node: tree_sitter.Node) -> list[CallSite]:
    """Walk the function body collecting every ``call`` expression.

    Recursive: a call nested inside an ``if`` / ``for`` / ``with`` /
    list comprehension / etc. still attributes to the enclosing
    function (we don't follow into nested ``function_definition`` /
    ``class_definition`` -- those have their own units / their own
    call_sites).

    The Python grammar shapes a call as ``(call function: <expr>
    arguments: <args>)`` where ``<expr>`` is either an
    ``identifier`` (free call), an ``attribute`` (``a.b`` -- the
    receiver case), or some more complex expression (function
    factory, subscript, etc.). The latter case is silently ignored
    at Tier 2 -- the resolver can't usefully match against it.
    """
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []
    sites: list[CallSite] = []
    _collect_python_calls(body, sites)
    return sites


def _collect_python_calls(node: tree_sitter.Node, out: list[CallSite]) -> None:
    """DFS for ``call`` nodes; skip nested function / class definitions
    so they don't pollute the enclosing function's call_sites."""
    # Don't descend into nested function / class definitions -- those
    # are their own CodeUnits with their own call_sites.
    if node.type in ("function_definition", "class_definition", "decorated_definition"):
        return
    if node.type == "call":
        site = _python_call_site_from_node(node)
        if site is not None:
            out.append(site)
        # We still descend the call's children so nested calls
        # (e.g. ``f(g())``) get collected as well.
    for child in node.children:
        _collect_python_calls(child, out)


def _python_call_site_from_node(call: tree_sitter.Node) -> CallSite | None:
    """Read the function expression of a ``call`` node and produce a
    :class:`CallSite`. Returns None for shapes Tier 2 can't usefully
    resolve (calls on subscripts, complex factories, etc.)."""
    fn = call.child_by_field_name("function")
    if fn is None:
        return None
    line = call.start_point[0] + 1
    if fn.type == "identifier":
        return CallSite(callee_name=_slice_text(fn), receiver=None, line=line)
    if fn.type == "attribute":
        # ``a.b`` -- receiver is the object expression, callee is the
        # attribute name.
        receiver_node = fn.child_by_field_name("object")
        attr_node = fn.child_by_field_name("attribute")
        if attr_node is None:
            return None
        attr_name = _slice_text(attr_node)
        # Receiver can itself be a chain (``a.b.c.method()``); for
        # Tier 2 we only need the *outermost* receiver name. If the
        # receiver is a plain identifier (``self``, an imported
        # module, a class name) we capture it verbatim; otherwise
        # we leave it None so the resolver treats it as a free call
        # by name.
        receiver = _slice_text(receiver_node) if receiver_node is not None else None
        if receiver and "." in receiver:
            # Chain: keep just the first segment so e.g. ``a.b.c()``
            # gives receiver="a", which the resolver can still match
            # against imports.
            receiver = receiver.split(".", 1)[0]
        return CallSite(callee_name=attr_name, receiver=receiver, line=line)
    return None


def _python_docstring(node: tree_sitter.Node, source: bytes) -> str | None:
    """Pull the first triple-quoted string in the function / class
    body as the description. Returns None if there's no docstring."""
    body = node.child_by_field_name("body")
    if body is None or not body.children:
        return None
    first = body.children[0]
    # In the Python grammar, a docstring is an ``expression_statement``
    # wrapping a ``string`` literal as the first body element.
    if first.type == "expression_statement" and first.children:
        inner = first.children[0]
        if inner.type == "string":
            text = _slice_bytes(source, inner).decode("utf-8", errors="replace")
            return _clean_docstring(text)
    return None


def _clean_docstring(raw: str) -> str:
    """Strip surrounding quotes + leading whitespace and return the
    first paragraph collapsed to one line."""
    s = raw.strip()
    for q in ('"""', "'''", '"', "'"):
        if s.startswith(q) and s.endswith(q) and len(s) >= 2 * len(q):
            s = s[len(q) : -len(q)]
            break
    paragraph = s.strip().split("\n\n", 1)[0].strip()
    return " ".join(paragraph.split())


# --- Imports --------------------------------------------------------------


def _extract_imports(tree: tree_sitter.Tree, language: str) -> list[str]:
    """Pull import target names off the AST.

    Phase 4 only handles Python imports. Other bundled languages get
    an empty list -- phase 6+ framework extractors fill in the rest.
    """
    if language != "python":
        return []
    targets: list[str] = []
    for child in tree.root_node.children:
        if child.type == "import_statement":
            for name_node in child.children:
                if name_node.type == "dotted_name":
                    targets.append(_slice_text(name_node))
                elif name_node.type == "aliased_import":
                    inner = name_node.child_by_field_name("name")
                    if inner is not None:
                        targets.append(_slice_text(inner))
        elif child.type == "import_from_statement":
            module = child.child_by_field_name("module_name")
            if module is not None:
                targets.append(_slice_text(module))
    return targets


# --- Helpers --------------------------------------------------------------


def _unwrap_decorated(node: tree_sitter.Node) -> tree_sitter.Node:
    """``@decorator def f()`` wraps the function in a
    ``decorated_definition``. Return the inner function / class node
    so callers don't have to know about the wrapper."""
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return child
    return node


def _slice_bytes(source: bytes, node: tree_sitter.Node) -> bytes:
    return source[node.start_byte : node.end_byte]


def _slice_text(node: tree_sitter.Node) -> str:
    """Return the source text covered by a node.

    Uses ``node.text`` (set by tree-sitter 0.20+ when the parser was
    given bytes) and falls back to an empty string for synthetic
    nodes that don't carry text.
    """
    text = getattr(node, "text", None)
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    if isinstance(text, str):
        return text
    return ""


def _truncate_lines(text: str, head_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= head_lines:
        return text
    head = lines[:head_lines]
    remaining = len(lines) - head_lines
    return "\n".join(head) + f"\n... ({remaining} more lines)"


def _hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --- Language extractor registry ------------------------------------------


_LANGUAGE_EXTRACTORS: dict[str, LanguageExtractor] = {
    "python": _extract_python,
}
"""Languages with a structural extractor. Others fall back to the
module-only path in :func:`extract`. Phase 4 ships Python; later
phases add JS/TS/Go etc."""
