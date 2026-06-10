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
# match (not exact) so "emo" matches "emojis" / "emoji".
_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9-]+")


@dataclass
class RetrievalResult:
    hits: list[CompressedHit]
    intent_tags: list[str]
    tokens_used: int
    query_id: str
    # v5 phase 1: count of nodes that were dropped because they
    # carried the ``local_only`` flag AND the caller passed
    # ``exclude_local_only=True``. The prompt-architect dock surfaces
    # this in a pre-emit warning ("X local-only excluded; verify
    # before pasting"). 0 when the filter is off, which is the
    # backward-compatible default for every non-prompt-architect
    # caller.
    local_only_excluded: int = 0


def resolve_auto_scope(store: Store, cwd: str | None) -> tuple[str | None, bool]:
    """Derive the active project from the caller's cwd (v5.26.0 leak fix).

    Returns ``(project_key_to_use, is_indexed)``. The key is applied ONLY
    when that project actually has nodes -- strict isolation would
    otherwise filter a fresh, unindexed project's queries to zero. An
    unindexed cwd returns ``(None, False)`` so callers can keep the query
    unscoped AND surface the "this project is not indexed -- want to index
    it?" offer. Strict isolation has existed since v1.2; the cross-project
    leak was that no production caller passed ``active_project``."""
    from mnemo import paths

    if not cwd:
        return None, False
    key = paths.resolve_project_key(cwd)
    try:
        owned = sum(store.count_nodes(project_key=key, include_base=False).values())
    except Exception:
        return None, False
    return (key, True) if owned > 0 else (None, False)


