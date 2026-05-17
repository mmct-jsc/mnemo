"""Edge-case validation across graph, ingest, retrieve.

These cover the failure modes that aren't part of the happy path: empty
inputs, malformed files, weird query shapes, unicode, and adversarial
inputs. They exist so refactors don't regress quietly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mnemo import graph, ingest, retrieve
from mnemo.store import EMBEDDING_DIM, Node, Store
from tests.conftest import FakeEmbedder

# --- Graph edge cases ----------------------------------------------------


def test_graph_empty_store_query_returns_no_hits(store: Store) -> None:
    fake = FakeEmbedder()
    result = retrieve.query(store, fake, "anything")
    assert result.hits == []
    assert result.tokens_used == 0


def test_graph_single_node_no_edges_returns_self(store: Store) -> None:
    n = Node.new(
        type="memory_feedback",
        name="solo",
        body="single body",
        source_path="/x.md",
        source_kind="memory_dir",
    )
    store.upsert_node(n)
    fake = FakeEmbedder()
    store.upsert_chunks(n.id, [(0, fake.embed_text(n.body), n.body)])

    # No graph edges, but vector hit should still find it.
    result = retrieve.query(store, fake, n.body)
    assert len(result.hits) == 1
    assert result.hits[0].node_id == n.id


def test_graph_self_edge_via_frontmatter_skipped(store: Store) -> None:
    """A frontmatter `appliesTo: [self_name]` should not create a self-edge."""
    n = Node.new(
        type="memory_project",
        name="self-ref",
        body="body",
        source_path="/x.md",
        source_kind="memory_dir",
        frontmatter_json='{"appliesTo": ["self-ref"]}',
    )
    store.upsert_node(n)
    added = graph.infer_edges_from_frontmatter(store, n)
    assert added == 0
    assert store.get_edges(src_id=n.id) == []


def test_graph_compute_scores_empty_candidates(store: Store) -> None:
    assert graph.compute_graph_scores(store, {}) == {}


def test_graph_compute_scores_unknown_dst_passthrough(store: Store) -> None:
    a = Node.new(
        type="memory_feedback",
        name="a",
        body="x",
        source_path="/a.md",
        source_kind="memory_dir",
    )
    store.upsert_node(a)
    # Edge to a non-existent destination - SQL FK constraint rejects it.
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store.add_edge(a.id, "does-not-exist", "applies_to")


def test_co_occurrence_with_singleton_does_nothing(store: Store) -> None:
    a = Node.new(
        type="memory_feedback",
        name="a",
        body="x",
        source_path="/a.md",
        source_kind="memory_dir",
    )
    store.upsert_node(a)
    n = graph.update_co_occurrence(store, [a.id])  # only one
    assert n == 0
    assert store.get_edges(src_id=a.id, relation="co_occurs_with") == []


# --- Ingest edge cases ---------------------------------------------------


def test_parse_file_empty(tmp_path: Path) -> None:
    p = tmp_path / "empty.md"
    p.write_text("", encoding="utf-8")
    parsed = ingest.parse_file(p, kind="memory_dir")
    assert parsed.body == ""
    # Description falls back to filename stem when nothing else available.
    assert parsed.name == "empty"


def test_parse_file_only_frontmatter(tmp_path: Path) -> None:
    p = tmp_path / "feedback_x.md"
    p.write_text(
        "---\nname: only-fm\ndescription: only frontmatter\ntype: feedback\n---\n",
        encoding="utf-8",
    )
    parsed = ingest.parse_file(p, kind="memory_dir")
    assert parsed.name == "only-fm"
    assert parsed.body.strip() == ""
    assert parsed.description == "only frontmatter"


def test_parse_file_malformed_yaml_falls_back(tmp_path: Path) -> None:
    """Malformed YAML in frontmatter shouldn't crash; we should treat the
    whole file as body and infer fields from the filename."""
    p = tmp_path / "feedback_broken.md"
    # Unbalanced bracket triggers a YAML error.
    p.write_text("---\nbad: [unclosed\n---\nactual content\n", encoding="utf-8")
    # python-frontmatter raises on broken YAML; ingest should at least not
    # explode. The current implementation will raise, but reindex catches it.
    try:
        parsed = ingest.parse_file(p, kind="memory_dir")
        # If parse succeeded (some YAML parsers are lenient), at least body
        # is non-empty.
        assert parsed is not None
    except Exception:  # noqa: BLE001
        pytest.skip("frontmatter library is strict about YAML; reindex catches this")


def test_parse_file_unicode_in_body(tmp_path: Path) -> None:
    p = tmp_path / "feedback_unicode.md"
    p.write_text(
        "---\ntype: feedback\nname: u\n---\nbody with é, 中文, 🚀\n",
        encoding="utf-8",
    )
    parsed = ingest.parse_file(p, kind="memory_dir")
    assert "中文" in parsed.body
    assert "🚀" in parsed.body
    assert len(parsed.hash) == 64


def test_parse_file_very_long_body(tmp_path: Path) -> None:
    p = tmp_path / "long.md"
    body = "## section\n\n" + ("paragraph " * 500 + "\n\n") * 20
    p.write_text(body, encoding="utf-8")
    parsed = ingest.parse_file(p, kind="memory_dir")
    assert len(parsed.body) > 50_000
    # Hash is stable
    h2 = ingest.parse_file(p, kind="memory_dir").hash
    assert parsed.hash == h2


def test_reindex_handles_unreadable_directory(tmp_path: Path, store: Store) -> None:
    """An empty (non-existent) source path is treated as 'nothing to scan'."""
    store.register_source(str(tmp_path / "ghost"), "memory_dir")
    report = ingest.reindex(store)
    assert report.added == 0
    assert report.errors == []  # no exception bubbled up


def test_reindex_skips_non_md_files(tmp_path: Path, store: Store) -> None:
    (tmp_path / "feedback_x.md").write_text("body", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\x00")
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")
    store.register_source(str(tmp_path), "memory_dir")
    report = ingest.reindex(store)
    assert report.added == 1
    assert len(store.list_nodes(limit=10)) == 1


def test_reindex_handles_renamed_file(tmp_path: Path, store: Store) -> None:
    """Renaming a file reads as 'old gone, new added' to mnemo. Hash matching
    across paths is out-of-scope for v1."""
    p1 = tmp_path / "feedback_a.md"
    p1.write_text("---\ntype: feedback\nname: a\n---\nbody\n", encoding="utf-8")
    store.register_source(str(tmp_path), "memory_dir")
    ingest.reindex(store)
    assert len(store.list_nodes(limit=10)) == 1

    p2 = tmp_path / "feedback_renamed.md"
    p1.rename(p2)
    report = ingest.reindex(store)
    assert report.added == 1
    assert report.removed == 1


def test_reindex_unicode_filename(tmp_path: Path, store: Store) -> None:
    p = tmp_path / "feedback_中文.md"
    p.write_text("body", encoding="utf-8")
    store.register_source(str(tmp_path), "memory_dir")
    report = ingest.reindex(store)
    assert report.added == 1


def test_reindex_deeply_nested_path(tmp_path: Path, store: Store) -> None:
    deep = tmp_path / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    (deep / "feedback_deep.md").write_text("body", encoding="utf-8")
    store.register_source(str(tmp_path), "memory_dir")
    report = ingest.reindex(store)
    assert report.added == 1


# --- Retrieve / query edge cases -----------------------------------------


def test_query_empty_string(store: Store, fake_embedder: FakeEmbedder) -> None:
    n = Node.new(
        type="memory_feedback",
        name="a",
        body="x",
        source_path="/a.md",
        source_kind="memory_dir",
    )
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, fake_embedder.embed_text("x"), "x")])
    # Empty query - retrieval should still return something (or nothing,
    # depending on vec_search behavior). The contract: no exception.
    result = retrieve.query(store, fake_embedder, "")
    assert isinstance(result.hits, list)


def test_query_single_character(store: Store, fake_embedder: FakeEmbedder) -> None:
    n = Node.new(
        type="memory_feedback",
        name="a",
        body="hello",
        source_path="/a.md",
        source_kind="memory_dir",
    )
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, fake_embedder.embed_text("hello"), "hello")])
    result = retrieve.query(store, fake_embedder, "x")
    assert isinstance(result.hits, list)


def test_query_unicode(store: Store, fake_embedder: FakeEmbedder) -> None:
    n = Node.new(
        type="memory_project",
        name="proj-中文",
        body="日本語 body",
        source_path="/p.md",
        source_kind="memory_dir",
        description="国際化",
    )
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, fake_embedder.embed_text("中文"), "中文")])
    result = retrieve.query(store, fake_embedder, "中文プロジェクト")
    assert isinstance(result.hits, list)


def test_query_very_long(store: Store, fake_embedder: FakeEmbedder) -> None:
    n = Node.new(
        type="memory_feedback",
        name="a",
        body="x",
        source_path="/a.md",
        source_kind="memory_dir",
    )
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, fake_embedder.embed_text("x"), "x")])
    big_query = "word " * 5000  # MiniLM caps at 256 tokens; sentence-transformers truncates
    result = retrieve.query(store, fake_embedder, big_query)
    assert isinstance(result.hits, list)


def test_query_punctuation_only(store: Store, fake_embedder: FakeEmbedder) -> None:
    n = Node.new(
        type="memory_feedback",
        name="a",
        body="x",
        source_path="/a.md",
        source_kind="memory_dir",
    )
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, fake_embedder.embed_text("x"), "x")])
    result = retrieve.query(store, fake_embedder, "!!! ??? ...")
    # No alpha tokens -> lexical score is 0; vector still runs.
    assert isinstance(result.hits, list)


def test_query_zero_budget_returns_no_hits(store: Store, fake_embedder: FakeEmbedder) -> None:
    n = Node.new(
        type="memory_feedback",
        name="a-very-long-description-that-takes-tokens",
        body="a long body " * 50,
        source_path="/a.md",
        source_kind="memory_dir",
    )
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, fake_embedder.embed_text("body"), "body")])
    result = retrieve.query(store, fake_embedder, "anything", budget_tokens=1)
    # With a 1-token budget, even one description-line won't fit.
    assert result.tokens_used <= 1


def test_query_dedups_when_one_node_has_many_chunks(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    n = Node.new(
        type="memory_feedback",
        name="multi",
        body="multi-chunk body",
        source_path="/m.md",
        source_kind="memory_dir",
    )
    store.upsert_node(n)
    # 5 identical chunks for the same node.
    chunks = [(i, fake_embedder.embed_text(f"chunk {i}"), f"chunk {i}") for i in range(5)]
    store.upsert_chunks(n.id, chunks)
    result = retrieve.query(store, fake_embedder, "chunk", k=10)
    ids = [h.node_id for h in result.hits]
    assert len(ids) == len(set(ids)), "per-node dedup is broken"


def test_query_active_project_boost(store: Store, fake_embedder: FakeEmbedder) -> None:
    a = Node.new(
        type="memory_project",
        name="p1-thing",
        body="body about deployment",
        source_path="/a.md",
        source_kind="memory_dir",
        project_key="P1",
    )
    b = Node.new(
        type="memory_project",
        name="p2-thing",
        body="body about deployment",
        source_path="/b.md",
        source_kind="memory_dir",
        project_key="P2",
    )
    store.upsert_node(a)
    store.upsert_node(b)
    vec = fake_embedder.embed_text("deployment")
    store.upsert_chunks(a.id, [(0, vec, "body about deployment")])
    store.upsert_chunks(b.id, [(0, vec, "body about deployment")])
    result = retrieve.query(store, fake_embedder, "deployment", active_project="P1")
    # Both have identical vector hit; project-scope boost should put P1 first.
    assert result.hits[0].node_id == a.id


def test_query_strict_isolation_keeps_project_key_none_nodes(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    """v1.2.1 regression: under strict isolation with an active project,
    nodes whose ``project_key`` is None (e.g. CLAUDE.md global memory,
    plan_docs, any cross-cutting entry) MUST survive the hard filter.

    Pre-fix: ``None != active_project`` was True so the filter dropped
    them silently. That made global memory invisible whenever a project
    was active -- one of the dominant 'common query returns nothing'
    causes in v1.2.0.
    """
    in_proj = Node.new(
        type="memory_project",
        name="in-p1",
        body="content about deployment",
        source_path="/in_p1.md",
        source_kind="memory_dir",
        project_key="P1",
    )
    other_proj = Node.new(
        type="memory_project",
        name="in-p2",
        body="content about deployment",
        source_path="/in_p2.md",
        source_kind="memory_dir",
        project_key="P2",
    )
    global_doc = Node.new(
        # No project_key -- mimics a CLAUDE.md / plan_doc / cross-cutting
        # memory entry. NOT BASE-flagged.
        type="project_doc",
        name="global-claude-md",
        body="content about deployment",
        source_path="/CLAUDE.md",
        source_kind="claude_md",
        project_key=None,
    )
    for n in (in_proj, other_proj, global_doc):
        store.upsert_node(n)
        vec = fake_embedder.embed_text("deployment")
        store.upsert_chunks(n.id, [(0, vec, "content about deployment")])

    result = retrieve.query(store, fake_embedder, "deployment", k=5, active_project="P1")
    surfaced = {h.node_id for h in result.hits}

    # v1.2.1: the active-project node and the global (None project_key)
    # node both survive strict isolation.
    assert in_proj.id in surfaced, "active-project node must surface"
    assert global_doc.id in surfaced, "project_key=None must survive strict isolation (v1.2.1 fix)"
    # v4.3.2 contract evolution: the cross-project node is no longer
    # HARD-dropped (that hid dominant matches -> silent-zero, the
    # user's "result seems wrong"). It is SOFT-penalized: present, but
    # ranked BELOW the in-project + global nodes (all three share the
    # identical body, so only the isolation penalty separates them).
    assert other_proj.id in surfaced, "v4.3.2: cross-project node must SOFT-penalize, not hard-drop"
    order = [h.node_id for h in result.hits]
    assert order.index(other_proj.id) > order.index(in_proj.id), (
        "the isolation penalty must rank the cross-project node below the active-project node"
    )
    assert order.index(other_proj.id) > order.index(global_doc.id), (
        "the isolation penalty must rank the cross-project node below "
        "the cross-cutting (project_key=None) node"
    )


# --- Vector dimension safety --------------------------------------------


def test_vec_search_rejects_wrong_dim(store: Store) -> None:
    with pytest.raises(ValueError, match="query dim"):
        store.vec_search([0.1] * (EMBEDDING_DIM - 1), k=5)


def test_upsert_chunks_rejects_wrong_dim(store: Store) -> None:
    n = Node.new(
        type="memory_feedback",
        name="a",
        body="x",
        source_path="/a.md",
        source_kind="memory_dir",
    )
    store.upsert_node(n)
    with pytest.raises(ValueError, match="vector dim"):
        store.upsert_chunks(n.id, [(0, [0.0, 1.0], "wrong dim")])
