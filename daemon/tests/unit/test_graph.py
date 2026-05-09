"""Tests for graph: frontmatter inference, co-occurrence, proximity scores."""

from __future__ import annotations

import json

import pytest

from mnemo.graph import (
    CO_OCCUR_INCREMENT,
    CO_OCCUR_MAX_WEIGHT,
    HOP_DECAY,
    compute_graph_scores,
    infer_edges_from_frontmatter,
    update_co_occurrence,
)
from mnemo.store import Node, Store


def _node(**kw: object) -> Node:
    defaults = {
        "type": "memory_project",
        "name": "n",
        "body": "b",
        "source_path": "/x.md",
        "source_kind": "memory_dir",
    }
    defaults.update(kw)
    return Node.new(**defaults)  # type: ignore[arg-type]


# --- infer_edges_from_frontmatter ----------------------------------------


def test_infer_edges_no_frontmatter_returns_zero(store: Store) -> None:
    n = _node(source_path="/x.md", frontmatter_json=None)
    store.upsert_node(n)
    assert infer_edges_from_frontmatter(store, n) == 0


def test_infer_applies_to_by_name(store: Store) -> None:
    target = _node(name="other", source_path="/o.md")
    store.upsert_node(target)
    fm = json.dumps({"appliesTo": ["other"]})
    src = _node(source_path="/s.md", frontmatter_json=fm)
    store.upsert_node(src)

    n = infer_edges_from_frontmatter(store, src)
    assert n == 1
    edges = store.get_edges(src_id=src.id, relation="applies_to")
    assert len(edges) == 1
    assert edges[0].dst_id == target.id
    assert edges[0].source == "frontmatter"


def test_infer_supersedes(store: Store) -> None:
    old = _node(name="old", source_path="/old.md")
    store.upsert_node(old)
    fm = json.dumps({"supersedes": ["old"]})
    new = _node(source_path="/new.md", frontmatter_json=fm)
    store.upsert_node(new)
    assert infer_edges_from_frontmatter(store, new) == 1
    edges = store.get_edges(src_id=new.id, relation="supersedes")
    assert len(edges) == 1


def test_infer_unresolvable_target_silently_dropped(store: Store) -> None:
    fm = json.dumps({"appliesTo": ["nonexistent_target"]})
    src = _node(source_path="/s.md", frontmatter_json=fm)
    store.upsert_node(src)
    assert infer_edges_from_frontmatter(store, src) == 0


def test_infer_skips_self_reference(store: Store) -> None:
    src = _node(name="self", source_path="/s.md")
    store.upsert_node(src)
    fm = json.dumps({"appliesTo": ["self"]})
    src.frontmatter_json = fm
    store.upsert_node(src)
    assert infer_edges_from_frontmatter(store, src) == 0


def test_infer_handles_malformed_json(store: Store) -> None:
    src = _node(source_path="/s.md", frontmatter_json="not valid json {")
    store.upsert_node(src)
    assert infer_edges_from_frontmatter(store, src) == 0


def test_infer_handles_non_list_field(store: Store) -> None:
    fm = json.dumps({"appliesTo": "single string, not list"})
    src = _node(source_path="/s.md", frontmatter_json=fm)
    store.upsert_node(src)
    assert infer_edges_from_frontmatter(store, src) == 0


# --- update_co_occurrence -------------------------------------------------


def test_update_co_occurrence_creates_pair_edges(store: Store) -> None:
    a, b, c = (_node(source_path=f"/{x}.md") for x in "abc")
    for n in (a, b, c):
        store.upsert_node(n)
    update_co_occurrence(store, [a.id, b.id, c.id])
    # 3 pairs * 2 directions = 6
    total = (
        len(store.get_edges(src_id=a.id, relation="co_occurs_with"))
        + len(store.get_edges(src_id=b.id, relation="co_occurs_with"))
        + len(store.get_edges(src_id=c.id, relation="co_occurs_with"))
    )
    assert total == 6


def test_update_co_occurrence_strengthens_existing(store: Store) -> None:
    a = _node(source_path="/a.md")
    b = _node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    update_co_occurrence(store, [a.id, b.id])
    update_co_occurrence(store, [a.id, b.id])
    edges = store.get_edges(src_id=a.id, relation="co_occurs_with")
    assert len(edges) == 1
    assert edges[0].weight == pytest.approx(2 * CO_OCCUR_INCREMENT)


def test_update_co_occurrence_caps_at_max(store: Store) -> None:
    a = _node(source_path="/a.md")
    b = _node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    for _ in range(200):
        update_co_occurrence(store, [a.id, b.id])
    edges = store.get_edges(src_id=a.id, relation="co_occurs_with")
    assert edges[0].weight == pytest.approx(CO_OCCUR_MAX_WEIGHT)


def test_update_co_occurrence_dedupes_input(store: Store) -> None:
    a = _node(source_path="/a.md")
    b = _node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    # Same id twice should not create self-edges.
    update_co_occurrence(store, [a.id, a.id, b.id])
    self_edges = store.get_edges(src_id=a.id, dst_id=a.id, relation="co_occurs_with")
    assert self_edges == []


# --- compute_graph_scores ------------------------------------------------


def test_compute_graph_scores_excludes_candidates(store: Store) -> None:
    a = _node(source_path="/a.md")
    b = _node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    store.add_edge(a.id, b.id, "applies_to", weight=1.0)
    scores = compute_graph_scores(store, {a.id: 0.9})
    assert a.id not in scores  # candidate excluded
    assert b.id in scores
    assert scores[b.id] == pytest.approx(0.9 * HOP_DECAY * 1.0)


def test_compute_graph_scores_walks_symmetric_relations(store: Store) -> None:
    # B has an outgoing applies_to edge to A; querying with A as candidate
    # should still surface B because applies_to is symmetric for proximity.
    a = _node(source_path="/a.md")
    b = _node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    store.add_edge(b.id, a.id, "applies_to", weight=1.0)
    scores = compute_graph_scores(store, {a.id: 1.0})
    assert b.id in scores


def test_compute_graph_scores_capped_at_one(store: Store) -> None:
    a = _node(source_path="/a.md")
    b = _node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    # Many heavy edges to inflate the raw sum past 1.0
    store.add_edge(a.id, b.id, "co_occurs_with", weight=10.0)
    scores = compute_graph_scores(store, {a.id: 1.0})
    assert scores[b.id] == pytest.approx(1.0)


def test_compute_graph_scores_supersedes_only_outgoing(store: Store) -> None:
    a = _node(source_path="/a.md")
    b = _node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    # B supersedes A. Querying with B as candidate -> A surfaces (outgoing).
    store.add_edge(b.id, a.id, "supersedes", weight=1.0)
    forward = compute_graph_scores(store, {b.id: 1.0})
    assert a.id in forward
    # Querying with A as candidate -> B should NOT surface (supersedes is asymmetric).
    backward = compute_graph_scores(store, {a.id: 1.0})
    assert b.id not in backward


def test_compute_graph_scores_empty_inputs(store: Store) -> None:
    assert compute_graph_scores(store, {}) == {}
