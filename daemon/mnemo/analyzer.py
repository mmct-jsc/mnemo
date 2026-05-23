"""v5.12.0 -- knowledge auditor: deterministic cross-reference analysis.

Phase 1 of mnemo's Understanding arc. The auditor walks the existing
node graph + surfaces structural issues. No LLM, no API key, no new
dependencies. See:

- ``docs/plans/2026-05-22-mnemo-understanding-phase1-design.md`` for
  the spec + Definition of Done.
- ``memory/project_mnemo_v6_vision_understanding`` for the long-term
  arc (LLM-augmented detection lands in v5.13.0+).

Three detectors:

1. :func:`detect_stale` -- nodes whose body / description contain the
   literal ``SUPERSEDED`` token. Lexical, instant. Severity: low.
2. :func:`detect_duplicates` -- pairs of same-type nodes with cosine
   similarity >= 0.95. Uses sqlite-vec's chunk-level NN search +
   filters to within-type pairs only (Phase 1 contract). Severity:
   medium.
3. :func:`detect_orphan_references` -- nodes whose body contains
   ``[mnemo:<id>]`` where ``<id>`` is not in the graph. Severity:
   high (broken citation).

The orchestrator :func:`analyze` runs all three (or a filtered
subset via ``types=``) and returns a canonical envelope:

    {
        "ran_at": "<ISO timestamp>",
        "node_count_scanned": <int>,
        "findings": [{type, node_ids, description, severity}, ...],
        "summary": {<type>: <count>, ...},
    }
"""

from __future__ import annotations

import re
import time
from typing import Any

from mnemo.store import Node, Store

# Per-type pages for ``Store.list_nodes``. We page through each
# type bucket so even a 4500-node bucket doesn't load everything at
# once. The default upper limit is generous enough that small
# buckets (memory_* are typically 10-200 nodes) finish in one
# fetch.
PAGE_SIZE = 1000

# Cosine-similarity threshold for the duplicates detector. 0.95 is
# the well-known sentence-transformers near-duplicate cutoff: 0.98
# misses real near-duplicates (different wording, same meaning);
# 0.90 false-positives on closely-related siblings.
#
# sqlite-vec uses L2 distance on normalized vectors:
#     cos = 1 - L2^2 / 2
# So cosine >= 0.95 <=> L2^2 <= 0.10 <=> L2 <= ~0.3162.
DUPLICATE_COSINE_THRESHOLD = 0.95
DUPLICATE_L2_THRESHOLD = (2 * (1 - DUPLICATE_COSINE_THRESHOLD)) ** 0.5

# Phase 1 only flags duplicates within these node types. Code nodes
# (code_function, code_method, ...) are intentionally skipped --
# tree-sitter already canonicalizes them + the dedup story for code
# is different (refactoring suggestions, not body merges).
DUPLICATE_TYPE_BUCKETS = (
    "memory_feedback",
    "memory_project",
    "memory_reference",
    "memory_user",
    "memory_session",
    "plan_doc",
    "project_doc",
    "session_summary",
)

# Regex for the canonical mnemo citation token. The v1.0+ convention
# is ``[mnemo:<id>]`` where ``<id>`` can be any URL-safe string.
_CITATION_RE = re.compile(r"\[mnemo:([^\]]+?)\]")

# Lexical marker for ``stale``. Case-insensitive match against the
# body OR the description. Matches our own session-handover
# convention ("SUPERSEDED by v5.X.X").
_STALE_MARKER_RE = re.compile(r"superseded", re.IGNORECASE)


def _iter_all_nodes(store: Store, *, type: str | None = None) -> list[Node]:
    """Page-through Store.list_nodes (which has a page-size cap) and
    return the concatenated list. Used by every detector + the
    orchestrator's ``node_count_scanned``."""
    out: list[Node] = []
    offset = 0
    while True:
        page = store.list_nodes(type=type, limit=PAGE_SIZE, offset=offset)
        if not page:
            break
        out.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return out


# --- 1. stale ----------------------------------------------------------


def detect_stale(store: Store) -> list[dict[str, Any]]:
    """Surface nodes whose body or description contains ``SUPERSEDED``
    (case-insensitive). The user's own marker; informational only."""
    findings: list[dict[str, Any]] = []
    for node in _iter_all_nodes(store):
        haystack = " ".join(filter(None, [node.description, node.body]))
        if _STALE_MARKER_RE.search(haystack):
            findings.append(
                {
                    "type": "stale",
                    "node_ids": [node.id],
                    "description": (
                        f"Node {node.id!r} body/description marks it as "
                        f"SUPERSEDED; consider archiving."
                    ),
                    "severity": "low",
                }
            )
    return findings


# --- 2. duplicates -----------------------------------------------------


