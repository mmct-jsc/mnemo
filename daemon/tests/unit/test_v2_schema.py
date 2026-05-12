"""v2.0 phase 1 schema migration tests.

Phase 1 widens three enums and adds one column:
- ``SOURCE_KINDS`` gains ``code_repo`` and ``docs_dir``.
- ``NODE_TYPES`` gains ``commit`` (for git-log decision provenance, phase 9).
- ``EDGE_RELATIONS`` gains ``references_function``, ``motivated_by`` and
  ``closed_by`` (the provenance edge family).
- ``edges`` gains a ``confidence FLOAT NOT NULL DEFAULT 1.0`` column so
  inferred edges (Tier 2 calls, Tier 3 framework matches, auto-linked
  provenance edges) can carry an uncertainty score into retrieval.

Phase 1 is schema-only; no ingester emits the new node / edge kinds yet.
Each later phase wires up the producer that targets one of these slots.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.store import (
    EDGE_RELATIONS,
    NODE_TYPES,
    SOURCE_KINDS,
    Edge,
    Node,
    Store,
)

# --- Source kinds ----------------------------------------------------------


def test_source_kinds_includes_code_repo() -> None:
    assert "code_repo" in SOURCE_KINDS


def test_source_kinds_includes_docs_dir() -> None:
    assert "docs_dir" in SOURCE_KINDS


def test_register_source_accepts_code_repo(store: Store) -> None:
    store.register_source("/repo/frontend", "code_repo", project_key="frontend")
    sources = store.list_sources()
    assert len(sources) == 1
    assert sources[0].kind == "code_repo"


def test_register_source_accepts_docs_dir(store: Store) -> None:
    store.register_source("/repo/docs", "docs_dir")
    sources = store.list_sources()
    assert len(sources) == 1
    assert sources[0].kind == "docs_dir"


def test_v1_source_kinds_still_accepted(store: Store) -> None:
    # Regression guard: adding new kinds must not break the v1.x set.
    for kind in ("memory_dir", "claude_md", "plan_dir", "transcripts"):
        assert kind in SOURCE_KINDS


# --- Node types ------------------------------------------------------------


def test_node_types_includes_commit() -> None:
    assert "commit" in NODE_TYPES


def test_node_new_accepts_commit_type() -> None:
    n = Node.new(
        type="commit",
        name="a1b2c3d fix: short-circuit login",
        body="full commit message body",
        source_path="/repo/frontend@a1b2c3d",
        source_kind="code_repo",
        description="Alice 2026-04-12 fix: short-circuit login on stale token",
    )
    assert n.type == "commit"
    assert n.source_kind == "code_repo"


def test_v1_node_types_still_accepted() -> None:
    # Regression guard for the existing typed-memory set.
    for t in (
        "memory_user",
        "memory_feedback",
        "memory_project",
        "memory_reference",
        "project_doc",
        "plan_doc",
        "session_summary",
    ):
        assert t in NODE_TYPES


# --- Edge relations: provenance family ------------------------------------


def _make_provenance_endpoints(store: Store) -> tuple[Node, Node]:
    """Two persisted nodes shaped like a commit and a feedback node.

    Provenance edges flow commit -> code_function (references_function),
    commit -> memory_feedback (motivated_by), memory_feedback -> commit
    (closed_by). These tests only assert the relation strings parse;
    later phases assert the semantic shape.
    """
    commit = Node.new(
        type="commit",
        name="a1b2c3d shortcircuit",
        body="commit body",
        source_path="/repo@a1b2c3d",
        source_kind="code_repo",
    )
    fb = Node.new(
        type="memory_feedback",
        name="feedback_login_flake",
        body="retro body",
        source_path="/mem/feedback.md",
        source_kind="memory_dir",
    )
    store.upsert_node(commit)
    store.upsert_node(fb)
    return commit, fb


def test_edge_relations_includes_provenance_family() -> None:
    for rel in ("references_function", "motivated_by", "closed_by"):
        assert rel in EDGE_RELATIONS


def test_add_edge_accepts_references_function(store: Store) -> None:
    commit, fb = _make_provenance_endpoints(store)
    # references_function targets a code_function in real use; for the
    # schema test, the validator only cares the relation string is known.
    store.add_edge(commit.id, fb.id, "references_function")
    edges = store.get_edges(src_id=commit.id)
    assert len(edges) == 1
    assert edges[0].relation == "references_function"


def test_add_edge_accepts_motivated_by(store: Store) -> None:
    commit, fb = _make_provenance_endpoints(store)
    store.add_edge(commit.id, fb.id, "motivated_by")
    edges = store.get_edges(src_id=commit.id)
    assert edges[0].relation == "motivated_by"


def test_add_edge_accepts_closed_by(store: Store) -> None:
    commit, fb = _make_provenance_endpoints(store)
    store.add_edge(fb.id, commit.id, "closed_by")
    edges = store.get_edges(src_id=fb.id)
    assert edges[0].relation == "closed_by"


def test_add_edge_still_rejects_bogus_relation(store: Store) -> None:
    commit, fb = _make_provenance_endpoints(store)
    with pytest.raises(ValueError, match="unknown edge relation"):
        store.add_edge(commit.id, fb.id, "totally_made_up")


# --- Edge confidence column -----------------------------------------------


def test_edges_table_has_confidence_column(tmp_path: Path) -> None:
    s = Store(tmp_path / "mnemo.db")
    try:
        cols = {r["name"] for r in s.conn.execute("PRAGMA table_info(edges)").fetchall()}
        assert "confidence" in cols
    finally:
        s.close()


def test_edge_dataclass_has_confidence_field() -> None:
    # Pure dataclass shape check -- no DB. Defaults to 1.0 so existing
    # call sites (Tier-less v1.x edges) keep their old semantics.
    e = Edge(
        src_id="a",
        dst_id="b",
        relation="applies_to",
        weight=1.0,
        source="inferred",
        created_at=0,
    )
    assert hasattr(e, "confidence")
    assert e.confidence == 1.0


def test_add_edge_default_confidence_is_one(store: Store) -> None:
    commit, fb = _make_provenance_endpoints(store)
    store.add_edge(commit.id, fb.id, "motivated_by")
    edges = store.get_edges(src_id=commit.id)
    assert edges[0].confidence == 1.0


def test_add_edge_persists_explicit_confidence(store: Store) -> None:
    commit, fb = _make_provenance_endpoints(store)
    store.add_edge(commit.id, fb.id, "motivated_by", confidence=0.6)
    edges = store.get_edges(src_id=commit.id)
    assert edges[0].confidence == 0.6


def test_add_edge_overwrite_updates_confidence(store: Store) -> None:
    # Same (src, dst, relation) triple: second add must update confidence
    # in place (the v1.x weight-overwrite contract extended to the new col).
    commit, fb = _make_provenance_endpoints(store)
    store.add_edge(commit.id, fb.id, "motivated_by", confidence=0.6)
    store.add_edge(commit.id, fb.id, "motivated_by", confidence=0.9)
    edges = store.get_edges(src_id=commit.id)
    assert len(edges) == 1
    assert edges[0].confidence == 0.9


# --- Scan safety for v2.0 source kinds ------------------------------------
#
# Phase 1 only widens the SOURCE_KINDS enum so registration succeeds; it
# does NOT wire a parser for code_repo / docs_dir (Tier 1 ingestion lands
# in phase 4, docs harvest in phase 2). The latent risk: scan_source would
# happily walk a code repo with the v1.x markdown parser if include
# patterns are empty. Guard rail: a source with no include patterns and
# no kind-specific default yields nothing instead of every file.


def test_scan_source_code_repo_walks_python_files_after_phase_4(  # type: ignore[no-untyped-def]
    tmp_path,
) -> None:
    """v2.0 phase 4 wired the tree-sitter ingester for code_repo, so a
    source pointing at a directory with ``.py`` files now produces
    ``code_module`` (+ declaration) nodes.

    Phase 1 originally asserted the opposite -- the safety rail
    yielding nothing -- because no parser was wired. This test
    replaces that assertion now that phase 4 ships a real ingester.
    The detailed shape (one module + one function + edges) lives in
    ``test_ingest_code_repo.py``; here we only assert non-emptiness.
    """
    from mnemo import ingest
    from mnemo.store import Source

    (tmp_path / "main.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    src = Source(
        path=str(tmp_path),
        kind="code_repo",
        project_key=None,
        last_indexed_at=None,
        enabled=True,
    )
    parsed = list(ingest.scan_source(src))
    types = {p.type for p in parsed}
    assert "code_module" in types
    assert "code_function" in types


def test_scan_source_docs_dir_with_no_include_yields_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Same guarantee for docs_dir until phase 2 wires its include defaults."""
    from mnemo import ingest
    from mnemo.store import Source

    (tmp_path / "guide.md").write_text("# Guide\nbody\n", encoding="utf-8")
    src = Source(
        path=str(tmp_path),
        kind="docs_dir",
        project_key=None,
        last_indexed_at=None,
        enabled=True,
    )
    parsed = list(ingest.scan_source(src))
    assert parsed == []


