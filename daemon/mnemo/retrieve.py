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


def resolve_auto_scope(store: Store, cwd: str | None) -> tuple[list[str], bool]:
    """Derive the active project key SET from the caller's cwd (v5.26.0).

    Returns ``(project_keys, is_indexed)``. A repo's knowledge can span
    MULTIPLE keys -- e.g. its memory under the path-derived key and its code
    under a source registered with a custom ``project_key`` (mnemo itself:
    memory under ``D--Repository-knowledge-base``, code under
    ``mnemo-daemon``). So the scope is the union of:

    1. the path-derived key (``paths.resolve_project_key(cwd)``), and
    2. the ``project_key`` of every registered source rooted at, under, or
       above cwd,

    each included ONLY when it actually owns nodes (the has-nodes guard --
    scoping to an empty project would starve the query). An empty result
    means "this directory is not indexed": callers keep the query unscoped
    and may surface the index-me offer."""
    from mnemo import paths

    if not cwd:
        return [], False

    def _owned(key: str) -> int:
        try:
            return sum(store.count_nodes(project_key=key, include_base=False).values())
        except Exception:
            return 0

    keys: list[str] = []
    derived = paths.resolve_project_key(cwd)
    if _owned(derived) > 0:
        keys.append(derived)
    norm_cwd = cwd.replace("\\", "/").rstrip("/").lower()
    try:
        for src in store.list_sources():
            skey = src.project_key
            if not skey or skey in keys:
                continue
            root = str(src.path).replace("\\", "/").rstrip("/").lower()
            related = (
                root == norm_cwd
                or root.startswith(norm_cwd + "/")
                or norm_cwd.startswith(root + "/")
            )
            if related and _owned(skey) > 0:
                keys.append(skey)
    except Exception:
        pass
    return keys, bool(keys)


def relevance_score(
    cfg: config.Config,
    *,
    vector: float,
    lexical: float,
    bm25_rank: int | None,
    vec_rank: int | None,
) -> float:
    """Fuse the two RELEVANCE rankers (dense vector + BM25) into one score.

    This is the only part of scoring that ``cfg.fusion_mode`` moves. The
    CONTEXT terms (graph / recency / type / project) and the multiplicative
    finishers (exact-name boost, isolation penalty) are orthogonal and
    proven, so they sit outside and are identical across modes.

    Every mode returns a score in the SAME band, ``[0, alpha + zeta]``. That
    matters: if one mode returned a wider band than another, flipping the
    mode would silently re-weight the context terms relative to relevance
    and we'd be measuring two changes at once.

    Ranks are 0-based (``store.bm25_search`` order); ``None`` means the node
    was not a candidate for that ranker.
    """
    sw = cfg.scoring
    band = sw.alpha + sw.zeta
    mode = getattr(cfg, "fusion_mode", config.DEFAULT_FUSION_MODE)
    if mode not in config.FUSION_MODES:
        mode = config.DEFAULT_FUSION_MODE  # fail open: never break retrieval

    if mode == "bm25_lead":
        # BM25 leads; vector is a recall fallback for BM25-misses plus a
        # tie-break. Deliberately NOT a hard band -- a BM25-miss keeps a
        # non-zero score, because erasing it would make conceptual queries
        # (whose answer is not a literal token match) unanswerable.
        w = getattr(cfg, "bm25_lead_weight", 0.85)
        bm25_component = (1.0 / (1.0 + bm25_rank)) if bm25_rank is not None else 0.0
        return band * (w * bm25_component + (1.0 - w) * max(0.0, vector))

    if mode == "weighted_rrf":
        # Rank-level RRF: scale-robust (reads positions, not raw cosines).
        # Weighted because equal-weight RRF measured WORSE than bm25-alone
        # (0.67 vs 0.81) -- RRF assumes comparably-good rankers, and here
        # the vector ranker is the weaker one.
        rk = getattr(cfg, "rrf_k", 60)
        wb = getattr(cfg, "rrf_weight_bm25", 0.75)
        wv = getattr(cfg, "rrf_weight_vector", 0.25)
        raw = 0.0
        if bm25_rank is not None:
            raw += wb / (rk + 1 + bm25_rank)
        if vec_rank is not None:
            raw += wv / (rk + 1 + vec_rank)
        ceiling = (wb + wv) / (rk + 1)
        return band * (raw / ceiling) if ceiling > 0 else 0.0

    # weighted_sum: the historical score-level blend, unchanged. No longer
    # the default (weighted_rrf measured +0.38 lexical / +0.05 conceptual
    # hit@5 against it) but kept exactly as-is: it is both the escape hatch
    # and the baseline any future fusion change is compared against.
    return sw.alpha * vector + sw.zeta * lexical


