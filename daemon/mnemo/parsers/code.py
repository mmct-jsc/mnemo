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
import json
import re
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

# v5.28.0: the legacy line-range suffix the builders still emit
# internally (``<file>:<start>-<end>`` with an optional ``#method``
# route tiebreaker). :func:`_stabilize_keys` parses it to recover the
# line range, then rewrites the key to the line-stable form.
_LINE_RANGE_SUFFIX = re.compile(r":(\d+)-(\d+)(?:#.*)?$")


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
    # v5.28.0: the declaration's line range, preserved as metadata after
    # the identity key stopped encoding it (see SS_stabilize_keys). 0/0
    # for the module unit and any unit whose key never carried a range.
    line_start: int = 0
    line_end: int = 0


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
    all_units = [module, *declarations, *framework_units]
    # v5.28.0: rewrite line-range keys to the line-stable identity form
    # in one place, AFTER every builder + framework extractor has run
    # (so the framework extractors' line-range handler matching still
    # works on the keys they expect). See :func:`_stabilize_keys`.
    _stabilize_keys(module.source_path, all_units)
    return all_units


def _qualified_name(unit: CodeUnit, by_old_key: dict[str, CodeUnit]) -> str:
    """The within-file qualifier for a declaration's stable key.

    - method -> ``<ClassName>.<method>`` (the parent class is looked up
      by its still-legacy ``parent_source_path``; falls back to the bare
      method name if the parent isn't a unit in this file).
    - function / class -> the declaration name.
    - route / anything else -> the unit's display name (routes name
      themselves ``"<METHOD> <path>"``, which is line-stable; the legacy
      line+method tiebreaker is dropped).
    """
    if unit.type == "code_method" and unit.parent_source_path:
        parent = by_old_key.get(unit.parent_source_path)
        if parent is not None:
            return f"{parent.name}.{unit.name}"
    return unit.name


def _stabilize_keys(file_key: str, units: list[CodeUnit]) -> None:
    """Rewrite legacy ``<file>:<start>-<end>`` keys to the line-stable
    ``<file>::<qualified_name>`` form, in place, and remap the
    cross-reference fields (``parent_source_path`` /
    ``children_source_paths`` / ``handler_source_path``) through the
    same old->new mapping.

    Only units whose current ``source_path`` carries the legacy
    line-range suffix are re-keyed; already-stable forms (the module's
    bare file path, ``endpoint:METHOD:path``) are left untouched. The
    parsed line range is preserved on each re-keyed unit as
    ``line_start`` / ``line_end``. Collisions on the same qualified name
    within one file (overloads, redefinitions, repeated ``<anonymous>``)
    get a document-order ordinal suffix ``#2``, ``#3``, ... -- the first
    occurrence stays unsuffixed for stability.
    """
    by_old_key = {u.source_path: u for u in units}
    remap: dict[str, str] = {}
    used: dict[str, int] = {}
    for u in units:
        m = _LINE_RANGE_SUFFIX.search(u.source_path)
        if m is None:
            continue  # module / endpoint / already-stable key
        u.line_start = int(m.group(1))
        u.line_end = int(m.group(2))
        base = f"{file_key}::{_qualified_name(u, by_old_key)}"
        n = used.get(base, 0) + 1
        used[base] = n
        remap[u.source_path] = base if n == 1 else f"{base}#{n}"
    if not remap:
        return
    for u in units:
        u.source_path = remap.get(u.source_path, u.source_path)
        if u.parent_source_path is not None:
            u.parent_source_path = remap.get(u.parent_source_path, u.parent_source_path)
        if u.handler_source_path is not None:
            u.handler_source_path = remap.get(u.handler_source_path, u.handler_source_path)
        if u.children_source_paths:
            u.children_source_paths = [remap.get(c, c) for c in u.children_source_paths]


