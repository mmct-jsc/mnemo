"""v5 phase 1: ``local_only`` node flag.

The flag marks nodes ineligible for inclusion in pasteable prompts
(the v5 prompt-architect output may be pasted into a foreign LLM, so
confidential-but-not-secret content must be filterable BEFORE the
analysis pass ever sees it).

Three input paths flag a node as local_only:

1. Frontmatter ``local_only: true`` (or 'yes' / '1' / 'on').
2. Source path contains a ``_private`` segment (the standing rule's
   ``docs/_private/`` convention).
3. Body starts with the literal marker ``[LOCAL ONLY]``.

Retrieval keeps the default backward-compatible: existing callers
see flagged nodes. The prompt-architect path opts in via
``exclude_local_only=True``.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from mnemo import ingest
from mnemo.retrieve import query
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder

# --- Schema ---------------------------------------------------------------


def test_nodes_table_has_local_only_column(tmp_path: Path) -> None:
    """The additive _ensure_columns migration adds local_only."""
    db = tmp_path / "mnemo.db"
    s = Store(db)
    cols = {r["name"] for r in s.conn.execute("PRAGMA table_info(nodes)").fetchall()}
    assert "local_only" in cols
    s.close()


def test_local_only_column_defaults_to_zero(tmp_path: Path) -> None:
    """Existing rows that predate the migration back-fill to 0 (False)."""
    db = tmp_path / "mnemo.db"
    s = Store(db)
    n = Node.new(
        type="memory_feedback",
        name="t",
        body="b",
        source_path="/t.md",
        source_kind="memory_dir",
    )
    s.upsert_node(n)
    row = s.conn.execute("SELECT local_only FROM nodes WHERE id = ?", (n.id,)).fetchone()
    assert row["local_only"] == 0
    s.close()


# --- Node dataclass round-trip --------------------------------------------


def test_node_dataclass_has_local_only_field(tmp_path: Path) -> None:
    n = Node.new(
        type="memory_feedback",
        name="t",
        body="b",
        source_path="/t.md",
        source_kind="memory_dir",
        local_only=True,
    )
    assert n.local_only is True


def test_upsert_and_get_preserves_local_only(tmp_path: Path) -> None:
    db = tmp_path / "mnemo.db"
    s = Store(db)
    n = Node.new(
        type="memory_feedback",
        name="confidential",
        body="strategy note",
        source_path="/conf.md",
        source_kind="memory_dir",
        local_only=True,
    )
    s.upsert_node(n)
    got = s.get_node(n.id)
    assert got is not None
    assert got.local_only is True
    s.close()


# --- Ingest auto-flag -----------------------------------------------------


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def test_ingest_flags_local_only_from_frontmatter(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "sponsor.md",
        """\
        ---
        name: anthropic-sponsor-draft
        type: project
        local_only: true
        ---
        Sponsor body text.
        """,
    )
    parsed = ingest.parse_file(p, kind="memory_dir")
    assert parsed.local_only is True


def test_ingest_flags_local_only_from_private_path(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "_private" / "strategy.md",
        """\
        ---
        name: strategy-doc
        type: project
        ---
        Plan body.
        """,
    )
    parsed = ingest.parse_file(p, kind="memory_dir")
    assert parsed.local_only is True


def test_ingest_flags_local_only_from_body_marker(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "design-partner.md",
        """\
        ---
        name: design-partner-log
        type: project
        ---
        [LOCAL ONLY]

        Design partner contact list.
        """,
    )
    parsed = ingest.parse_file(p, kind="memory_dir")
    assert parsed.local_only is True


def test_ingest_does_not_flag_regular_files(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "public" / "feedback.md",
        """\
        ---
        name: public-feedback
        type: feedback
        ---
        Public lesson.
        """,
    )
    parsed = ingest.parse_file(p, kind="memory_dir")
    assert parsed.local_only is False


def test_local_only_false_in_frontmatter_overrides_path(tmp_path: Path) -> None:
    """Explicit ``local_only: false`` wins over the path heuristic."""
    p = _write(
        tmp_path / "_private" / "explicitly-shareable.md",
        """\
        ---
        name: shareable
        type: project
        local_only: false
        ---
        Body.
        """,
    )
    parsed = ingest.parse_file(p, kind="memory_dir")
    assert parsed.local_only is False


# --- Retrieve filter ------------------------------------------------------


def _seed(store: Store, embedder: FakeEmbedder, *, name: str, local_only: bool) -> Node:
    """Insert a node and its embedding for retrieval tests."""
    n = Node.new(
        type="memory_feedback",
        name=name,
        body=f"This is the body for {name}",
        source_path=f"/{name}.md",
        source_kind="memory_dir",
        local_only=local_only,
    )
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, embedder.embed_text(n.body), n.body)])
    return n


def test_retrieve_default_includes_local_only_nodes(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    """Backward compat: existing callers see flagged nodes unchanged."""
    _seed(store, fake_embedder, name="public-node", local_only=False)
    _seed(store, fake_embedder, name="private-node", local_only=True)
    result = query(store, fake_embedder, "node body", budget_tokens=2000, k=10)
    ids = {h.node_id for h in result.hits}
    # Both nodes are retrievable by default.
    assert len(ids) == 2


def test_retrieve_with_exclude_local_only_filters_flagged(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    """Opt-in filter: the prompt-architect path passes exclude_local_only=True."""
    public_n = _seed(store, fake_embedder, name="public-node", local_only=False)
    private_n = _seed(store, fake_embedder, name="private-node", local_only=True)
    result = query(
        store, fake_embedder, "node body", budget_tokens=2000, k=10, exclude_local_only=True
    )
    ids = {h.node_id for h in result.hits}
    assert public_n.id in ids
    assert private_n.id not in ids


def test_retrieve_exposes_excluded_count(store: Store, fake_embedder: FakeEmbedder) -> None:
    """The pre-emit warning needs to know HOW MANY local_only were dropped."""
    _seed(store, fake_embedder, name="public-1", local_only=False)
    _seed(store, fake_embedder, name="public-2", local_only=False)
    _seed(store, fake_embedder, name="private-1", local_only=True)
    _seed(store, fake_embedder, name="private-2", local_only=True)
    result = query(
        store, fake_embedder, "node body", budget_tokens=2000, k=10, exclude_local_only=True
    )
    # 2 of 4 retrievable nodes were filtered.
    assert result.local_only_excluded == 2


def test_retrieve_default_excluded_count_is_zero(store: Store, fake_embedder: FakeEmbedder) -> None:
    """When the filter is OFF, no nodes are excluded by this gate."""
    _seed(store, fake_embedder, name="public-node", local_only=False)
    _seed(store, fake_embedder, name="private-node", local_only=True)
    result = query(store, fake_embedder, "node body", budget_tokens=2000, k=10)
    assert result.local_only_excluded == 0


# --- Frontmatter JSON round-trip preserves flag --------------------------


def test_parsed_frontmatter_json_includes_local_only(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "sponsor.md",
        """\
        ---
        name: sponsor-draft
        type: project
        local_only: true
        ---
        Body.
        """,
    )
    parsed = ingest.parse_file(p, kind="memory_dir")
    assert parsed.frontmatter_json is not None
    fm = json.loads(parsed.frontmatter_json)
    assert fm["local_only"] is True