def query(
    store: Store,
    embedder: Embedder,
    prompt: str,
    *,
    budget_tokens: int | None = None,
    k: int | None = None,
    active_project: str | None = None,
    active_projects: list[str] | None = None,
    update_graph: bool = True,
    exclude_local_only: bool = False,
) -> RetrievalResult:
    cfg = config.load()
    # v5.26.0: a project's knowledge can span multiple keys (memory under
    # the path-derived key, code under a source-declared one). Normalize
    # both spellings into one scope SET; ``active_projects`` wins.
    scope: frozenset[str] | None = None
    if active_projects:
        scope = frozenset(p for p in active_projects if p) or None
    elif active_project:
        scope = frozenset({active_project})
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

    # 2b. Vector RANK positions (0-based, best first) for the rank-level
    # fusion modes. `weighted_rrf` reads positions rather than raw cosines,
    # which is what makes it robust to the two rankers' scores living on
    # incomparable scales.
    vec_ranks: dict[str, int] = {
        nid: i for i, nid in enumerate(sorted(vec_scores, key=lambda n: -vec_scores[n]))
    }

    # 3. Graph proximity from candidates.
    graph_scores = graph.compute_graph_scores(store, vec_scores)

    # 3b. v5.27.0: BM25 lexical RECALL. A name-exact node that misses the
    # vector top-40 (long prose dominates embeddings) becomes a candidate
    # here; its rank feeds the zeta/lexical component below.
    try:
        bm25_ranks: dict[str, int] = dict(store.bm25_search(prompt, k=max(k * 2, 40)))
    except Exception:
        bm25_ranks = {}

    # 4. Score each candidate (union of vector, graph, and BM25).
    # Single batched SELECT for all candidate nodes - cleaner and faster than
    # per-candidate get_node() calls.
    #
    # v1.1 BASE / project-isolation: when an active project is set and the
    # current isolation mode is 'strict' (default), drop candidates that
    # are neither in the active project NOR flagged BASE. Scoring still
    # boosts the project match via epsilon for ranking within the kept set.
    now = time.time()
    prompt_lower = prompt.lower()
    candidate_ids = list(set(vec_scores) | set(graph_scores) | set(bm25_ranks))
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
            and scope is not None
            and not node.base
            and node.project_key is not None
            and node.project_key not in scope
        )
        c_vector = vec_scores.get(nid, 0.0)
        c_graph = graph_scores.get(nid, 0.0)
        c_recency = _recency_score(node.updated_at, now, cfg.recency_half_life_days)
        c_type = type_pri.get(node.type, 0.0)
        c_project = _project_score(node.project_key, scope)
        # v5.27.0: RRF-style fusion folded into the existing zeta component
        # -- the substring fraction OR the BM25 rank score (top hit ~1.0,
        # decaying), whichever is stronger. No 7th weight; the auto-tuner
        # contract is untouched.
        c_lexical = _lexical_score(q_tokens, node)
        if nid in bm25_ranks:
            c_lexical = max(c_lexical, 1.0 / (1.0 + bm25_ranks[nid]))
        # Fusion rebalance: the vector<->lexical blend is the ONLY
        # mode-dependent part of the score. The context terms below are
        # orthogonal and identical across modes, so an A/B of fusion_mode
        # measures one change, not two.
        s = relevance_score(
            cfg,
            vector=c_vector,
            lexical=c_lexical,
            bm25_rank=bm25_ranks.get(nid),
            vec_rank=vec_ranks.get(nid),
        ) + (sw.beta * c_graph + sw.gamma * c_recency + sw.delta * c_type + sw.epsilon * c_project)
        if out_of_scope:
            # v4.3.2: deprioritize (don't erase) cross-project matches.
            s *= getattr(cfg, "project_isolation_penalty", 0.85)
        # v5.27.0: exact-name finisher. Asking for a thing BY NAME is the
        # strongest intent signal retrieval gets; same multiplicative
        # pattern as the isolation penalty.
        if _exact_name_match(node.name, prompt_lower):
            s *= getattr(cfg, "exact_name_boost", 1.25)
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


def _exact_name_match(name: str, prompt: str) -> bool:
    """True when the candidate's name appears verbatim in the prompt.

    Names shorter than 4 chars are noise ('app', 'db'); the match is
    case-insensitive substring (qualified/underscored names make token
    splitting unreliable)."""
    n = (name or "").strip().lower()
    return len(n) >= 4 and n in (prompt or "").lower()


def _project_score(node_project: str | None, scope: frozenset[str] | None) -> float:
    if scope is None or node_project is None:
        return 0.0
    return 1.0 if node_project in scope else 0.0


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
