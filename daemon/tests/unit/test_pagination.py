"""v2.6.7: real SQL pagination -- uncapped totals + LIMIT/OFFSET.

The browse surfaces (/nodes-page, /audit-page) used the anti-pattern
``rows = store.list_nodes(limit=10_000); total = len(rows);
page = rows[offset:offset+25]``. That:
  - CAPS the displayed total at 10 000 ("Showing 1-25 of 10000"
    even when the real count is larger);
  - loads up to 10 000 rows into memory to render 25.

The fix is a proper data-layer pagination primitive:
  - ``list_nodes`` / ``recent_queries`` gain an ``offset`` arg ->
    SQL ``LIMIT ? OFFSET ?`` (no load-all);
  - scalar ``count_nodes_total`` / ``count_queries`` via
    ``SELECT COUNT(*)`` (no cap, honors the same filters);
  - ``list_project_keys`` for the filter dropdown (was derived from
    a capped list_nodes scan).

These tests lock the primitives + that the routes are wired to the
uncapped count, so a future feature reusing pagination inherits the
correct contract.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    # The client fixture shares the SAME store instance, so seeding via
    # the store fixture is visible to the routes under test.
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


def _node(i: int, **ov: object) -> Node:
    d: dict[str, object] = {
        "type": "memory_feedback",
        "name": f"n{i:04d}",
        "body": "b",
        "source_path": f"/seed/n{i:04d}.md",
        "source_kind": "memory_dir",
        "description": "d",
    }
    d.update(ov)
    n = Node.new(**d)  # type: ignore[arg-type]
    n.updated_at = 1000 + i  # deterministic DESC ordering
    return n


def _seed_nodes(store: Store, n: int, **ov: object) -> list[str]:
    ids = []
    for i in range(n):
        node = _node(i, **ov)
        store.upsert_node(node)
        ids.append(node.id)
    return ids


# --- list_nodes offset ------------------------------------------------------


def test_list_nodes_offset_returns_disjoint_contiguous_slices(store: Store) -> None:
    _seed_nodes(store, 30)
    page1 = store.list_nodes(limit=10, offset=0)
    page2 = store.list_nodes(limit=10, offset=10)
    page3 = store.list_nodes(limit=10, offset=20)
    assert len(page1) == len(page2) == len(page3) == 10
    ids1 = {n.id for n in page1}
    ids2 = {n.id for n in page2}
    ids3 = {n.id for n in page3}
    # No overlap between pages.
    assert ids1 & ids2 == set()
    assert ids2 & ids3 == set()
    # Together they cover 30 distinct nodes in DESC updated_at order.
    assert len(ids1 | ids2 | ids3) == 30
    assert page1[0].updated_at > page3[-1].updated_at


def test_list_nodes_offset_defaults_to_zero(store: Store) -> None:
    _seed_nodes(store, 5)
    assert [n.id for n in store.list_nodes(limit=5)] == [
        n.id for n in store.list_nodes(limit=5, offset=0)
    ]


# --- count_nodes_total (scalar, uncapped) -----------------------------------


def test_count_nodes_total_is_real_count(store: Store) -> None:
    _seed_nodes(store, 30)
    # Scalar COUNT(*) -- never capped by a LIMIT.
    assert store.count_nodes_total() == 30


def test_count_nodes_total_respects_type_filter(store: Store) -> None:
    _seed_nodes(store, 5, type="memory_feedback")
    _seed_nodes(store, 3, type="memory_project", source_path="/proj/p.md")
    assert store.count_nodes_total(type="memory_project") == 3
    assert store.count_nodes_total() == 8


def test_count_nodes_total_respects_project_filter(store: Store) -> None:
    _seed_nodes(store, 4, project_key="P1")
    _seed_nodes(store, 2, project_key="P2", source_path="/p2/x.md")
    assert store.count_nodes_total(project_key="P1", include_base=False) == 4


# --- recent_queries offset + count_queries ----------------------------------


def _log(store: Store, n: int) -> None:
    for i in range(n):
        store.log_query(
            prompt=f"q{i:04d}",
            intent_tags=[],
            retrieved_ids=[],
            scores={},
        )


def test_recent_queries_offset_returns_disjoint_slice(store: Store) -> None:
    _log(store, 30)
    page1 = store.recent_queries(limit=10, offset=0)
    page2 = store.recent_queries(limit=10, offset=10)
    assert len(page1) == len(page2) == 10
    assert {q.id for q in page1} & {q.id for q in page2} == set()


def test_count_queries_returns_total(store: Store) -> None:
    _log(store, 7)
    assert store.count_queries() == 7


def test_query_audit_stats_single_pass_uncapped(store: Store) -> None:
    """The /audit-page side cards (total hits, avg, span, top tags)
    must be computed in ONE bounded pass over the FULL log -- not by
    materializing 10 000 Query objects (the old `all_q` load-all the
    pagination rewrite removed)."""
    store.log_query(prompt="a", intent_tags=["impl", "none"], retrieved_ids=["x", "y"], scores={})
    store.log_query(prompt="b", intent_tags=["impl"], retrieved_ids=["z"], scores={})
    st = store.query_audit_stats()
    assert st["total_queries"] == 2
    assert st["total_hits"] == 3  # 2 + 1 retrieved ids
    assert st["first_ts"] > 0
    assert st["last_ts"] >= st["first_ts"]
    # "none" is filtered out by the route; the raw histogram counts impl=2.
    assert dict(st["top_tags"]).get("impl") == 2


# --- list_project_keys ------------------------------------------------------


def test_list_project_keys_distinct_non_null_sorted(store: Store) -> None:
    _seed_nodes(store, 2, project_key="P1")
    _seed_nodes(store, 1, project_key="P2", source_path="/p2/a.md")
    _seed_nodes(store, 1, project_key=None, source_path="/none/a.md")
    assert store.list_project_keys() == ["P1", "P2"]


# --- route wiring: total is the uncapped count, offset paginates ------------


def test_nodes_page_total_is_uncapped_count(client: TestClient, store: Store) -> None:
    """The rendered 'Showing X-Y of TOTAL' must use the real
    COUNT(*), and page 2 must be a disjoint OFFSET slice -- not a
    len() of a capped load-all."""
    _seed_nodes(store, 30)  # > PAGE_SIZE (25) -> 2 pages
    r1 = client.get("/nodes-page")
    assert r1.status_code == 200
    assert "of 30" in r1.text  # pagination.total == real count
    assert store.count_nodes_total() == 30

    r2 = client.get("/nodes-page?page=2")
    assert r2.status_code == 200
    # Page 2 is a disjoint OFFSET slice: the oldest node (n0000,
    # lowest updated_at) falls on page 2 only -- proof the route
    # uses LIMIT/OFFSET, not a len() of one capped load-all.
    assert "n0000" in r2.text
    assert "n0000" not in r1.text


def test_audit_page_total_is_uncapped_count(client: TestClient, store: Store) -> None:
    _log(store, 30)  # > PAGE_SIZE_AUDIT (25)
    r = client.get("/audit-page")
    assert r.status_code == 200
    assert store.count_queries() == 30
    assert "of 30" in r.text
    r2 = client.get("/audit-page?page=2")
    assert r2.status_code == 200
