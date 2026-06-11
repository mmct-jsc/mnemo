"""v5.27.0 step 1: the FTS5/BM25 lexical channel in the Store.

A lexically-perfect node that is not in the vector top-40 was invisible
to retrieval (the old substring scorer only RESCORED existing candidates).
``nodes_fts`` gives lexical RECALL: synced on upsert/delete, backfilled on
first open of a pre-v5.27 store, queryable via ``bm25_search``.
"""

from __future__ import annotations

from pathlib import Path

from mnemo.store import Node, Store


def _node(name: str, body: str, path: str) -> Node:
    return Node.new(
        type="code_function",
        name=name,
        description=f"{name} helper",
        body=body,
        source_path=path,
        source_kind="code_repo",
    )


def test_bm25_search_finds_lexical_match(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    store.upsert_node(_node("format_statusline", "def format_statusline(): ...", "/a.py"))
    store.upsert_node(_node("unrelated_thing", "completely different text", "/b.py"))
    hits = store.bm25_search("where is format_statusline defined", k=5)
    assert hits, "BM25 must surface the lexical match"
    top_id, rank = hits[0]
    assert store.get_node(top_id).name == "format_statusline"
    assert rank == 0, "rank positions are 0-based"
    store.close()


def test_bm25_search_orders_by_relevance(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    store.upsert_node(_node("alpha", "statusline statusline statusline", "/a.py"))
    store.upsert_node(_node("beta", "statusline once, then prose about other topics", "/b.py"))
    hits = store.bm25_search("statusline", k=5)
    names = [store.get_node(nid).name for nid, _ in hits]
    assert names[0] == "alpha", "term-dense doc must rank first under BM25"
    store.close()


def test_bm25_search_handles_garbage_and_empty(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    assert store.bm25_search("", k=5) == []
    assert store.bm25_search('"unbalanced AND (', k=5) == []  # FTS-safe escaping
    store.close()


def test_fts_sync_on_update_and_delete(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    n = _node("gamma_rotor", "original body", "/g.py")
    store.upsert_node(n)
    assert store.bm25_search("gamma_rotor", k=5)
    # update: body change must be reflected
    n.body = "renamed to delta_flux entirely"
    n.hash = "changed"
    store.upsert_node(n)
    assert any(nid == n.id for nid, _ in store.bm25_search("delta_flux", k=5))
    # delete: gone from the index
    store.delete_node(n.id)
    assert store.bm25_search("delta_flux", k=5) == []
    store.close()


def test_fts_backfills_existing_store_on_open(tmp_path: Path) -> None:
    """A pre-v5.27 store (nodes present, no FTS rows) must backfill once."""
    db = tmp_path / "t.db"
    store = Store(db)
    store.upsert_node(_node("epsilon_gear", "body text", "/e.py"))
    # simulate a pre-FTS store: drop the fts table entirely
    store.conn.execute("DROP TABLE IF EXISTS nodes_fts")
    store.conn.commit()
    store.close()

    reopened = Store(db)
    hits = reopened.bm25_search("epsilon_gear", k=5)
    assert hits, "reopening must recreate + backfill nodes_fts"
    reopened.close()


def test_fts_body_is_capped(tmp_path: Path) -> None:
    """Only the first 32 KB of a body is indexed (mirrors the lexical cap)."""
    store = Store(tmp_path / "t.db")
    huge = ("filler " * 8000) + " needleterm"  # needle far past 32 KB
    store.upsert_node(_node("zeta_node", huge, "/z.py"))
    assert store.bm25_search("needleterm", k=5) == [], "terms past the cap are not indexed"
    assert store.bm25_search("filler", k=5), "terms inside the cap are indexed"
    store.close()