def code_file_and_range(
    source_path: str, frontmatter_json: str | None = None
) -> tuple[str, tuple[int, int] | None]:
    """Split a stored code node's identity into ``(file_path, line_range)``.

    v5.28.0: the stable key is ``<file>::<qualified_name>`` and the line
    range lives in ``frontmatter_json['code_unit']['line_start'/'line_end']``.
    Pre-migration nodes still carry the legacy ``<file>:<start>-<end>``
    form, so the line-range suffix is parsed as a fallback. Returns
    ``(file_path, (start, end))`` or ``(file_path, None)`` when no range
    is recoverable (e.g. a module node). This is the one helper every
    stored-node consumer (git-log overlap, the full_source endpoint)
    should use so the key format lives in exactly one place.
    """
    sep = source_path.find("::")
    if sep != -1:
        file_path = source_path[:sep]
    else:
        m = _LINE_RANGE_SUFFIX.search(source_path)
        file_path = source_path[: m.start()] if m else source_path
    if frontmatter_json:
        try:
            cu = json.loads(frontmatter_json).get("code_unit")
        except (ValueError, AttributeError):
            cu = None
        if isinstance(cu, dict):
            ls, le = cu.get("line_start"), cu.get("line_end")
            if isinstance(ls, int) and isinstance(le, int) and ls > 0 and le > 0:
                return file_path, (ls, le)
    m = _LINE_RANGE_SUFFIX.search(source_path)
    if m is not None:
        return file_path, (int(m.group(1)), int(m.group(2)))
    return file_path, None


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

    Phase 4 wired Python; v2.5.0 adds JavaScript / TypeScript / TSX.
    Other bundled languages still get an empty list -- their
    extractors land in v2.5.1+ (Go) and beyond.
    """
    if language == "python":
        return _extract_python_imports(tree)
    if language in ("javascript", "typescript", "tsx"):
        return _extract_jsts_imports(tree)
    if language == "go":
        return _extract_go_imports(tree)
    return []


def _extract_go_imports(tree: tree_sitter.Tree) -> list[str]:
    """Go ``import`` declarations -- both single and grouped.

    Recognized shapes:
      - ``import "fmt"``                         -> ``fmt``
      - ``import alias "pkg/path"``              -> ``pkg/path``
        (we record the path, not the alias)
      - ``import ( "fmt"; "os"; ... )``          -> each path
      - dot ``import . "pkg"`` / blank ``import _ "pkg"``
                                                 -> the path
    """
    targets: list[str] = []
    for child in tree.root_node.children:
        if child.type != "import_declaration":
            continue
        # Single import: import_declaration -> import_spec.
        # Grouped:       import_declaration -> import_spec_list -> N import_spec.
        _go_collect_import_specs(child, targets)
    return targets


def _go_collect_import_specs(node: tree_sitter.Node, targets: list[str]) -> None:
    """DFS for ``import_spec`` nodes under an ``import_declaration``
    or its inner ``import_spec_list``."""
    if node.type == "import_spec":
        path_node = node.child_by_field_name("path")
        if path_node is not None:
            targets.append(_unquote_jsts_string(_slice_text(path_node)))
        return
    for child in node.children:
        _go_collect_import_specs(child, targets)


def _extract_python_imports(tree: tree_sitter.Tree) -> list[str]:
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


def _extract_jsts_imports(tree: tree_sitter.Tree) -> list[str]:
    """JS / TS ``import`` statements.

    Recognized shapes:
      - ``import x from 'mod'``                -> ``mod``
      - ``import { a, b } from 'mod'``         -> ``mod``
      - ``import * as ns from 'mod'``          -> ``mod``
      - ``import 'mod'``  (side-effect import) -> ``mod``
      - dynamic ``import('mod')``              -> not currently captured
      - ``require('mod')``                     -> not currently captured

    The ``source`` (the module string) is what we want, NOT the
    imported symbols -- the importable target is the module.
    """
    targets: list[str] = []
    for child in tree.root_node.children:
        if child.type != "import_statement":
            continue
        source = child.child_by_field_name("source")
        if source is None:
            # tree-sitter-javascript exposes the string as a direct
            # child rather than a named field in some versions; fall
            # back to scanning children.
            for c in child.children:
                if c.type == "string":
                    source = c
                    break
        if source is None:
            continue
        targets.append(_unquote_jsts_string(_slice_text(source)))
    return targets


def _unquote_jsts_string(raw: str) -> str:
    """Strip surrounding single / double / backtick quotes from a JS
    string literal. The tree-sitter string node value INCLUDES the
    quotes."""
    s = raw.strip()
    for q in ('"', "'", "`"):
        if len(s) >= 2 and s.startswith(q) and s.endswith(q):
            return s[1:-1]
    return s


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
    """Return the first ``head_lines`` lines of ``text``.

    v2.0 originally appended a ``... (N more lines)`` marker to signal
    truncation. v2.1 drops that marker so the stored body is clean
    source code -- embeddings, query hits, and any future chat layer
    see only the actual lines, not a human-readable note that could
    leak into LLM output. The UI surfaces "X of Y lines stored" via
    separate metadata if it needs the affordance; the canonical
    "full content" path is the ``GET /v1/nodes/<id>/full_source``
    endpoint which re-reads the file from disk.
    """
    lines = text.splitlines()
    if len(lines) <= head_lines:
        return text
    return "\n".join(lines[:head_lines])


def _hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --- Language extractor registry ------------------------------------------


_LANGUAGE_EXTRACTORS: dict[str, LanguageExtractor] = {
    "python": _extract_python,
}
"""Languages with a structural extractor. Others fall back to the
module-only path in :func:`extract`. Phase 4 ships Python; v2.5.0
adds JavaScript + TypeScript (registered after their extractor
helpers are defined below); Go follows in v2.5.1."""


# --- JavaScript / TypeScript extractor (v2.5.0) --------------------------

# Tree-sitter-javascript + tree-sitter-typescript share most of the
# AST shape we care about (function_declaration, class_declaration,
# method_definition, import_statement, call_expression, ...). The
# TS grammar adds parameter / return type annotations that we
# silently ignore, plus interface / type_alias / enum nodes that
# we leave for a future cut.

_JSTS_DECLARATION_TYPES = frozenset(
    {
        "function_declaration",
        "class_declaration",
        # Arrow / function-expression assignments live under a
        # ``lexical_declaration`` (const / let) or
        # ``variable_declaration`` (var) wrapper.
        "lexical_declaration",
        "variable_declaration",
        # TS interface / type alias / enum could land here later;
        # the current extractor focuses on runtime declarations.
    }
)


def _extract_jsts(tree: tree_sitter.Tree, source: bytes, file_path: str) -> list[CodeUnit]:
    """v2.5.0 Tier 1 for JavaScript + TypeScript.

    Walks the module's top-level statements and emits one CodeUnit
    per function / class / arrow-function-assignment / method. Each
    function / method records its call_sites for the Tier 2
    resolver to consume.
    """
    units: list[CodeUnit] = []
    for child in tree.root_node.children:
        # ``export`` wraps the inner declaration; unwrap so we treat
        # ``export function foo() {}`` like ``function foo() {}``.
        node = _jsts_unwrap_export(child)
        if node.type == "function_declaration":
            units.append(_jsts_function_unit(node, source, file_path))
        elif node.type == "class_declaration":
            cls_unit = _jsts_class_unit(node, source, file_path)
            units.append(cls_unit)
            units.extend(_jsts_methods(node, source, file_path, parent=cls_unit))
        elif node.type in ("lexical_declaration", "variable_declaration"):
            # ``const f = () => {}`` -- pull the arrow function out
            # so it counts as a top-level function declaration.
            units.extend(_jsts_arrow_function_units(node, source, file_path))
    return units


def _jsts_unwrap_export(node: tree_sitter.Node) -> tree_sitter.Node:
    """``export function foo() {}`` wraps the function in an
    ``export_statement``. Return the inner declaration so callers
    don't have to know about the wrapper."""
    if node.type != "export_statement":
        return node
    for child in node.children:
        if child.type in _JSTS_DECLARATION_TYPES:
            return child
    return node