def detect_duplicates(store: Store, *, embedder: Any) -> list[dict[str, Any]]:
    """Surface within-type pairs of nodes whose embeddings are within
    the near-duplicate cosine threshold.

    Implementation: iterate each node, embed its body (cheaply --
    embedder is cached + warm in production), then do a vec_search
    with type_filter=[node.type] for k=10. Any hit other than the
    node itself with L2 <= DUPLICATE_L2_THRESHOLD becomes a finding.

    De-duplication of pairs: a pair (A, B) and (B, A) would otherwise
    be emitted twice; we sort the pair + use a seen-set."""
    if embedder is None:
        return []

    findings: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for type_bucket in DUPLICATE_TYPE_BUCKETS:
        nodes = _iter_all_nodes(store, type=type_bucket)
        if len(nodes) < 2:
            continue
        for node in nodes:
            haystack = node.body or node.description or node.name
            if not haystack:
                continue
            try:
                vec = embedder.embed_text(haystack)
            except Exception:  # noqa: BLE001
                continue
            try:
                hits = store.vec_search(vec, k=10, type_filter=[type_bucket])
            except Exception:  # noqa: BLE001 -- vec table missing on empty stores
                continue
            for hit_node_id, _chunk_idx, _chunk_text, distance in hits:
                if hit_node_id == node.id:
                    continue
                if distance > DUPLICATE_L2_THRESHOLD:
                    continue
                pair = tuple(sorted([node.id, hit_node_id]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                # Cosine = 1 - L2^2 / 2 (for normalized vectors).
                cosine = max(0.0, min(1.0, 1.0 - (distance * distance) / 2.0))
                findings.append(
                    {
                        "type": "duplicates",
                        "node_ids": list(pair),
                        "description": (
                            f"Two {type_bucket} nodes with cosine similarity "
                            f"{cosine:.3f}; consider merging or marking one as "
                            f"superseded."
                        ),
                        "severity": "medium",
                    }
                )
    return findings


# --- 3. orphan_references ----------------------------------------------


def detect_orphan_references(store: Store) -> list[dict[str, Any]]:
    """Surface nodes whose body cites ``[mnemo:<id>]`` for an ``<id>``
    not in the current graph."""
    all_nodes = _iter_all_nodes(store)
    existing_ids = {n.id for n in all_nodes}

    findings: list[dict[str, Any]] = []
    for node in all_nodes:
        if not node.body:
            continue
        missing: list[str] = []
        for match in _CITATION_RE.finditer(node.body):
            referenced = match.group(1).strip()
            if referenced and referenced not in existing_ids:
                missing.append(referenced)
        if missing:
            findings.append(
                {
                    "type": "orphan_reference",
                    "node_ids": [node.id],
                    "description": (
                        f"Node {node.id!r} body cites missing target(s): "
                        f"{sorted(set(missing))}; the target node was deleted "
                        f"or never existed."
                    ),
                    "severity": "high",
                    "missing_targets": sorted(set(missing)),
                }
            )
    return findings


# --- Orchestrator ------------------------------------------------------


# Canonical type strings the analyzer recognizes. Used by the
# ``types=`` filter on :func:`analyze`. ``orphan_references`` is
# plural for the API; the per-finding ``type`` is the singular
# ``orphan_reference``.
KNOWN_DETECTOR_TYPES = ("stale", "duplicates", "orphan_references")


def analyze(
    store: Store,
    *,
    embedder: Any | None = None,
    types: list[str] | None = None,
    project_key: str | None = None,  # noqa: ARG001 -- reserved for v5.13.0 scoping
) -> dict[str, Any]:
    """Run the requested detectors + return a canonical envelope.

    Args:
        store: the live mnemo Store.
        embedder: optional Embedder for ``duplicates``. When ``None``,
            the duplicates detector returns an empty list (clean
            fallback for stores without embeddings; tests can opt out).
        types: filter list, default = all detectors. Pass e.g.
            ``["stale"]`` to skip duplicates + orphan_references.
        project_key: reserved for v5.13.0 (currently no-op).

    Returns:
        ``{ran_at, node_count_scanned, findings, summary}``.
    """
    requested = set(types) if types else set(KNOWN_DETECTOR_TYPES)

    findings: list[dict[str, Any]] = []
    if "stale" in requested:
        findings.extend(detect_stale(store))
    if "duplicates" in requested:
        findings.extend(detect_duplicates(store, embedder=embedder))
    if "orphan_references" in requested:
        findings.extend(detect_orphan_references(store))

    # Tally by type. ``orphan_reference`` (singular per-finding type)
    # is reported under the API-facing ``orphan_references`` (plural)
    # key so callers can match on the same vocabulary they passed via
    # ``types=``.
    summary: dict[str, int] = {}
    for f in findings:
        bucket = f["type"]
        if bucket == "orphan_reference":
            bucket = "orphan_references"
        summary[bucket] = summary.get(bucket, 0) + 1

    return {
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "node_count_scanned": len(_iter_all_nodes(store)),
        "findings": findings,
        "summary": summary,
    }