# --- v2.0 phase 4: Tier 1 code node types + edge relations ----------------


def test_node_types_includes_tier1_code_types() -> None:
    for t in ("code_module", "code_function", "code_class", "code_method"):
        assert t in NODE_TYPES, t


def test_edge_relations_includes_tier1_code_relations() -> None:
    # `defines`: module -> top-level function / class
    # `method_of`: method -> containing class
    # `imports`: module -> imported module (best-effort cross-file)
    for rel in ("defines", "method_of", "imports"):
        assert rel in EDGE_RELATIONS, rel


def test_node_new_accepts_code_module(store: Store) -> None:
    n = Node.new(
        type="code_module",
        name="auth.py",
        body="def login(): pass\n",
        source_path="/repo/auth.py",
        source_kind="code_repo",
        description="Module: auth",
    )
    store.upsert_node(n)
    got = store.get_node(n.id)
    assert got is not None
    assert got.type == "code_module"


def test_node_new_accepts_code_function(store: Store) -> None:
    n = Node.new(
        type="code_function",
        name="login",
        body="def login():\n    return True\n",
        source_path="/repo/auth.py:1-2",
        source_kind="code_repo",
        description="Authenticate a user.",
    )
    store.upsert_node(n)
    got = store.get_node(n.id)
    assert got is not None
    assert got.type == "code_function"