def _jsts_function_unit(
    node: tree_sitter.Node,
    source: bytes,
    file_path: str,
    *,
    is_method: bool = False,
    name_override: str | None = None,
) -> CodeUnit:
    name = name_override or _jsts_decl_name(node)
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    body_bytes = _slice_bytes(source, node)
    body = body_bytes.decode("utf-8", errors="replace")
    return CodeUnit(
        type="code_method" if is_method else "code_function",
        name=name,
        body=_truncate_lines(body, FUNCTION_BODY_HEAD_LINES),
        source_path=f"{file_path}:{start_line}-{end_line}",
        description=None,
        hash=_hash_bytes(body_bytes),
        call_sites=_jsts_call_sites(node),
    )


def _jsts_class_unit(node: tree_sitter.Node, source: bytes, file_path: str) -> CodeUnit:
    name = _jsts_decl_name(node)
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    body_bytes = _slice_bytes(source, node)
    return CodeUnit(
        type="code_class",
        name=name,
        body=_truncate_lines(body_bytes.decode("utf-8", errors="replace"), MODULE_HEAD_LINES),
        source_path=f"{file_path}:{start_line}-{end_line}",
        description=None,
        hash=_hash_bytes(body_bytes),
    )


def _jsts_methods(
    class_node: tree_sitter.Node,
    source: bytes,
    file_path: str,
    *,
    parent: CodeUnit,
) -> list[CodeUnit]:
    """Walk a class body, pulling out every ``method_definition``."""
    methods: list[CodeUnit] = []
    body = class_node.child_by_field_name("body")
    if body is None:
        return methods
    for child in body.children:
        if child.type != "method_definition":
            continue
        unit = _jsts_function_unit(child, source, file_path, is_method=True)
        unit.parent_source_path = parent.source_path
        methods.append(unit)
    return methods


