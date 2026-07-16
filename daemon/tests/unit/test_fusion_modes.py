"""Fusion rebalance step 2: the ``fusion_mode`` seam.

A first probe on the 42-query LEXICAL set alone (fusion isolated) read:

    vec 0.55 | bm25 0.81 | naive RRF 0.67 | production 6-term sum 0.62  (hit@5)

and pointed at a BM25-led fusion. This seam made the relevance core
swappable so that hypothesis could be MEASURED per query_type rather than
argued about -- and on the expanded 80-query set it was overturned:

                     lexical  conceptual  overall   (hit@5, one pinned corpus)
    weighted_sum       0.500     0.132     0.325
    bm25_lead          0.690     0.079     0.400    <- regresses conceptual
    weighted_rrf       0.881     0.184     0.550    <- wins both halves

- ``weighted_rrf``  (default) -- rank-level RRF, scale-robust, bm25 >> vector.
- ``weighted_sum``  -- the historical blend; escape hatch + baseline.
- ``bm25_lead``     -- BM25 leads; vector is recall-fallback + tie-break.

Naive EQUAL-weight RRF really was wrong (the probe was right about that),
but that is a statement about the weighting, not about RRF: weighting it
toward bm25 beats bm25_lead on both halves.

The CONTEXT terms (graph/recency/type/project) and the multiplicative
finishers (exact-name boost, isolation penalty) are orthogonal and proven,
so every mode leaves them untouched. Only vector-vs-lexical fusion moves.
"""

from __future__ import annotations

import pytest

from mnemo import config, retrieve
from mnemo.config import Config

REL_MAX = 0.60  # alpha (0.40) + zeta (0.20): the relevance band, mode-invariant


def _cfg(mode: str, **over: object) -> Config:
    c = Config()
    c.fusion_mode = mode
    for k, v in over.items():
        setattr(c, k, v)
    return c


# --- config plumbing --------------------------------------------------------


def test_fusion_mode_defaults_to_the_measured_winner() -> None:
    """weighted_rrf won BOTH halves of the expanded set (lexical
    0.500->0.881, conceptual 0.132->0.184 hit@5). bm25_lead -- the mode the
    lexical-only probe pointed at -- regressed conceptual to 0.079 and is
    NOT the default."""
    assert Config().fusion_mode == "weighted_rrf"
    assert config.DEFAULT_FUSION_MODE == "weighted_rrf"


def test_config_parses_known_fusion_mode() -> None:
    c = Config()
    from mnemo import config as cfgmod

    cfgmod._apply(c, {"fusion_mode": " BM25_Lead "})
    assert c.fusion_mode == "bm25_lead"


def test_config_rejects_unknown_fusion_mode() -> None:
    c = Config()
    from mnemo import config as cfgmod

    cfgmod._apply(c, {"fusion_mode": "quantum"})
    assert c.fusion_mode == config.DEFAULT_FUSION_MODE, "an unknown mode must not take effect"


# --- weighted_sum: the default must be EXACTLY today's formula -------------


def test_weighted_sum_is_alpha_vector_plus_zeta_lexical() -> None:
    cfg = _cfg("weighted_sum")
    got = retrieve.relevance_score(cfg, vector=0.5, lexical=0.25, bm25_rank=None, vec_rank=0)
    assert got == pytest.approx(0.40 * 0.5 + 0.20 * 0.25)


def test_weighted_sum_ignores_ranks() -> None:
    """Ranks are a bm25_lead/rrf concern; the legacy sum reads the scores only."""
    cfg = _cfg("weighted_sum")
    a = retrieve.relevance_score(cfg, vector=0.5, lexical=0.25, bm25_rank=0, vec_rank=0)
    b = retrieve.relevance_score(cfg, vector=0.5, lexical=0.25, bm25_rank=39, vec_rank=39)
    assert a == b


def test_unknown_mode_falls_back_to_the_shipped_default() -> None:
    """Fail-open: a bad config value must never break retrieval, and must
    land on the shipped default rather than some third behaviour."""
    kw = {"vector": 0.5, "lexical": 0.25, "bm25_rank": 2, "vec_rank": 1}
    bad = retrieve.relevance_score(_cfg("nonsense"), **kw)
    default = retrieve.relevance_score(_cfg(config.DEFAULT_FUSION_MODE), **kw)
    assert bad == pytest.approx(default)


# --- bm25_lead --------------------------------------------------------------


def test_bm25_lead_top_bm25_hit_beats_perfect_vector_only_node() -> None:
    """The whole point: a name-exact BM25 hit outranks a merely-similar node."""
    cfg = _cfg("bm25_lead")
    bm25_top = retrieve.relevance_score(cfg, vector=0.0, lexical=0.0, bm25_rank=0, vec_rank=None)
    vec_only = retrieve.relevance_score(cfg, vector=1.0, lexical=0.0, bm25_rank=None, vec_rank=0)
    assert bm25_top > vec_only


