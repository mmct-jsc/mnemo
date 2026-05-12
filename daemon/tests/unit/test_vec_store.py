"""Tests for vec_chunks / chunk_meta and the vec_search API.

Vectors here are fake (hand-crafted unit vectors along basis axes) so we can
make precise assertions about distances without loading a real model.
"""

from __future__ import annotations

import pytest

from mnemo.store import EMBEDDING_DIM, Node, Store


def _basis(i: int) -> list[float]:
    """Unit vector along axis ``i`` of length ``EMBEDDING_DIM``."""
    v = [0.0] * EMBEDDING_DIM
    v[i] = 1.0
    return v


def _node(**kw: object) -> Node:
    defaults = {
        "type": "memory_project",
        "name": "n",
        "body": "body",
        "source_path": "/x.md",
        "source_kind": "memory_dir",
    }
    defaults.update(kw)
    return Node.new(**defaults)  # type: ignore[arg-type]


def test_ensure_vec_idempotent(store: Store) -> None:
    store.ensure_vec()
    store.ensure_vec()  # second call must not raise


def test_upsert_and_search_basic(store: Store) -> None:
    n = _node(source_path="/a.md")
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, _basis(0), "axis-0 chunk")])
    results = store.vec_search(_basis(0), k=5)
    assert len(results) == 1
    node_id, chunk_idx, text, distance = results[0]
    assert node_id == n.id
    assert chunk_idx == 0
    assert text == "axis-0 chunk"
    assert distance < 1e-6  # identical vector -> ~0


def test_search_orders_by_distance(store: Store) -> None:
    a = _node(source_path="/a.md")
    b = _node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    store.upsert_chunks(a.id, [(0, _basis(0), "A")])
    store.upsert_chunks(b.id, [(0, _basis(1), "B")])  # orthogonal to query
    results = store.vec_search(_basis(0), k=5)
    assert len(results) == 2
    # Closest first
    assert results[0][0] == a.id
    assert results[0][3] < results[1][3]


def test_upsert_chunks_replaces_existing(store: Store) -> None:
    n = _node(source_path="/a.md")
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, _basis(0), "v1")])
    store.upsert_chunks(n.id, [(0, _basis(1), "v2")])  # replace
    rows = store.vec_search(_basis(0), k=5)
    if rows:
        assert rows[0][2] == "v2"  # new chunk text


def test_upsert_chunks_multi_chunk(store: Store) -> None:
    n = _node(source_path="/a.md")
    store.upsert_node(n)
    store.upsert_chunks(
        n.id,
        [
            (0, _basis(0), "first"),
            (1, _basis(1), "second"),
            (2, _basis(2), "third"),
        ],
    )
    assert n.id in store.list_embedded_node_ids()
    results = store.vec_search(_basis(1), k=10)
    # All three chunks are findable.
    chunk_idxs = {r[1] for r in results}
    assert chunk_idxs == {0, 1, 2}


def test_delete_chunks_removes_only_target(store: Store) -> None:
    a = _node(source_path="/a.md")
    b = _node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    store.upsert_chunks(a.id, [(0, _basis(0), "A")])
    store.upsert_chunks(b.id, [(0, _basis(1), "B")])
    store.delete_chunks(a.id)
    embedded = store.list_embedded_node_ids()
    assert a.id not in embedded
    assert b.id in embedded


def test_delete_node_also_deletes_chunks(store: Store) -> None:
    n = _node(source_path="/a.md")
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, _basis(0), "A")])
    assert n.id in store.list_embedded_node_ids()
    store.delete_node(n.id)
    assert n.id not in store.list_embedded_node_ids()


def test_search_filters_by_type(store: Store) -> None:
    fb = _node(type="memory_feedback", source_path="/fb.md")
    pj = _node(type="memory_project", source_path="/pj.md")
    store.upsert_node(fb)
    store.upsert_node(pj)
    store.upsert_chunks(fb.id, [(0, _basis(0), "fb")])
    store.upsert_chunks(pj.id, [(0, _basis(0), "pj")])

    fb_only = store.vec_search(_basis(0), k=5, type_filter=["memory_feedback"])
    assert all(r[0] == fb.id for r in fb_only)


def test_search_filters_by_project(store: Store) -> None:
    p1 = _node(project_key="P1", source_path="/p1.md")
    p2 = _node(project_key="P2", source_path="/p2.md")
    store.upsert_node(p1)
    store.upsert_node(p2)
    store.upsert_chunks(p1.id, [(0, _basis(0), "1")])
    store.upsert_chunks(p2.id, [(0, _basis(0), "2")])

    p1_only = store.vec_search(_basis(0), k=5, project_key="P1")
    assert all(r[0] == p1.id for r in p1_only)


def test_search_rejects_wrong_dim(store: Store) -> None:
    with pytest.raises(ValueError, match="query dim"):
        store.vec_search([0.0, 1.0, 2.0], k=5)


def test_upsert_rejects_wrong_dim(store: Store) -> None:
    n = _node(source_path="/a.md")
    store.upsert_node(n)
    with pytest.raises(ValueError, match="vector dim"):
        store.upsert_chunks(n.id, [(0, [0.0, 1.0], "bad")])


def test_get_chunk_embeddings_bulk_roundtrip(store: Store) -> None:
    """v1.2 phase 4: MMR re-rank reads each candidate's best-chunk
    embedding back out so it can compute pairwise cosine. The bulk
    helper fetches in one query keyed on (node_id, chunk_idx)."""
    a = _node(source_path="/a.md")
    b = _node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    # Two chunks for a (idx 0 and 1), one for b (idx 0).
    store.upsert_chunks(a.id, [(0, _basis(0), "a0"), (1, _basis(1), "a1")])
    store.upsert_chunks(b.id, [(0, _basis(2), "b0")])

    got = store.get_chunk_embeddings([(a.id, 1), (b.id, 0)])

    assert set(got.keys()) == {(a.id, 1), (b.id, 0)}
    # Each embedding deserializes back to the EXACT vector we wrote
    # (float32 -> float64 widening; values still exact for unit
    # vectors).
    assert got[(a.id, 1)] == _basis(1)
    assert got[(b.id, 0)] == _basis(2)


def test_get_chunk_embeddings_missing_pair_omitted(store: Store) -> None:
    """A (node_id, chunk_idx) pair that doesn't exist (e.g. node was
    deleted between vec_search and MMR's read-back) simply doesn't
    appear in the result dict. MMR treats missing entries as
    zero-cosine."""
    n = _node(source_path="/a.md")
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, _basis(0), "a")])

    got = store.get_chunk_embeddings([(n.id, 99), ("ghost", 0)])
    assert got == {}


def test_get_chunk_embeddings_empty_input_short_circuits(store: Store) -> None:
    assert store.get_chunk_embeddings([]) == {}