def _jsts_arrow_function_units(
    decl: tree_sitter.Node, source: bytes, file_path: str
) -> list[CodeUnit]:
    """``const x = () => {}`` / ``let x = function() {}`` -- pull
    each variable declarator whose value is a function / arrow
    function and emit a ``code_function`` named after the variable.
    """
    out: list[CodeUnit] = []
    for child in decl.children:
        if child.type != "variable_declarator":
            continue
        name_node = child.child_by_field_name("name")
        value_node = child.child_by_field_name("value")
        if name_node is None or value_node is None:
            continue
        if value_node.type not in ("arrow_function", "function_expression", "function"):
            continue
        name = _slice_text(name_node)
        unit = _jsts_function_unit(value_node, source, file_path, name_override=name)
        out.append(unit)
    return out


def _jsts_decl_name(node: tree_sitter.Node) -> str:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return "<anonymous>"
    return _slice_text(name_node)


def _jsts_call_sites(fn_node: tree_sitter.Node) -> list[CallSite]:
    """Walk a function / method body collecting every
    ``call_expression`` (Tier 2's raw input)."""
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []
    sites: list[CallSite] = []
    _collect_jsts_calls(body, sites)
    return sites


def _collect_jsts_calls(node: tree_sitter.Node, out: list[CallSite]) -> None:
    """DFS for ``call_expression`` nodes; skip nested function /
    class definitions so they don't pollute the enclosing function's
    call_sites."""
    if node.type in (
        "function_declaration",
        "class_declaration",
        "method_definition",
        "arrow_function",
        "function_expression",
    ):
        return
    if node.type == "call_expression":
        site = _jsts_call_site_from_node(node)
        if site is not None:
            out.append(site)
    for child in node.children:
        _collect_jsts_calls(child, out)


def _jsts_call_site_from_node(call: tree_sitter.Node) -> CallSite | None:
    """Read the function expression of a ``call_expression`` node
    (JS / TS) and produce a :class:`CallSite`. JS expresses a
    bare call as ``identifier`` and a member call as
    ``member_expression`` (the JS analogue of Python's
    ``attribute``)."""
    fn = call.child_by_field_name("function")
    if fn is None:
        return None
    line = call.start_point[0] + 1
    if fn.type == "identifier":
        return CallSite(callee_name=_slice_text(fn), receiver=None, line=line)
    if fn.type == "member_expression":
        receiver_node = fn.child_by_field_name("object")
        property_node = fn.child_by_field_name("property")
        if property_node is None:
            return None
        callee_name = _slice_text(property_node)
        receiver = _slice_text(receiver_node) if receiver_node is not None else None
        if receiver and "." in receiver:
            receiver = receiver.split(".", 1)[0]
        return CallSite(callee_name=callee_name, receiver=receiver, line=line)
    return None


