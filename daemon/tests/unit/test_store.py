"""Tests for the Store layer."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mnemo.store import SCHEMA_VERSION, Edge, Node, Store

# --- Schema ----------------------------------------------------------------


def test_schema_creates_on_open(tmp_path: Path) -> None:
    db = tmp_path / "mnemo.db"
    s = Store(db)
    assert s.schema_version() == SCHEMA_VERSION
    s.close()


def test_schema_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "mnemo.db"
    Store(db).close()
    Store(db).close()  # second open does not raise


# --- Node CRUD -------------------------------------------------------------


def _make_node(**overrides: object) -> Node:
    defaults = {
        "type": "memory_feedback",
        "name": "n",
        "body": "body text",
        "source_path": "/x.md",
        "source_kind": "memory_dir",
        "description": "desc",
    }
    defaults.update(overrides)
    return Node.new(**defaults)  # type: ignore[arg-type]


def test_upsert_and_get_node(store: Store) -> None:
    n = _make_node(name="commit-style", body="No co-author trailers.")
    store.upsert_node(n)
    got = store.get_node(n.id)
    assert got is not None
    assert got.name == "commit-style"
    assert got.body == "No co-author trailers."
    assert got.type == "memory_feedback"


def test_upsert_overwrites_existing(store: Store) -> None:
    n = _make_node(body="a")
    store.upsert_node(n)
    n.body = "b"
    n.updated_at = int(time.time()) + 1
    store.upsert_node(n)
    got = store.get_node(n.id)
    assert got is not None
    assert got.body == "b"


def test_get_node_returns_none_when_missing(store: Store) -> None:
    assert store.get_node("nonexistent") is None


def test_get_node_by_source(store: Store) -> None:
    n = _make_node(source_path="/some/path.md")
    store.upsert_node(n)
    got = store.get_node_by_source("/some/path.md")
    assert got is not None
    assert got.id == n.id


def test_list_nodes_filter_by_type(store: Store) -> None:
    a = _make_node(type="memory_feedback", source_path="/a.md")
    b = _make_node(type="memory_project", source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    feedback = store.list_nodes(type="memory_feedback")
    assert len(feedback) == 1
    assert feedback[0].id == a.id


def test_list_nodes_filter_by_project(store: Store) -> None:
    a = _make_node(project_key="P1", source_path="/a.md")
    b = _make_node(project_key="P2", source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    p1 = store.list_nodes(project_key="P1")
    assert len(p1) == 1
    assert p1[0].id == a.id


def test_list_nodes_orders_by_updated_desc(store: Store) -> None:
    older = _make_node(source_path="/o.md")
    older.updated_at = 1000
    newer = _make_node(source_path="/n.md")
    newer.updated_at = 2000
    store.upsert_node(older)
    store.upsert_node(newer)
    rows = store.list_nodes()
    assert rows[0].id == newer.id


def test_count_nodes(store: Store) -> None:
    for i, t in enumerate(["memory_feedback", "memory_feedback", "memory_project"]):
        store.upsert_node(_make_node(type=t, source_path=f"/n{i}.md"))
    counts = store.count_nodes()
    assert counts["memory_feedback"] == 2
    assert counts["memory_project"] == 1


def test_delete_node(store: Store) -> None:
    n = _make_node()
    store.upsert_node(n)
    store.delete_node(n.id)
    assert store.get_node(n.id) is None


def test_node_new_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="unknown node type"):
        Node.new(
            type="bogus",
            name="x",
            body="y",
            source_path="/x.md",
            source_kind="memory_dir",
        )


def test_node_new_rejects_unknown_source_kind() -> None:
    with pytest.raises(ValueError, match="unknown source kind"):
        Node.new(
            type="memory_feedback",
            name="x",
            body="y",
            source_path="/x.md",
            source_kind="bogus",
        )


# --- Edges -----------------------------------------------------------------


def test_add_and_get_edge(store: Store) -> None:
    a = _make_node(source_path="/a.md")
    b = _make_node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    store.add_edge(a.id, b.id, "applies_to", weight=0.9, source="user")
    edges = store.get_edges(src_id=a.id)
    assert len(edges) == 1
    e: Edge = edges[0]
    assert e.dst_id == b.id
    assert e.relation == "applies_to"
    assert e.weight == 0.9


def test_add_edge_idempotent_overwrites_weight(store: Store) -> None:
    a = _make_node(source_path="/a.md")
    b = _make_node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    store.add_edge(a.id, b.id, "applies_to", weight=0.5)
    store.add_edge(a.id, b.id, "applies_to", weight=0.9)
    edges = store.get_edges(src_id=a.id)
    assert len(edges) == 1
    assert edges[0].weight == 0.9


def test_delete_node_cascades_edges(store: Store) -> None:
    a = _make_node(source_path="/a.md")
    b = _make_node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    store.add_edge(a.id, b.id, "applies_to")
    store.delete_node(a.id)
    assert store.get_edges(src_id=a.id) == []
    assert store.get_edges(dst_id=b.id) == []


def test_remove_edge(store: Store) -> None:
    a = _make_node(source_path="/a.md")
    b = _make_node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    store.add_edge(a.id, b.id, "applies_to")
    store.remove_edge(a.id, b.id, "applies_to")
    assert store.get_edges(src_id=a.id) == []


def test_add_edge_rejects_unknown_relation(store: Store) -> None:
    a = _make_node(source_path="/a.md")
    b = _make_node(source_path="/b.md")
    store.upsert_node(a)
    store.upsert_node(b)
    with pytest.raises(ValueError, match="unknown edge relation"):
        store.add_edge(a.id, b.id, "bogus")


# --- Sources ---------------------------------------------------------------


def test_register_and_list_source(store: Store) -> None:
    store.register_source("/p", "memory_dir", project_key="P")
    sources = store.list_sources()
    assert len(sources) == 1
    s = sources[0]
    assert s.path == "/p"
    assert s.kind == "memory_dir"
    assert s.project_key == "P"
    assert s.enabled is True
    assert s.last_indexed_at is None


def test_register_source_idempotent(store: Store) -> None:
    store.register_source("/p", "memory_dir")
    store.register_source("/p", "claude_md")  # update kind
    sources = store.list_sources()
    assert len(sources) == 1
    assert sources[0].kind == "claude_md"


def test_mark_source_indexed(store: Store) -> None:
    store.register_source("/p", "memory_dir")
    store.mark_source_indexed("/p", when=1234)
    assert store.list_sources()[0].last_indexed_at == 1234


def test_list_sources_only_enabled(store: Store) -> None:
    store.register_source("/p1", "memory_dir", enabled=True)
    store.register_source("/p2", "memory_dir", enabled=False)
    enabled = store.list_sources(only_enabled=True)
    assert len(enabled) == 1
    assert enabled[0].path == "/p1"


def test_remove_source(store: Store) -> None:
    store.register_source("/p", "memory_dir")
    assert store.remove_source("/p") == 0  # no nodes -> 0 cascaded
    assert store.list_sources() == []


def test_remove_source_cascades_descendant_nodes(store: Store) -> None:
    """v1.1.1 regression: removing a memory_dir source must delete every
    node whose source_path lives under that directory. Before the fix the
    sources row was deleted but the nodes lingered forever (the reindex
    orphan-sweep only catches nodes under STILL-REGISTERED sources)."""
    store.register_source("/repo/memory", "memory_dir")
    # Two nodes under the source path (direct + nested) plus one unrelated
    # node that should survive the cascade.
    inside_a = _make_node(name="a", source_path="/repo/memory/a.md", source_kind="memory_dir")
    inside_b = _make_node(name="b", source_path="/repo/memory/sub/b.md", source_kind="memory_dir")
    outside = _make_node(name="c", source_path="/elsewhere/c.md", source_kind="memory_dir")
    store.upsert_node(inside_a)
    store.upsert_node(inside_b)
    store.upsert_node(outside)

    removed = store.remove_source("/repo/memory")

    assert removed == 2
    assert store.get_node(inside_a.id) is None
    assert store.get_node(inside_b.id) is None
    # Unrelated node survives.
    assert store.get_node(outside.id) is not None
    # Source itself is gone.
    assert store.list_sources() == []


def test_remove_source_cascade_respects_claude_md_exact_match(store: Store) -> None:
    """claude_md is a single-file source. Its cascade should hit only the
    exact node whose source_path equals the source path, not unrelated
    files in the same directory."""
    store.register_source("/home/u/.claude/CLAUDE.md", "claude_md")
    exact = _make_node(
        name="claude-md",
        source_path="/home/u/.claude/CLAUDE.md",
        source_kind="claude_md",
    )
    sibling = _make_node(
        name="sibling",
        source_path="/home/u/.claude/other.md",
        source_kind="memory_dir",
    )
    store.upsert_node(exact)
    store.upsert_node(sibling)

    removed = store.remove_source("/home/u/.claude/CLAUDE.md")

    assert removed == 1
    assert store.get_node(exact.id) is None
    assert store.get_node(sibling.id) is not None


def test_remove_source_unregistered_returns_zero(store: Store) -> None:
    """Removing a source that was never registered is a no-op that returns
    0 (so HTTP DELETE stays idempotent)."""
    assert store.remove_source("/never/registered") == 0


def test_find_orphan_nodes_returns_unregistered_sources(store: Store) -> None:
    """v1.1.1 cleanup helper for the pre-1.1.1 leak path: a node whose
    source_path doesn't match any registered source should be detected."""
    # One registered source + a node under it (should NOT be orphan).
    store.register_source("/repo/memory", "memory_dir")
    in_source = _make_node(name="a", source_path="/repo/memory/a.md", source_kind="memory_dir")
    store.upsert_node(in_source)

    # Two nodes that look like leftovers from a previously-removed source.
    orphan_a = _make_node(
        name="orphan-a",
        source_path="/old/Duyen/README.md",
        source_kind="memory_dir",
    )
    orphan_b = _make_node(
        name="orphan-b",
        source_path="/old/Duyen/sub/sub2/inner.md",
        source_kind="memory_dir",
    )
    store.upsert_node(orphan_a)
    store.upsert_node(orphan_b)

    orphans = store.find_orphan_nodes()
    ids = {n.id for n in orphans}
    assert orphan_a.id in ids
    assert orphan_b.id in ids
    assert in_source.id not in ids
    assert len(orphans) == 2


