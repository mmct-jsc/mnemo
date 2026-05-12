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

import logging
import math
import re
import time
from dataclasses import dataclass

from mnemo import compress, config, graph
from mnemo.compress import CompressedHit, ScoredHit
from mnemo.embed import Embedder
from mnemo.intent import classify_intent, type_priority_for
from mnemo.store import Node, Store

log = logging.getLogger(__name__)

# Scoring weights are now in mnemo.config (editable via UI / API).
# These module attributes are kept for backwards-compatible imports and
# tests; on every query we re-read from the config file.
ALPHA = 0.40  # vector cosine
BETA = 0.15  # graph proximity
GAMMA = 0.10  # recency
DELTA = 0.10  # type priority
EPSILON = 0.05  # project scope
ZETA = 0.20  # lexical overlap (name + description)

RECENCY_HALF_LIFE_DAYS = 90.0
DEFAULT_K = 20
DEFAULT_BUDGET_TOKENS = 800

# Lexical scorer: tokenize alpha-word-ish things of >= 3 chars. Substring
# match (not exact) so "co-auth" matches "co-authored-by".
_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9-]+")


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
    budget_tokens: int | None = None,
    k: int | None = None,
    active_project: str | None = None,
    update_graph: bool = True,
) -> RetrievalResult:
    cfg = config.load()
    if k is None:
        k = cfg.defaults.k
    if budget_tokens is None:
        budget_tokens = cfg.defaults.budget_tokens
    sw = cfg.scoring

    tags = classify_intent(prompt)
    type_pri = type_priority_for(tags)
    q_tokens = _tokenize(prompt)

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
    # Single batched SELECT for all candidate nodes - cleaner and faster than
    # per-candidate get_node() calls.
    #
    # v1.1 BASE / project-isolation: when an active project is set and the
    # current isolation mode is 'strict' (default), drop candidates that
    # are neither in the active project NOR flagged BASE. Scoring still
    # boosts the project match via epsilon for ranking within the kept set.
    now = time.time()
    candidate_ids = list(set(vec_scores) | set(graph_scores))
    nodes_by_id = store.get_nodes_by_ids(candidate_ids)
    isolation_mode = getattr(cfg, "project_isolation_mode", "strict")
    scored: list[ScoredHit] = []
    for nid in candidate_ids:
        node = nodes_by_id.get(nid)
        if node is None:
            continue
        if (
            isolation_mode == "strict"
            and active_project is not None
            and not node.base
            and node.project_key != active_project
        ):
            continue  # hard-filter: outside active project, not BASE
        s = (
            sw.alpha * vec_scores.get(nid, 0.0)
            + sw.beta * graph_scores.get(nid, 0.0)
            + sw.gamma * _recency_score(node.updated_at, now, cfg.recency_half_life_days)
            + sw.delta * type_pri.get(node.type, 0.0)
            + sw.epsilon * _project_score(node.project_key, active_project)
            + sw.zeta * _lexical_score(q_tokens, node)
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

    # 6b. v1.2 phase 2: inferred-re-query feedback.
    #
    # BEFORE we persist the current query, look at the recent audit
    # log. If any prior query within the window is cosine-similar to
    # this one, treat it as evidence the user re-asked because the
    # earlier hits missed -- emit feedback against those earlier hits.
    # Running this before log_query is what keeps us from comparing
    # the current query to itself.
    from mnemo import feedback as _fb  # local import to avoid cycle

    try:
        _fb.infer_requery_feedback(
            store,
            query_emb=query_vec,
            window_seconds=cfg.requery_window_seconds,
            threshold=cfg.requery_cosine_threshold,
            top_n=cfg.requery_top_n_hits,
        )
    except Exception:
        # Never let a feedback-detection error abort the user's query.
        log.exception("inferred_requery detector failed; continuing")

    qid = store.log_query(
        prompt=prompt,
        intent_tags=sorted(tags),
        retrieved_ids=retrieved_ids,
        scores={h.node_id: round(h.score, 4) for h in hits},
        embedding=query_vec,
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


def _recency_score(updated_at: int, now: float, half_life_days: float | None = None) -> float:
    half = half_life_days if half_life_days is not None else RECENCY_HALF_LIFE_DAYS
    age_seconds = max(0.0, now - updated_at)
    age_days = age_seconds / 86400.0
    return math.exp(-age_days / half)


def _project_score(node_project: str | None, active_project: str | None) -> float:
    if active_project is None or node_project is None:
        return 0.0
    return 1.0 if node_project == active_project else 0.0


def _tokenize(text: str) -> list[str]:
    """Return query tokens of >= 3 characters, lowercased."""
    return [t.lower() for t in _TOKEN_RE.findall(text) if len(t) >= 3]


def _lexical_score(query_tokens: list[str], node: Node) -> float:
    """Fraction of query tokens that appear (as substrings) in the node's
    name + description. This catches exact-term matches the embedding
    tends to dilute on short queries.
    """
    if not query_tokens:
        return 0.0
    haystack = (node.name + " " + (node.description or "")).lower()
    if not haystack.strip():
        return 0.0
    matches = sum(1 for t in query_tokens if t in haystack)
    return min(1.0, matches / len(query_tokens))
