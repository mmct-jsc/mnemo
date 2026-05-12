"""v2.0 phase 5: Tier 2 call-graph scope resolver.

Consumes the call-site intent recorded on code-function / code-method
nodes (carried through ``frontmatter_json['code_unit']['call_sites']``
by the ingest pipeline) and emits ``calls`` edges by resolving each
call site against the freshly-populated Tier 1 graph.

Resolution rules, in decreasing confidence:

1. **Same-class method** (``receiver == "self"`` / ``"this"`` /
   ``"cls"``): the caller is a ``code_method``; walk its
   ``method_of`` edge to the enclosing class; pick the
   sibling method with the matching name. Confidence: 0.95.

2. **Same-module name** (no receiver, callee defined in the same
   file): match against the module's ``defines`` children
   (functions + classes -- a ``Session()`` call resolves to the
   class node, used as a constructor stand-in). Confidence: 0.95.

3. **Cross-file via imports** (``receiver`` matches an imported
   module name): walk the caller's enclosing module ``imports``
   edge to the target module; match the callee name against the
   target's ``defines`` children. Confidence: 0.8 (lower because
   the receiver is a name match, not a typed lookup).

4. **Unresolved**: no edge. Tier 2 stays best-effort by design --
   spurious edges hurt retrieval more than missing ones.

The resolver is invoked from :mod:`mnemo.ingest` after the Tier 1
edges (``defines`` / ``method_of`` / ``imports``) are wired, so the
lookups above hit the freshly-populated graph.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from mnemo.store import Node, Store

log = logging.getLogger(__name__)


# Confidence levels mirror the design's edge-uncertainty taxonomy.
SAME_FILE_CONFIDENCE = 0.95
"""Within-file resolution (free call or self.method)."""

CROSS_FILE_CONFIDENCE = 0.8
"""Cross-file resolution via an imports edge."""


# Receivers that mean "method of my enclosing class".
SELF_LIKE_RECEIVERS = frozenset({"self", "this", "cls"})


@dataclass
class _ResolutionIndex:
    """Snapshot of the graph state needed for one resolve_calls run.

    Built once at the top of :func:`resolve_calls` so each call site
    lookup is O(1) rather than re-querying the store.
    """

    # source_path -> Node for every code node currently in the store.
    by_source_path: dict[str, Node]
    # (module_source_path, declaration_name) -> Node. The "module"
    # join key is the bare file path (no line range); declarations
    # match by display name. Used for same-module and cross-file
    # lookups.
    by_module_and_name: dict[tuple[str, str], Node]
    # class_source_path -> list of code_method nodes whose
    # method_of points at it. Used for self.method() lookups.
    methods_by_class: dict[str, list[Node]]
    # caller-method's source_path -> class node id (its
    # method_of target). One-hop precompute.
    class_of_method: dict[str, Node]
    # module_source_path -> list of (target_module_name, target_module_node).
    # Built from ``imports`` edges + the module-stem index used by the
    # Tier 1 imports resolver, but expanded so the call resolver can
    # match a receiver name like ``helper`` to the helper.py module
    # without re-parsing imports here.
    imported_module_by_name: dict[tuple[str, str], Node]


def resolve_calls(store: Store, code_node_ids: list[str]) -> int:
    """Emit ``calls`` edges for the given code node ids.

    ``code_node_ids`` is the post-pass batch from
    :func:`mnemo.ingest.reindex` -- the set of code nodes just
    touched this run. Returns the number of edges created (mostly
    useful for tests / diagnostics).

    The resolver reads ``call_sites`` off each node's frontmatter,
    looks up the callee, and emits the edge. Existing edges are
    overwritten in place by ``store.add_edge`` (ON CONFLICT UPDATE).
    """
    if not code_node_ids:
        return 0

    index = _build_index(store)
    edges_created = 0

    for nid in code_node_ids:
        node = store.get_node(nid)
        if node is None or not node.frontmatter_json:
            continue
        # Only functions / methods carry call_sites; modules and
        # classes don't have a body the resolver can scan.
        if node.type not in ("code_function", "code_method"):
            continue
        sites = _call_sites_from_frontmatter(node.frontmatter_json)
        if not sites:
            continue
        caller_module_path = _module_path_of(node.source_path)
        caller_class = index.class_of_method.get(node.source_path)

        for site in sites:
            target = _resolve_one(site, node, caller_module_path, caller_class, index)
            if target is None:
                continue
            target_node, confidence = target
            if target_node.id == node.id:
                # Don't emit self-edges. A recursive function's
                # call site resolves to itself by name; we suppress
                # the edge so a single-node cycle doesn't pollute
                # the graph (retrieval algorithms generally treat
                # self-edges as noise).
                continue
            store.add_edge(node.id, target_node.id, "calls", confidence=confidence)
            edges_created += 1

    return edges_created


def _resolve_one(
    site: dict[str, object],
    caller: Node,
    caller_module_path: str,
    caller_class: Node | None,
    index: _ResolutionIndex,
) -> tuple[Node, float] | None:
    """Apply the resolution rules in order. Returns (target, confidence)
    or None if unresolved."""
    callee_name = site.get("callee")
    receiver = site.get("receiver")
    if not isinstance(callee_name, str) or not callee_name:
        return None

    # Rule 1: same-class method via self/this/cls.
    if isinstance(receiver, str) and receiver in SELF_LIKE_RECEIVERS:
        if caller_class is None:
            return None
        for method in index.methods_by_class.get(caller_class.source_path, []):
            if method.name == callee_name:
                return method, SAME_FILE_CONFIDENCE
        return None

    # Rule 2: same-module free call (no receiver).
    if receiver is None:
        hit = index.by_module_and_name.get((caller_module_path, callee_name))
        if hit is not None and hit.type in ("code_function", "code_class"):
            return hit, SAME_FILE_CONFIDENCE
        return None

    # Rule 3: cross-file via imports. ``receiver`` names an imported
    # module; find that module and match the callee name inside it.
    if isinstance(receiver, str):
        target_module = index.imported_module_by_name.get((caller_module_path, receiver))
        if target_module is None:
            return None
        hit = index.by_module_and_name.get((target_module.source_path, callee_name))
        if hit is not None and hit.type in ("code_function", "code_class"):
            return hit, CROSS_FILE_CONFIDENCE
        return None

    return None


# --- Index construction ---------------------------------------------------


def _build_index(store: Store) -> _ResolutionIndex:
    """Build the lookup tables used by :func:`resolve_calls`.

    Builds in one read pass over the code-typed nodes + the
    ``method_of`` / ``imports`` edges -- cheap relative to the
    per-call-site lookup cost it saves.
    """
    by_source_path: dict[str, Node] = {}
    by_module_and_name: dict[tuple[str, str], Node] = {}
    methods_by_class: dict[str, list[Node]] = {}
    class_of_method: dict[str, Node] = {}
    imported_module_by_name: dict[tuple[str, str], Node] = {}

    for ct in ("code_module", "code_function", "code_class", "code_method"):
        for n in store.list_nodes(type=ct, limit=1_000_000):
            by_source_path[n.source_path] = n
            module_path = _module_path_of(n.source_path)
            # Declarations join under their containing module's path.
            # Modules join under themselves so a ``Foo`` class can be
            # looked up via (module_path, "Foo") and the module via
            # (module_path, module_stem).
            if ct == "code_module":
                stem = Path(n.source_path).stem
                by_module_and_name[(n.source_path, stem)] = n
            else:
                by_module_and_name[(module_path, n.name)] = n

    # method_of: method's source_path -> class node.
    method_nodes = [n for n in by_source_path.values() if n.type == "code_method"]
    for method in method_nodes:
        edges = store.get_edges(src_id=method.id, relation="method_of")
        for edge in edges:
            cls = by_source_path.get(_source_path_of_node(store, edge.dst_id))
            if cls is None:
                continue
            class_of_method[method.source_path] = cls
            methods_by_class.setdefault(cls.source_path, []).append(method)
            break  # one method_of per method

    # imports: for each module, get its imports edges and index the
    # *target module*'s stem -> the target node. The receiver name a
    # call site carries is the imported name (e.g. ``import helper``
    # leaves "helper" as the receiver of ``helper.f()``); the target
    # module's file stem is what we index.
    module_nodes = [n for n in by_source_path.values() if n.type == "code_module"]
    for module in module_nodes:
        edges = store.get_edges(src_id=module.id, relation="imports")
        for edge in edges:
            target_sp = _source_path_of_node(store, edge.dst_id)
            target = by_source_path.get(target_sp) if target_sp else None
            if target is None or target.type != "code_module":
                continue
            stem = Path(target.source_path).stem
            imported_module_by_name[(module.source_path, stem)] = target

    return _ResolutionIndex(
        by_source_path=by_source_path,
        by_module_and_name=by_module_and_name,
        methods_by_class=methods_by_class,
        class_of_method=class_of_method,
        imported_module_by_name=imported_module_by_name,
    )


# --- Helpers --------------------------------------------------------------


def _module_path_of(source_path: str) -> str:
    """Strip the v2.0 ``:<line>-<line>`` declaration suffix to get the
    file path. For module nodes this is a no-op."""
    if ":" not in source_path:
        return source_path
    head, _, tail = source_path.rpartition(":")
    # Detect the ``<digits>-<digits>`` shape -- if it doesn't match,
    # the colon belongs to something else (Windows drive letter,
    # an embedded ``:`` in the name) so we leave the path alone.
    if "-" in tail:
        a, _, b = tail.partition("-")
        if a.isdigit() and b.isdigit():
            return head
    return source_path


def _call_sites_from_frontmatter(fm_json: str) -> list[dict[str, object]]:
    """Read ``frontmatter_json['code_unit']['call_sites']``, returning
    [] on any shape mismatch (the caller is defensive against external
    edits / hand-written frontmatter)."""
    try:
        fm = json.loads(fm_json)
    except ValueError:
        return []
    intent = fm.get("code_unit")
    if not isinstance(intent, dict):
        return []
    sites = intent.get("call_sites")
    if not isinstance(sites, list):
        return []
    return [s for s in sites if isinstance(s, dict)]


def _source_path_of_node(store: Store, node_id: str) -> str | None:
    """Look up a node's source_path by id.

    Used twice per edge during index construction; cheap enough that
    we don't bother caching here -- the index itself is the cache."""
    node = store.get_node(node_id)
    return node.source_path if node else None
