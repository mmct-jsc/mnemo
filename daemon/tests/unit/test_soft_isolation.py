"""v4.3.2: strict project-isolation must SOFT-penalize, not HARD-drop.

Root cause of the user's "the result seems wrong": retrieve.query()
hard-`continue`d every cross/inactive-project non-BASE candidate, so a
dramatically-stronger match (the v4 handover, sim 0.757) was INVISIBLE
and a weaker BASE node (0.529) won -- a strict-isolation silent-zero
(feedback_mnemo_silent_zero_modes; the v1.2.1 fix already softened the
symmetric project_key=None case).

Fix: a configurable multiplicative isolation penalty
(Config.project_isolation_penalty, default 0.85) -- out-of-scope
strict nodes are scored + ranked but deprioritized, never erased; a
dominantly-stronger match still wins.
"""

from __future__ import annotations

from mnemo import retrieve
from mnemo.config import Config
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder


def test_default_isolation_penalty_knob_exists() -> None:
    cfg = Config()
    assert hasattr(cfg, "project_isolation_penalty")
    assert 0.0 < cfg.project_isolation_penalty <= 1.0
    assert cfg.project_isolation_penalty == 0.85  # tuned default


def _seed(store: Store, embedder: FakeEmbedder, name: str, project_key, base, vec_text):
    n = Node.new(
        type="memory_project",
        name=name,
        body="content about " + name,
        source_path=f"/mem/{name}.md",
        source_kind="memory_dir",
        project_key=project_key,
        base=base,
    )
    store.upsert_node(n)
    # embed the chunk with vec_text so cosine vs the query is controllable
    store.upsert_chunks(n.id, [(0, embedder.embed_text(vec_text), n.body)])
    return n


def test_strict_isolation_soft_penalizes_not_hard_drops(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    """The silent-zero reproduction: a DOMINANT cross-project match
    (perfect cosine) must SURFACE under strict isolation with a foreign
    active project (was hard-dropped pre-v4.3.2)."""
    q = "exact handover contract refactor"
    # foreign project, NOT base, but a PERFECT vector match (its chunk
    # embeds the query text itself -> cosine 1.0). The analogue of the
    # v4 handover the user searched for.
    foreign = _seed(store, fake_embedder, "foreign-exact", "OTHER", False, q)
    # a BASE node, weaker match (different vec text) -- analogue of
    # reference-mnemo-pipelines.
    _seed(store, fake_embedder, "base-weak", None, True, "unrelated pipelines text")
    # an in-active-project node, also weaker match.
    _seed(store, fake_embedder, "in-scope", "P1", False, "unrelated in scope text")

    result = retrieve.query(store, fake_embedder, q, k=5, active_project="P1")
    surfaced = {h.node_id for h in result.hits}
    assert foreign.id in surfaced, (
        "strict isolation must SOFT-penalize, not hard-drop -- a "
        "dominant cross-project match must still surface (v4.3.2 "
        "silent-zero cure; the user's 'result seems wrong')."
    )
    # and being a perfect match, after only a 0.7 penalty it still
    # beats the weak BASE / in-scope nodes -> ranks #1 (the user
    # expected their handover at the top).
    assert result.hits[0].node_id == foreign.id, (
        "a dominant cross-project match should still rank top after the "
        "soft penalty (perfect cosine * 0.7 > weak BASE/in-scope)."
    )