# Register JS + TS extractors. TypeScript shares the JS shape -- TS-
# only nodes (type annotations, interface_declaration, etc.) are
# silently ignored by the walk above.
_LANGUAGE_EXTRACTORS["javascript"] = _extract_jsts
_LANGUAGE_EXTRACTORS["typescript"] = _extract_jsts
# TSX shares the TypeScript grammar (with JSX nodes layered on top).
# We register the same extractor so a .tsx file gets its function /
# class declarations indexed alongside React extractor output.
_LANGUAGE_EXTRACTORS["tsx"] = _extract_jsts


# --- Go extractor (v2.5.1) -----------------------------------------------

# Tree-sitter-go AST node names for the shapes we care about:
#   - ``function_declaration``         func foo() {}
#   - ``method_declaration``           func (r *T) m() {} -- has a
#                                       ``receiver`` field
#   - ``type_declaration``             wraps one or more ``type_spec``
#   - ``type_spec``                    has ``name`` + the underlying
#                                       type expression
#   - ``struct_type`` / ``interface_type``
#                                       the type shapes we map onto
#                                       ``code_class``
#   - ``import_declaration``           wraps single or grouped imports
#   - ``import_spec``                  has ``path`` (string literal)
#                                       + optional alias ``name``
#   - ``call_expression``              has ``function`` + ``arguments``
#   - ``selector_expression``          ``a.b`` -- has ``operand`` +
#                                       ``field``


def _extract_go(tree: tree_sitter.Tree, source: bytes, file_path: str) -> list[CodeUnit]:
    """v2.5.1 Tier 1 for Go.

    Go has no classes; structs + their receiver-methods are the
    natural analogue. We map ``type Foo struct { ... }`` and
    ``type Foo interface { ... }`` to ``code_class``; methods on a
    receiver type to ``code_method`` parented at the receiver
    type's ``code_class`` source_path.
    """
    units: list[CodeUnit] = []
    # First pass: collect type declarations so receiver-methods
    # can later look up their parent's source_path.
    type_units_by_name: dict[str, CodeUnit] = {}
    for child in tree.root_node.children:
        if child.type == "function_declaration":
            units.append(_go_function_unit(child, source, file_path))
        elif child.type == "type_declaration":
            for type_unit in _go_type_units(child, source, file_path):
                units.append(type_unit)
                type_units_by_name[type_unit.name] = type_unit
        elif child.type == "method_declaration":
            # Deferred to second pass once type_units_by_name is full.
            pass

    # Second pass: method_declaration nodes can appear before OR
    # after their receiver type's type_declaration in the file, so
    # we run a separate sweep and attach each method to its parent
    # via the index built above.
    for child in tree.root_node.children:
        if child.type == "method_declaration":
            unit = _go_method_unit(child, source, file_path, type_units_by_name)
            if unit is not None:
                units.append(unit)
    return units


def _go_function_unit(node: tree_sitter.Node, source: bytes, file_path: str) -> CodeUnit:
    name = _go_decl_name(node)
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    body_bytes = _slice_bytes(source, node)
    return CodeUnit(
        type="code_function",
        name=name,
        body=_truncate_lines(
            body_bytes.decode("utf-8", errors="replace"), FUNCTION_BODY_HEAD_LINES
        ),
        source_path=f"{file_path}:{start_line}-{end_line}",
        description=None,
        hash=_hash_bytes(body_bytes),
        call_sites=_go_call_sites(node),
    )


def _go_type_units(type_decl: tree_sitter.Node, source: bytes, file_path: str) -> list[CodeUnit]:
    """A single ``type_declaration`` can contain multiple type_specs
    (``type ( Foo struct{}; Bar interface{} )``). Yield one
    ``code_class`` per spec whose underlying type is a struct or
    interface.
    """
    out: list[CodeUnit] = []
    for child in type_decl.children:
        if child.type != "type_spec":
            continue
        name_node = child.child_by_field_name("name")
        type_node = child.child_by_field_name("type")
        if name_node is None or type_node is None:
            continue
        # We classify struct + interface as the class-analogue.
        # Other type aliases (``type Foo int``) aren't class-shaped
        # and stay out of the graph at Tier 1.
        if type_node.type not in ("struct_type", "interface_type"):
            continue
        name = _slice_text(name_node)
        start_line = child.start_point[0] + 1
        end_line = child.end_point[0] + 1
        body_bytes = _slice_bytes(source, child)
        out.append(
            CodeUnit(
                type="code_class",
                name=name,
                body=_truncate_lines(
                    body_bytes.decode("utf-8", errors="replace"), MODULE_HEAD_LINES
                ),
                source_path=f"{file_path}:{start_line}-{end_line}",
                description=None,
                hash=_hash_bytes(body_bytes),
            )
        )
    return out


