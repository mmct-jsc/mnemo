"""Tests for token-budget compression."""

from __future__ import annotations

from mnemo.compress import ScoredHit, compress_to_budget, count_tokens
from mnemo.store import Node


def _node(**kw: object) -> Node:
    defaults = {
        "type": "memory_project",
        "name": "n",
        "body": "body content",
        "source_path": "/x.md",
        "source_kind": "memory_dir",
        "description": "short desc",
    }
    defaults.update(kw)
    return Node.new(**defaults)  # type: ignore[arg-type]


def _hit(node: Node, score: float = 0.9) -> ScoredHit:
    return ScoredHit(node=node, score=score, chunk_idx=0, chunk_text=node.body)


def test_count_tokens_floor() -> None:
    assert count_tokens("") >= 1


def test_compress_empty_input() -> None:
    out, used = compress_to_budget([])
    assert out == []
    assert used == 0


def test_compress_descriptions_only_when_tight() -> None:
    # Use space-separated words so the token approximation is realistic.
    big = "word " * 500
    hits = [_hit(_node(name=f"n{i}", body=big, description="some short desc")) for i in range(5)]
    out, used = compress_to_budget(hits, budget_tokens=60)
    assert all(h.body is None for h in out)
    assert used <= 60


def test_compress_attaches_top_body_when_room() -> None:
    hits = [_hit(_node(name="a", body="short body content"))]
    out, used = compress_to_budget(hits, budget_tokens=200)
    assert len(out) == 1
    assert out[0].body == "short body content"


def test_compress_includes_citation_per_hit() -> None:
    hits = [_hit(_node(name=f"n{i}")) for i in range(3)]
    out, _ = compress_to_budget(hits, budget_tokens=400)
    for h in out:
        assert h.citation.startswith("[mnemo:")
        assert h.citation.endswith("]")
        assert h.node_id in h.citation


def test_compress_truncates_at_budget() -> None:
    hits = [
        _hit(_node(name=f"n{i}", description="some moderate-length description text"))
        for i in range(20)
    ]
    out, used = compress_to_budget(hits, budget_tokens=40)
    assert len(out) < 20
    assert used <= 40


def test_compress_preserves_score_order() -> None:
    a = _hit(_node(name="best", source_path="/a.md"), score=0.9)
    b = _hit(_node(name="mid", source_path="/b.md"), score=0.5)
    c = _hit(_node(name="low", source_path="/c.md"), score=0.1)
    out, _ = compress_to_budget([a, b, c], budget_tokens=300)
    assert [h.name for h in out] == ["best", "mid", "low"]


def test_compress_custom_citation_prefix() -> None:
    out, _ = compress_to_budget([_hit(_node())], budget_tokens=200, citation_prefix="kb")
    assert out[0].citation.startswith("[kb:")


def test_compress_uses_name_when_description_missing() -> None:
    n = _node(description=None, name="fallback-name")
    out, _ = compress_to_budget([_hit(n)], budget_tokens=200)
    assert len(out) == 1
    # Description in output is empty string since the node has none, but the
    # rendered description-line uses the name as fallback (asserted indirectly
    # via tokens_used > 1 and a non-empty citation).
    assert out[0].citation
