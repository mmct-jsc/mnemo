"""Graph: edge inference from frontmatter, co-occurrence learning, proximity.

The store owns edges; this module provides the policies on top.

- :func:`infer_edges_from_frontmatter` reads explicit ``appliesTo`` /
  ``supersedes`` / ``derivedFrom`` fields and writes corresponding edges.
- :func:`update_co_occurrence` strengthens ``co_occurs_with`` weights
  between nodes that surface together in a single retrieval.
- :func:`compute_graph_scores` walks 1-hop from a candidate set and returns
  proximity-weighted scores for the neighbors not already in the candidates.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Iterable

from mnemo.store import Node, Store

log = logging.getLogger(__name__)


# Tunables. Exposed as module attributes so tests and the daemon can override.
HOP_DECAY = 0.5
CO_OCCUR_INCREMENT = 0.1
CO_OCCUR_MAX_WEIGHT = 5.0
GRAPH_RELATIONS: tuple[str, ...] = (
    "applies_to",
    "co_occurs_with",
    "supersedes",
    "derived_from",
)

# Frontmatter field name -> edge relation
_FRONTMATTER_EDGE_FIELDS: dict[str, str] = {
    "appliesTo": "applies_to",
    "supersedes": "supersedes",
    "derivedFrom": "derived_from",
}


# --- Frontmatter inference -------------------------------------------------


def infer_edges_from_frontmatter(store: Store, node: Node) -> int:
    """Extract typed edges declared in node frontmatter. Returns count added.

    Frontmatter targets may be either node IDs or node ``name`` values.
    Unresolvable targets are silently dropped (logged at debug level).
    """
    if not node.frontmatter_json:
        return 0
    try:
        fm = json.loads(node.frontmatter_json)
    except (json.JSONDecodeError, TypeError):
        return 0

    n_added = 0
    for fm_key, relation in _FRONTMATTER_EDGE_FIELDS.items():
        targets = fm.get(fm_key)
        if not isinstance(targets, list):
            continue
        for target in targets:
            if not isinstance(target, str) or not target.strip():
                continue
            target_id = _resolve_target(store, target.strip())
            if target_id is None:
                log.debug("frontmatter target %r unresolved (node %s)", target, node.id)
                continue
            if target_id == node.id:
                continue
            store.add_edge(node.id, target_id, relation, source="frontmatter")
            n_added += 1
    return n_added


def _resolve_target(store: Store, target: str) -> str | None:
    direct = store.get_node(target)
    if direct is not None:
        return target
    for n in store.list_nodes(limit=1_000_000):
        if n.name == target:
            return n.id
    return None


# --- Co-occurrence learning ------------------------------------------------


def update_co_occurrence(
    store: Store,
    retrieved_ids: Iterable[str],
    *,
    weight_increment: float = CO_OCCUR_INCREMENT,
    cap: float = CO_OCCUR_MAX_WEIGHT,
) -> int:
    """Strengthen ``co_occurs_with`` weights between every pair of nodes.

    Stored as two directed edges per pair so 1-hop walks pick them up regardless
    of direction. Weights are capped to keep heavily-co-occurring pairs from
    dominating later retrievals.
    """
    ids = list(dict.fromkeys(retrieved_ids))  # dedupe, preserve order
    n = 0
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            for src, dst in ((a, b), (b, a)):
                existing = store.get_edges(src_id=src, dst_id=dst, relation="co_occurs_with")
                cur = existing[0].weight if existing else 0.0
                new = min(cur + weight_increment, cap)
                store.add_edge(src, dst, "co_occurs_with", weight=new, source="inferred")
                n += 1
    return n


# --- Proximity scoring -----------------------------------------------------


def compute_graph_scores(
    store: Store,
    candidate_scores: dict[str, float],
    *,
    relations: tuple[str, ...] = GRAPH_RELATIONS,
    hop_decay: float = HOP_DECAY,
) -> dict[str, float]:
    """Return proximity scores for nodes 1-hop from candidates.

    For each candidate ``c`` with score ``s`` and edge ``c <-> n`` of weight
    ``w``, contribute ``s * hop_decay * w`` to ``n``'s graph score. Edges are
    walked in both directions for the symmetric relations
    (``applies_to``, ``co_occurs_with``); for asymmetric relations
    (``supersedes``, ``derived_from``) only outgoing edges contribute.

    Nodes that are themselves candidates are excluded from the result so the
    final scorer can keep vector and graph contributions cleanly separated.
    Output values are capped at 1.0 so they don't outweigh vector cosine.

    Implementation: one batched SELECT to fetch every relevant edge in a
    single round-trip, then group in Python. With K candidates and R
    relations this used to do K*R*2 SELECTs; it now does 1.
    """
    if not candidate_scores:
        return {}
    out: dict[str, float] = defaultdict(float)
    symmetric = {"applies_to", "co_occurs_with"}
    candidate_ids = list(candidate_scores)
    edges = store.get_edges_for_nodes(candidate_ids, relations=relations)
    candidate_set = set(candidate_ids)
    for e in edges:
        # Outgoing edges contribute to dst when src is a candidate.
        if e.src_id in candidate_set:
            out[e.dst_id] += candidate_scores[e.src_id] * hop_decay * e.weight
        # Incoming edges contribute back to src for symmetric relations.
        if e.relation in symmetric and e.dst_id in candidate_set:
            out[e.src_id] += candidate_scores[e.dst_id] * hop_decay * e.weight

    return {nid: min(v, 1.0) for nid, v in out.items() if nid not in candidate_set}
