"""Hybrid Graph-RAG retrieval orchestrator.

Pipeline (one query):

1. Classify intent -> tag set + per-node-type priority weights.
2. Embed prompt -> 384-d vector. Run ``store.vec_search`` for top ``2k``
   chunks.
3. Deduplicate chunks by ``node_id`` (best chunk per node wins).
4. Compute 1-hop graph proximity scores from the candidate set.
5. Score every candidate ``alpha*vector + beta*graph + gamma*recency
   + delta*type + epsilon*project_scope``; take top ``k``.
6. Compress to ``budget_tokens`` with citations.
7. Strengthen co-occurrence edges between the surfaced nodes.
8. Persist a row in the ``queries`` audit log.

All scoring weights and the recency half-life are module attributes so the
daemon and tests can tune them.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from mnemo import compress, graph
from mnemo.compress import CompressedHit, ScoredHit
from mnemo.embed import Embedder
from mnemo.intent import classify_intent, type_priority_for
from mnemo.store import Store

# Scoring weights (design doc s 6.3)
ALPHA = 0.45  # vector cosine
BETA = 0.20  # graph proximity
GAMMA = 0.15  # recency
DELTA = 0.15  # type priority
EPSILON = 0.05  # project scope

RECENCY_HALF_LIFE_DAYS = 90.0
DEFAULT_K = 20
DEFAULT_BUDGET_TOKENS = 800


@dataclass
class RetrievalResult:
    hits: list[CompressedHit]
    intent_tags: list[str]
    tokens_used: int
    query_id: str


def query(
    store: Store,
    embedder: Embedder,
    prompt: str,
    *,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    k: int = DEFAULT_K,
    active_project: str | None = None,
    update_graph: bool = True,
) -> RetrievalResult:
    tags = classify_intent(prompt)
    type_pri = type_priority_for(tags)

    # 1. Vector search (oversample to leave room for dedup + graph).
    query_vec = embedder.embed_text(prompt)
    raw = store.vec_search(query_vec, k=max(k * 2, 40))

    # 2. Per-node best chunk.
    vec_scores: dict[str, float] = {}
    chunk_info: dict[str, tuple[int, str]] = {}
    for nid, chunk_idx, chunk_text, distance in raw:
        sim = _l2_distance_to_cosine(distance)
        if nid not in vec_scores or sim > vec_scores[nid]:
            vec_scores[nid] = sim
            chunk_info[nid] = (chunk_idx, chunk_text)

    # 3. Graph proximity from candidates.
    graph_scores = graph.compute_graph_scores(store, vec_scores)

    # 4. Score each candidate (union of vector and graph).
    now = time.time()
    candidate_ids = set(vec_scores) | set(graph_scores)
    scored: list[ScoredHit] = []
    for nid in candidate_ids:
        node = store.get_node(nid)
        if node is None:
            continue
        s = (
            ALPHA * vec_scores.get(nid, 0.0)
            + BETA * graph_scores.get(nid, 0.0)
            + GAMMA * _recency_score(node.updated_at, now)
            + DELTA * type_pri.get(node.type, 0.0)
            + EPSILON * _project_score(node.project_key, active_project)
        )
        idx, text = chunk_info.get(nid, (None, None))
        scored.append(ScoredHit(node=node, score=s, chunk_idx=idx, chunk_text=text))

    scored.sort(key=lambda h: -h.score)
    top = scored[:k]

    # 5. Compress to budget.
    hits, used = compress.compress_to_budget(top, budget_tokens=budget_tokens)

    # 6. Co-occurrence learning + audit log.
    retrieved_ids = [h.node_id for h in hits]
    if update_graph and len(retrieved_ids) >= 2:
        graph.update_co_occurrence(store, retrieved_ids)

    qid = store.log_query(
        prompt=prompt,
        intent_tags=sorted(tags),
        retrieved_ids=retrieved_ids,
        scores={h.node_id: round(h.score, 4) for h in hits},
    )

    return RetrievalResult(hits=hits, intent_tags=sorted(tags), tokens_used=used, query_id=qid)


# --- Score helpers ---------------------------------------------------------


def _l2_distance_to_cosine(distance: float) -> float:
    """For unit vectors, L2 distance d satisfies d^2 = 2 * (1 - cos).

    sqlite-vec returns L2 (not L2 squared), so cos = 1 - d^2 / 2.
    Clamped to [0, 1] to absorb tiny floating-point noise.
    """
    sim = 1.0 - 0.5 * distance * distance
    if sim < 0.0:
        return 0.0
    if sim > 1.0:
        return 1.0
    return sim


def _recency_score(updated_at: int, now: float) -> float:
    age_seconds = max(0.0, now - updated_at)
    age_days = age_seconds / 86400.0
    return math.exp(-age_days / RECENCY_HALF_LIFE_DAYS)


def _project_score(node_project: str | None, active_project: str | None) -> float:
    if active_project is None or node_project is None:
        return 0.0
    return 1.0 if node_project == active_project else 0.0