def test_node_new_accepts_code_class_and_method(store: Store) -> None:
    cls = Node.new(
        type="code_class",
        name="Session",
        body="class Session:\n    pass\n",
        source_path="/repo/auth.py:5-10",
        source_kind="code_repo",
    )
    method = Node.new(
        type="code_method",
        name="renew",
        body="def renew(self):\n    pass\n",
        source_path="/repo/auth.py:7-8",
        source_kind="code_repo",
    )
    store.upsert_node(cls)
    store.upsert_node(method)
    assert store.get_node(cls.id) is not None
    assert store.get_node(method.id) is not None


def test_add_edge_accepts_defines_relation(store: Store) -> None:
    mod = Node.new(
        type="code_module",
        name="auth.py",
        body="",
        source_path="/repo/auth.py",
        source_kind="code_repo",
    )
    fn = Node.new(
        type="code_function",
        name="login",
        body="",
        source_path="/repo/auth.py:1-1",
        source_kind="code_repo",
    )
    store.upsert_node(mod)
    store.upsert_node(fn)
    store.add_edge(mod.id, fn.id, "defines")
    edges = store.get_edges(src_id=mod.id)
    assert edges[0].relation == "defines"


def test_add_edge_accepts_method_of_relation(store: Store) -> None:
    cls = Node.new(
        type="code_class",
        name="Session",
        body="",
        source_path="/repo/auth.py:1-5",
        source_kind="code_repo",
    )
    method = Node.new(
        type="code_method",
        name="renew",
        body="",
        source_path="/repo/auth.py:2-3",
        source_kind="code_repo",
    )
    store.upsert_node(cls)
    store.upsert_node(method)
    store.add_edge(method.id, cls.id, "method_of")
    edges = store.get_edges(src_id=method.id)
    assert edges[0].relation == "method_of"


def test_add_edge_accepts_imports_relation(store: Store) -> None:
    a = Node.new(
        type="code_module",
        name="auth.py",
        body="",
        source_path="/repo/auth.py",
        source_kind="code_repo",
    )
    b = Node.new(
        type="code_module",
        name="db.py",
        body="",
        source_path="/repo/db.py",
        source_kind="code_repo",
    )
    store.upsert_node(a)
    store.upsert_node(b)
    store.add_edge(a.id, b.id, "imports", confidence=0.8)
    edges = store.get_edges(src_id=a.id)
    assert edges[0].relation == "imports"
    assert edges[0].confidence == 0.8
