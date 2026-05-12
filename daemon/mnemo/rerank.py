"""Maximal Marginal Relevance (MMR) re-rank for v1.2 phase 4.

MMR diversifies a top-N candidate pool so the user doesn't see five
paraphrases of the same node. Plugged into ``retrieve.query`` between
the 6-term scoring sort and the budget compression step.

Algorithm
=========

At each step, pick the candidate that maximizes::

    score_mmr(c) = lambda * relevance(c)
                 - (1 - lambda) * max_sim(c, picked)

Where:
- ``relevance(c)`` is the existing 6-term score, min-max-normalized
  within the input pool so it's on the same [0, 1] scale as cosine.
- ``max_sim(c, picked)`` is the largest cosine of c's chunk embedding
  against any already-picked candidate's chunk embedding.

Defaults
========

- ``lambda_ = 0.7`` (set in ``config.mmr_lambda``): leans toward
  relevance with enough diversity penalty to nuke near-duplicates.
- ``lambda_ = 1.0`` short-circuits to pure relevance (~0.5 ms saved
  per query) -- the pre-v1.2 behavior.
- ``lambda_ = 0.0`` is pure diversity (probably never useful in
  production; available for diagnostics).

Edge cases
==========

- Empty pool -> empty result.
- Pool smaller than k -> return the pool as-is.
- Missing embeddings -> treated as zero-cosine to everything, so the
  candidate is admitted on relevance alone. Without this fallback a
  single embed glitch would silently drop hits.
- Tied MMR scores -> deterministic first-pick wins, preserving the
  caller's input order for predictable output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mnemo.feedback import cosine_similarity

if TYPE_CHECKING:
    from mnemo.compress import ScoredHit


def mmr_select(
    pool: list[ScoredHit],
    *,
    k: int,
    lambda_: float,
    embeddings: dict[str, list[float]],
) -> list[ScoredHit]:
    """Run MMR over ``pool``, returning up to ``k`` candidates.

    ``embeddings`` maps ``node_id`` -> the embedding of that node's
    best-scoring chunk (caller assembles via
    ``Store.get_chunk_embeddings``). Missing entries are treated as
    "no diversity signal" -- the candidate is selected on relevance
    alone.

    Caller is responsible for passing ``pool`` already sorted by
    relevance score (descending). The function doesn't re-sort.
    """
    if not pool or k <= 0:
        return []
    # Fast path: lambda = 1.0 means no diversity penalty, output is
    # just the input truncated.
    if lambda_ >= 1.0:
        return pool[:k]
    # Pool smaller than k -> return everything (no diversification
    # needed because we'd just be ordering it).
    if len(pool) <= k:
        return list(pool)

    # Min-max normalize relevance scores to [0, 1] so the MMR formula
    # is comparing apples to apples (cosine is already in [-1, 1] but
    # in practice for our embeddings stays in [0, 1]).
    scores = [h.score for h in pool]
    s_min, s_max = min(scores), max(scores)
    if s_max == s_min:
        # Degenerate pool -- everyone has the same relevance; MMR
        # collapses to "first pool item wins per step" once diversity
        # has anything to say.
        relevance = {h.node.id: 1.0 for h in pool}
    else:
        relevance = {h.node.id: (h.score - s_min) / (s_max - s_min) for h in pool}

    picked: list[ScoredHit] = []
    remaining = list(pool)
    while remaining and len(picked) < k:
        best_idx = 0
        best_mmr = float("-inf")
        for i, cand in enumerate(remaining):
            cand_emb = embeddings.get(cand.node.id)
            if not picked or cand_emb is None:
                max_sim = 0.0
            else:
                max_sim = 0.0
                for p in picked:
                    p_emb = embeddings.get(p.node.id)
                    if p_emb is None:
                        continue
                    sim = cosine_similarity(cand_emb, p_emb)
                    if sim > max_sim:
                        max_sim = sim
            mmr = lambda_ * relevance[cand.node.id] - (1 - lambda_) * max_sim
            # Strict > -- ties go to the earlier index (deterministic
            # tiebreak documented in v1.2 design section 5).
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = i
        picked.append(remaining.pop(best_idx))
    return picked