def test_bm25_lead_keeps_vector_as_recall_fallback() -> None:
    """A BM25-miss must still score -- vector is the fallback, not erased.
    Erasing it would make conceptual queries unanswerable."""
    cfg = _cfg("bm25_lead")
    assert retrieve.relevance_score(cfg, vector=0.9, lexical=0.0, bm25_rank=None, vec_rank=0) > 0.0


def test_bm25_lead_respects_bm25_rank_order() -> None:
    cfg = _cfg("bm25_lead")
    r0 = retrieve.relevance_score(cfg, vector=0.0, lexical=0.0, bm25_rank=0, vec_rank=None)
    r5 = retrieve.relevance_score(cfg, vector=0.0, lexical=0.0, bm25_rank=5, vec_rank=None)
    assert r0 > r5


def test_bm25_lead_band_is_normalized_to_the_relevance_max() -> None:
    """Modes must share one relevance band or the context terms silently
    change weight when the mode flips."""
    cfg = _cfg("bm25_lead")
    both = retrieve.relevance_score(cfg, vector=1.0, lexical=1.0, bm25_rank=0, vec_rank=0)
    assert both == pytest.approx(REL_MAX)


def test_bm25_lead_weight_is_tunable() -> None:
    cfg = _cfg("bm25_lead", bm25_lead_weight=1.0)
    vec_only = retrieve.relevance_score(cfg, vector=1.0, lexical=0.0, bm25_rank=None, vec_rank=0)
    assert vec_only == pytest.approx(0.0), "weight 1.0 = pure BM25, no vector share"


# --- weighted_rrf -----------------------------------------------------------


def test_weighted_rrf_is_rank_level_not_score_level() -> None:
    """RRF reads RANKS: the raw cosine must not change the result."""
    cfg = _cfg("weighted_rrf")
    a = retrieve.relevance_score(cfg, vector=0.99, lexical=0.0, bm25_rank=3, vec_rank=2)
    b = retrieve.relevance_score(cfg, vector=0.11, lexical=0.0, bm25_rank=3, vec_rank=2)
    assert a == b


def test_weighted_rrf_top_of_both_hits_the_relevance_max() -> None:
    cfg = _cfg("weighted_rrf")
    got = retrieve.relevance_score(cfg, vector=1.0, lexical=1.0, bm25_rank=0, vec_rank=0)
    assert got == pytest.approx(REL_MAX)


def test_weighted_rrf_weights_bm25_over_vector() -> None:
    """The measured lesson: equal-weight RRF (0.67) lost to bm25-alone (0.81).
    Weighting bm25 above vector is what makes RRF viable here at all."""
    cfg = _cfg("weighted_rrf")
    bm25_only = retrieve.relevance_score(cfg, vector=0.0, lexical=0.0, bm25_rank=0, vec_rank=None)
    vec_only = retrieve.relevance_score(cfg, vector=1.0, lexical=0.0, bm25_rank=None, vec_rank=0)
    assert bm25_only > vec_only


def test_weighted_rrf_decays_with_rank() -> None:
    cfg = _cfg("weighted_rrf")
    r0 = retrieve.relevance_score(cfg, vector=0.0, lexical=0.0, bm25_rank=0, vec_rank=None)
    r9 = retrieve.relevance_score(cfg, vector=0.0, lexical=0.0, bm25_rank=9, vec_rank=None)
    assert r0 > r9


# --- shared invariants ------------------------------------------------------


@pytest.mark.parametrize("mode", ["weighted_sum", "bm25_lead", "weighted_rrf"])
def test_no_signal_scores_zero(mode: str) -> None:
    cfg = _cfg(mode)
    got = retrieve.relevance_score(cfg, vector=0.0, lexical=0.0, bm25_rank=None, vec_rank=None)
    assert got == pytest.approx(0.0)


@pytest.mark.parametrize("mode", ["weighted_sum", "bm25_lead", "weighted_rrf"])
def test_relevance_never_exceeds_the_band(mode: str) -> None:
    """A mode that overflows the band would drown the context terms."""
    cfg = _cfg(mode)
    got = retrieve.relevance_score(cfg, vector=1.0, lexical=1.0, bm25_rank=0, vec_rank=0)
    assert got <= REL_MAX + 1e-9


@pytest.mark.parametrize("mode", ["weighted_sum", "bm25_lead", "weighted_rrf"])
def test_relevance_is_non_negative(mode: str) -> None:
    cfg = _cfg(mode)
    assert retrieve.relevance_score(cfg, vector=0.0, lexical=0.0, bm25_rank=39, vec_rank=39) >= 0.0
