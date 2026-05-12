"""Unit tests for v1.2 phase 4 MMR re-rank.

Algorithm (recap):
    score_mmr(c) = lambda * relevance(c) - (1 - lambda) * max_sim(c, picked)

Where ``relevance`` is the existing 6-term score (min-max normalized
within the pool) and ``max_sim`` is the largest cosine of c's chunk
embedding against the embedding of any already-picked candidate.
"""

from __future__ import annotations

from mnemo.compress import ScoredHit
from mnemo.rerank import mmr_select
from mnemo.store import Node


def _node(name: str) -> Node:
    return Node.new(
        type="memory_feedback",
        name=name,
        body="b",
        source_path=f"/{name}.md",
        source_kind="memory_dir",
    )


def _hit(name: str, score: float, chunk_idx: int = 0) -> ScoredHit:
    return ScoredHit(
        node=_node(name),
        score=score,
        chunk_idx=chunk_idx,
        chunk_text=f"chunk {chunk_idx}",
    )


def _vec(seed: float, dim: int = 384) -> list[float]:
    """Embedding where every component equals ``seed``. Cosine of two
    such vectors with same-sign seeds is 1.0 (parallel)."""
    return [seed] * dim


def _orthogonal_to_ones(dim: int = 384) -> list[float]:
    """A vector orthogonal to ``_vec(1.0)`` (all-ones). Half +1, half -1
    so the dot product cancels out to 0. Lets a test dial cosine = 0
    between two candidates."""
    return [1.0 if i % 2 == 0 else -1.0 for i in range(dim)]


def test_mmr_select_lambda_one_is_pure_relevance_sort() -> None:
    """lambda_=1.0 disables the diversity penalty entirely -- the
    output is the input order truncated to k. Matches today's
    pre-v1.2 behavior; the design treats it as the bypass case."""
    a, b, c = _hit("a", 0.9), _hit("b", 0.7), _hit("c", 0.5)
    # Pool intentionally pre-sorted descending by score.
    result = mmr_select([a, b, c], k=2, lambda_=1.0, embeddings={})
    assert [h.node.name for h in result] == ["a", "b"]


def test_mmr_select_empty_pool() -> None:
    assert mmr_select([], k=5, lambda_=0.7, embeddings={}) == []


def test_mmr_select_pool_smaller_than_k_returns_pool() -> None:
    """If we only have 2 candidates and the caller asked for 5, the
    output is just the 2 candidates -- no diversification needed,
    no padding."""
    pool = [_hit("a", 0.9), _hit("b", 0.7)]
    result = mmr_select(pool, k=5, lambda_=0.7, embeddings={})
    assert [h.node.name for h in result] == ["a", "b"]


def test_mmr_select_diversifies_near_duplicates() -> None:
    """The headline test: a pool of 3 candidates where the top-2 are
    near-duplicates and the 3rd is distinct should produce a top-2
    output that includes the 3rd (more diverse) over the 2nd
    near-duplicate."""
    a, b, c = _hit("a", 0.90), _hit("b", 0.85), _hit("c", 0.50)
    # a and b share the same embedding -> cosine 1.0 with each other.
    # c is built to be orthogonal to that embedding -> cosine 0 with both.
    embeddings = {
        a.node.id: _vec(1.0),
        b.node.id: _vec(1.0),
        c.node.id: _orthogonal_to_ones(),
    }

    # With lambda = 0.5, the diversity penalty (1 - 0.5) = 0.5 weighs
    # max_sim heavily enough to push b out:
    #   pick a (highest relevance, no penalty yet)
    #   then choose between b and c:
    #     b: 0.5 * 1.0 (relevance normalized) - 0.5 * 1.0 (cosine to a) = 0.0
    #     c: 0.5 * 0.0 (lowest relevance norm) - 0.5 * 0.0 (orthogonal) = 0.0
    # Tie; with deterministic tiebreak first-pick wins -> b. So we
    # need lambda lower than 0.5 OR we need c's relevance to win.
    # Push lambda to 0.3 so diversity dominates.
    result = mmr_select([a, b, c], k=2, lambda_=0.3, embeddings=embeddings)
    names = [h.node.name for h in result]
    assert names == ["a", "c"]  # not [a, b]


def test_mmr_select_falls_back_to_relevance_when_no_embeddings() -> None:
    """If we have no embeddings (degenerate state where vec_search
    didn't return chunks for any node, e.g. embedder failure), MMR
    should still return SOMETHING -- the pure relevance order
    truncated to k. Without this fallback the query path would
    silently drop hits."""
    pool = [_hit("a", 0.9), _hit("b", 0.7), _hit("c", 0.5)]
    result = mmr_select(pool, k=2, lambda_=0.7, embeddings={})
    assert [h.node.name for h in result] == ["a", "b"]


def test_mmr_select_partial_embeddings_no_crash() -> None:
    """Some hits have embeddings, others don't (e.g. one node hit by
    name match only without a vec chunk). The detector must not
    crash and should treat the missing ones as zero-similarity to
    everyone."""
    a, b, c = _hit("a", 0.9), _hit("b", 0.7), _hit("c", 0.5)
    embeddings = {a.node.id: _vec(1.0)}  # only a has an embedding
    result = mmr_select([a, b, c], k=3, lambda_=0.7, embeddings=embeddings)
    # All three picked, order may shift but length matches.
    assert len(result) == 3
    assert {h.node.name for h in result} == {"a", "b", "c"}


def test_mmr_select_deterministic_tiebreak_picks_first() -> None:
    """When two candidates have identical MMR scores, the
    earlier-in-pool one wins. Preserves the existing sort order
    for non-diversity-relevant decisions, makes test output stable."""
    a = _hit("a", 0.9)
    b = _hit("b", 0.9)
    c = _hit("c", 0.9)  # identical scores
    # All parallel embeddings (cosine 1.0 to each other) -> identical MMR.
    embeddings = {a.node.id: _vec(1.0), b.node.id: _vec(1.0), c.node.id: _vec(1.0)}
    result = mmr_select([a, b, c], k=2, lambda_=0.7, embeddings=embeddings)
    # First pick is the first by position; second pick is whichever
    # remaining candidate appears earliest (deterministic).
    assert [h.node.name for h in result] == ["a", "b"]