def _go_method_unit(
    node: tree_sitter.Node,
    source: bytes,
    file_path: str,
    type_units_by_name: dict[str, CodeUnit],
) -> CodeUnit | None:
    name = _go_decl_name(node)
    receiver_type = _go_receiver_type(node)
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    body_bytes = _slice_bytes(source, node)
    unit = CodeUnit(
        type="code_method",
        name=name,
        body=_truncate_lines(
            body_bytes.decode("utf-8", errors="replace"), FUNCTION_BODY_HEAD_LINES
        ),
        source_path=f"{file_path}:{start_line}-{end_line}",
        description=None,
        hash=_hash_bytes(body_bytes),
        call_sites=_go_call_sites(node),
    )
    # Wire the parent only when we resolved the receiver type to a
    # type_spec in the same file. Cross-file resolution happens at
    # the post-pass layer same as JS / Python.
    if receiver_type and receiver_type in type_units_by_name:
        unit.parent_source_path = type_units_by_name[receiver_type].source_path
    return unit


def _go_receiver_type(method: tree_sitter.Node) -> str | None:
    """Pull the type name from a method_declaration's receiver
    field. Strips a leading ``*`` for pointer receivers."""
    receiver = method.child_by_field_name("receiver")
    if receiver is None:
        return None
    # receiver is a parameter_list with one parameter_declaration.
    for child in receiver.children:
        if child.type != "parameter_declaration":
            continue
        type_node = child.child_by_field_name("type")
        if type_node is None:
            continue
        if type_node.type == "pointer_type":
            # *Receiver -- the inner type is the qualified type.
            for inner in type_node.children:
                if inner.type == "type_identifier":
                    return _slice_text(inner)
        if type_node.type == "type_identifier":
            return _slice_text(type_node)
    return None


def _go_decl_name(node: tree_sitter.Node) -> str:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return "<anonymous>"
    return _slice_text(name_node)


def _go_call_sites(fn_node: tree_sitter.Node) -> list[CallSite]:
    """Walk a function / method body collecting every
    ``call_expression``."""
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []
    sites: list[CallSite] = []
    _collect_go_calls(body, sites)
    return sites


def _collect_go_calls(node: tree_sitter.Node, out: list[CallSite]) -> None:
    """DFS for ``call_expression`` nodes; skip nested function
    declarations / func literals so they don't pollute the
    enclosing function's call_sites."""
    if node.type in ("function_declaration", "method_declaration", "func_literal"):
        return
    if node.type == "call_expression":
        site = _go_call_site_from_node(node)
        if site is not None:
            out.append(site)
    for child in node.children:
        _collect_go_calls(child, out)


def _go_call_site_from_node(call: tree_sitter.Node) -> CallSite | None:
    """Read the function expression of a ``call_expression`` node
    (Go). Bare call -> ``identifier``; package / receiver call ->
    ``selector_expression``."""
    fn = call.child_by_field_name("function")
    if fn is None:
        return None
    line = call.start_point[0] + 1
    if fn.type == "identifier":
        return CallSite(callee_name=_slice_text(fn), receiver=None, line=line)
    if fn.type == "selector_expression":
        operand = fn.child_by_field_name("operand")
        field = fn.child_by_field_name("field")
        if field is None:
            return None
        callee_name = _slice_text(field)
        receiver = _slice_text(operand) if operand is not None else None
        if receiver and "." in receiver:
            receiver = receiver.split(".", 1)[0]
        return CallSite(callee_name=callee_name, receiver=receiver, line=line)
    return None


_LANGUAGE_EXTRACTORS["go"] = _extract_go