def test_find_orphan_nodes_empty_when_all_match(store: Store) -> None:
    store.register_source("/repo/memory", "memory_dir")
    n = _make_node(source_path="/repo/memory/a.md", source_kind="memory_dir")
    store.upsert_node(n)
    assert store.find_orphan_nodes() == []


def test_find_orphan_nodes_no_sources_means_everything_orphan(store: Store) -> None:
    """If the user has removed every source (pre-1.1.1 cascade), every
    node is an orphan."""
    n1 = _make_node(source_path="/a/x.md", source_kind="memory_dir")
    n2 = _make_node(source_path="/b/y.md", source_kind="memory_dir")
    store.upsert_node(n1)
    store.upsert_node(n2)
    orphans = store.find_orphan_nodes()
    assert {o.id for o in orphans} == {n1.id, n2.id}


def test_register_source_rejects_unknown_kind(store: Store) -> None:
    with pytest.raises(ValueError, match="unknown source kind"):
        store.register_source("/p", "bogus")


# --- Query audit log -------------------------------------------------------


def test_log_query_and_read_back(store: Store) -> None:
    qid = store.log_query(
        prompt="why?",
        intent_tags=["debug", "feedback-recall"],
        retrieved_ids=["n1", "n2"],
        scores={"n1": 0.9, "n2": 0.8},
    )
    assert qid
    recent = store.recent_queries()
    assert len(recent) == 1
    q = recent[0]
    assert q.prompt == "why?"
    assert q.intent_tags == ["debug", "feedback-recall"]
    assert q.retrieved_ids == ["n1", "n2"]
    assert q.scores == {"n1": 0.9, "n2": 0.8}


def test_recent_queries_orders_newest_first(store: Store) -> None:
    store.log_query(prompt="first", intent_tags=[], retrieved_ids=[], scores={})
    time.sleep(0.01)
    store.log_query(prompt="second", intent_tags=[], retrieved_ids=[], scores={})
    recent = store.recent_queries()
    assert recent[0].prompt == "second"
    assert recent[1].prompt == "first"


# --- Context manager -------------------------------------------------------


def test_store_works_as_context_manager(tmp_path: Path) -> None:
    with Store(tmp_path / "mnemo.db") as s:
        assert s.schema_version() == SCHEMA_VERSION
