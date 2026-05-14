"""Tests for ``mnemo.retrieve._lexical_score``.

v2.6.0 polish: the lexical scorer must consider the node body, not
just name + description. Without this, a verbose handover doc that
literally contains every distinctive query term loses to a popular
short doc with zero keyword overlap (the graph-edge boost dominates).

The fix: walk name + description + body with a size cap on body so
truly enormous docs don't slow the scorer.
"""

from __future__ import annotations

from mnemo.retrieve import _lexical_score, _tokenize
from mnemo.store import Node


def _make_node(name: str, description: str, body: str) -> Node:
    return Node.new(
        type="memory_feedback",
        name=name,
        body=body,
        source_path=f"/test/{name}.md",
        source_kind="memory_dir",
        description=description,
    )


def test_lexical_score_returns_zero_for_no_overlap() -> None:
    node = _make_node(
        "alpine-double-init",
        "Alpine.js auto-invokes init() on x-data",
        "The body talks about Alpine, x-data, and the init pattern.",
    )
    score = _lexical_score(_tokenize("workspace retrieval chips"), node)
    assert score == 0.0


def test_lexical_score_counts_name_description_matches() -> None:
    node = _make_node(
        "workspace-design",
        "v2.6 workspace + retrieval scope abstraction",
        "Body without the query terms.",
    )
    # tokens: workspace, retrieval -- both in description.
    score = _lexical_score(_tokenize("workspace retrieval"), node)
    assert score == 1.0


def test_lexical_score_counts_body_matches() -> None:
    """v2.6.0 polish: body MUST be part of the haystack.

    Without this, the verbose v2.6 handover (whose body holds every
    distinctive term) loses to a short feedback doc with zero overlap.
    """
    node = _make_node(
        "handover",
        "v2.6 shipped",
        "Body covers chips, workspace creation, the retrieval rescore "
        "and the active-project pill removal polish.",
    )
    # Tokens from prompt: chips, workspace, retrieval, polish.
    # Only "workspace" appears in description -- the rest are body-only.
    score = _lexical_score(_tokenize("chips workspace retrieval polish"), node)
    # 4/4 should match now that body is in the haystack.
    assert score == 1.0


def test_lexical_score_partial_body_hit() -> None:
    node = _make_node(
        "handover",
        "design doc",
        "The body mentions only the active workspace path and retrieval scope.",
    )
    # tokens: chips, workspace, retrieval, polish. Only workspace +
    # retrieval are in the body; chips + polish are not.
    score = _lexical_score(_tokenize("chips workspace retrieval polish"), node)
    assert score == 0.5


def test_lexical_score_handles_empty_haystack() -> None:
    node = _make_node("no-content", "", "")
    score = _lexical_score(_tokenize("anything goes here"), node)
    assert score == 0.0


def test_lexical_score_caps_body_for_size() -> None:
    """An enormous body should not slow the scorer. The match
    semantics are preserved within the cap; matches past the cap
    are missed (the trade-off the cap explicitly accepts)."""
    # Build a 500 KB body. The cap is 32 KB; a term placed at byte
    # 100_000 should not appear in the haystack.
    far_body = ("filler word " * 8000) + "deeplyhiddenmagicword"
    node = _make_node("big-doc", "", far_body)
    score = _lexical_score(_tokenize("deeplyhiddenmagicword"), node)
    # Past the cap -> miss.
    assert score == 0.0
    # But a term in the first 32 KB hits normally.
    node2 = _make_node("big-doc-2", "", "magicstart " + "filler " * 8000)
    score2 = _lexical_score(_tokenize("magicstart"), node2)
    assert score2 == 1.0


def test_lexical_score_normalizes_to_zero_one_range() -> None:
    """Even with many matches, score caps at 1.0."""
    node = _make_node(
        "many-matches",
        "workspace retrieval chips polish add UX v2.6",
        "workspace retrieval chips polish add UX v2.6 more text",
    )
    score = _lexical_score(_tokenize("workspace retrieval chips polish"), node)
    assert 0.0 <= score <= 1.0
    assert score == 1.0


def test_lexical_score_returns_zero_for_empty_query() -> None:
    node = _make_node("anything", "anything", "anything")
    assert _lexical_score([], node) == 0.0