def query(
    store: Store,
    embedder: Embedder,
    prompt: str,
    *,
    budget_tokens: int | None = None,
    k: int | None = None,
    active_project: str | None = None,
    update_graph: bool = True,
    exclude_local_only: bool = False,
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
    # v5 phase 1: track local_only drops so the prompt-architect can
    # surface a "N excluded" warning. The filter only fires when the
    # caller opts in (default False = legacy behaviour).
    local_only_excluded = 0
    # v1.2 phase 5: capture unweighted 6-term components per candidate
    # so the auto-tuner can rescore with alternative weights without
    # rerunning the embedder. Logged with the audit row below.
    components_by_node: dict[str, dict[str, float]] = {}
    for nid in candidate_ids:
        node = nodes_by_id.get(nid)
        if node is None:
            continue
        if exclude_local_only and node.local_only:
            local_only_excluded += 1
            continue
        # v1.1 BASE / project-isolation hard-filter. v1.2.1 fix: treat
        # ``project_key is None`` as cross-cutting (it survives the
        # filter). Pre-fix, every ``project_doc`` (CLAUDE.md and friends)
        # AND any memory entry that hadn't picked up a project_key got
        # silently dropped from every project's queries, because
        # ``None != active_project`` is True. That made global memory
        # invisible whenever an active project was set -- one of the
        # dominant causes of "common query returns nothing" in v1.2.0.
        # v4.3.2: SOFT isolation. Pre-v4.3.2 this was a hard `continue`
        # that ERASED cross/inactive-project non-BASE candidates -- so a
        # dramatically stronger exact match (e.g. the v4 handover, sim
        # 0.757) was invisible and a weaker BASE node (0.529) won: a
        # strict-isolation silent-zero ("the result seems wrong"). Now
        # the node is kept + scored but its final score is multiplied
        # by cfg.project_isolation_penalty (default 0.7) below, so BASE
        # + in-project still win for comparable relevance while a
        # dominant cross-project match still surfaces.
        out_of_scope = (
            isolation_mode == "strict"
            and active_project is not None
            and not node.base
            and node.project_key is not None
            and node.project_key != active_project
        )
        c_vector = vec_scores.get(nid, 0.0)
        c_graph = graph_scores.get(nid, 0.0)
        c_recency = _recency_score(node.updated_at, now, cfg.recency_half_life_days)
        c_type = type_pri.get(node.type, 0.0)
        c_project = _project_score(node.project_key, active_project)
        c_lexical = _lexical_score(q_tokens, node)
        s = (
            sw.alpha * c_vector
            + sw.beta * c_graph
            + sw.gamma * c_recency
            + sw.delta * c_type
            + sw.epsilon * c_project
            + sw.zeta * c_lexical
        )
        if out_of_scope:
            # v4.3.2: deprioritize (don't erase) cross-project matches.
            s *= getattr(cfg, "project_isolation_penalty", 0.85)
        components_by_node[nid] = {
            "vector": c_vector,
            "graph": c_graph,
            "recency": c_recency,
            "type": c_type,
            "project": c_project,
            "lexical": c_lexical,
        }
        idx, text = chunk_info.get(nid, (None, None))
        scored.append(ScoredHit(node=node, score=s, chunk_idx=idx, chunk_text=text))

    scored.sort(key=lambda h: -h.score)

    # 4b. v1.2 phase 4: MMR re-rank on an oversampled top pool.
    #
    # Without this the top-K is the pure score-sort; near-duplicate
    # nodes (e.g. five paraphrases of the same feedback retro) crowd
    # out distinct angles. MMR penalizes the diversity penalty so the
    # output spans more distinct chunks while still leaning on
    # relevance (lambda = 0.7 by default).
    if cfg.mmr_lambda < 1.0 and len(scored) > k:
        from mnemo import rerank as _rerank  # local import to avoid cycles

        pool_size = max(k * 2, 20)
        top_pool = scored[:pool_size]
        # Read back each candidate's best-chunk embedding so MMR can
        # compute the cosine diversity penalty. Pairs are (node_id,
        # chunk_idx); chunk_info captured them during the vec_search
        # dedup pass.
        wanted: list[tuple[str, int]] = []
        for h in top_pool:
            info = chunk_info.get(h.node.id)
            if info is not None:
                wanted.append((h.node.id, info[0]))
        chunk_embs = store.get_chunk_embeddings(wanted) if wanted else {}
        # Re-key by node_id so mmr_select can look up by ScoredHit.node.id.
        emb_by_node: dict[str, list[float]] = {}
        for h in top_pool:
            info = chunk_info.get(h.node.id)
            if info is None:
                continue
            v = chunk_embs.get((h.node.id, info[0]))
            if v is not None:
                emb_by_node[h.node.id] = v
        top = _rerank.mmr_select(top_pool, k=k, lambda_=cfg.mmr_lambda, embeddings=emb_by_node)
    else:
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

    # v1.2 phase 5: log per-hit components for the top pool (caps audit
    # row size while keeping enough candidates for the auto-tuner to
    # consider rescoring). Use the top ``max(k*2, 20)`` candidates so a
    # post-tune ordering can pull from them.
    pool_size = max(k * 2, 20)
    pool_nids = [h.node.id for h in scored[:pool_size]]
    components_log = {
        nid: components_by_node[nid] for nid in pool_nids if nid in components_by_node
    }

    qid = store.log_query(
        prompt=prompt,
        intent_tags=sorted(tags),
        retrieved_ids=retrieved_ids,
        scores={h.node_id: round(h.score, 4) for h in hits},
        embedding=query_vec,
        score_components=components_log,
    )

    return RetrievalResult(
        hits=hits,
        intent_tags=sorted(tags),
        tokens_used=used,
        query_id=qid,
        local_only_excluded=local_only_excluded,
    )


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


# v2.6.0 polish: cap the body slice we walk for lexical matching so an
# enormous body (e.g. a 500 KB Plan_doc) doesn't slow the scorer. 32 KB
# is enough to cover most handovers + feedback notes end-to-end while
# bounding the per-candidate cost. Terms past the cap are missed; the
# trade-off is explicit and documented in the test.
_LEXICAL_BODY_CAP = 32 * 1024


def _lexical_score(query_tokens: list[str], node: Node) -> float:
    """Fraction of query tokens that appear (as substrings) in the node's
    name + description + body.

    Catches exact-term matches the embedding tends to dilute on long
    bodies. Without the body in the haystack a verbose handover whose
    *body* literally contains every distinctive query term loses to a
    popular short doc with zero keyword overlap because the graph-edge
    boost dominates the small gap (v2.6.0 polish lesson).
    """
    if not query_tokens:
        return 0.0
    body_slice = (node.body or "")[:_LEXICAL_BODY_CAP]
    haystack = (node.name + " " + (node.description or "") + " " + body_slice).lower()
    if not haystack.strip():
        return 0.0
    matches = sum(1 for t in query_tokens if t in haystack)
    return min(1.0, matches / len(query_tokens))
