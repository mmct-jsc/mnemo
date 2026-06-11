"""v5.27.0 step 3: NULL-key derivation + backfill at reindex.

NULL-keyed directory sources make every owned node cross-cutting (the
v1.2.1 contract), leaking foreign docs into every scoped query (the live
aibox AGENTS.md case). Reindex now derives the key from the source root,
persists it on the source row, and backfills owned NULL-key nodes.
``claude_md`` sources stay None -- global memory is cross-cutting BY DESIGN.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mnemo import ingest, paths
from mnemo.store import Node, Store


@pytest.fixture(autouse=True)
def _sandbox(isolated_mnemo_home: Path) -> Path:
    return isolated_mnemo_home


def _seed_memory_file(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "feedback_x.md").write_text(
        "---\nname: rule-x\ndescription: a rule about retries\ntype: feedback\n---\n"
        "Always retry three times.\n",
        encoding="utf-8",
    )


def test_reindex_derives_source_key_and_backfills_nodes(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    root = tmp_path / "repo" / "memory"
    _seed_memory_file(root)
    store.register_source(str(root), "memory_dir")  # registered WITHOUT a key
    expected = paths.resolve_project_key(str(root))

    # Seed a legacy NULL-keyed row the way the world actually produced them:
    # an EARLIER reindex (before this version derived keys) left real,
    # parser-consistent nodes with project_key=None. Simulate by reindexing
    # with the derivation disabled, then NULLing the key + clearing the
    # source key -- exactly the pre-v5.27 on-disk state.
    ingest.reindex(store, embedder=None)
    store.conn.execute("UPDATE nodes SET project_key = NULL")
    store.conn.execute("UPDATE sources SET project_key = NULL")
    store.conn.commit()
    assert store.count_nodes_total() >= 1
    assert all(n.project_key is None for n in store.list_nodes())

    # The real reindex now derives + persists the source key AND backfills
    # the legacy NULL-key nodes.
    ingest.reindex(store, embedder=None)

    src = store.list_sources()[0]
    assert src.project_key == expected, "the source row must persist the derived key"
    under_root = [n for n in store.list_nodes() if str(root) in n.source_path]
    assert under_root, "the x.md node must still be present"
    assert all(n.project_key == expected for n in under_root), (
        "every NULL-key node owned by the source must backfill to the derived key"
    )
    store.close()


def test_claude_md_sources_stay_global(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    md = tmp_path / "CLAUDE.md"
    md.write_text("# global memory\n\n- rule", encoding="utf-8")
    store.register_source(str(md), "claude_md")
    ingest.reindex(store, embedder=None)
    src = store.list_sources()[0]
    assert src.project_key is None, "global memory is cross-cutting BY DESIGN"
    store.close()


def test_backfill_only_touches_owned_null_nodes(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    foreign = Node.new(
        type="project_doc",
        name="foreign",
        body="elsewhere",
        source_path=str(tmp_path / "other" / "f.md"),
        source_kind="docs_dir",
    )
    keyed = Node.new(
        type="project_doc",
        name="keyed",
        body="already keyed",
        source_path=str(tmp_path / "repo" / "docs" / "k.md"),
        source_kind="docs_dir",
    )
    keyed.project_key = "KEEP"
    store.upsert_node(foreign)
    store.upsert_node(keyed)
    n = store.backfill_project_keys(str(tmp_path / "repo" / "docs"), "NEW", "docs_dir")
    assert n == 0, "no NULL-keyed node under the root -> nothing updated"
    assert store.get_node(foreign.id).project_key is None
    assert store.get_node(keyed.id).project_key == "KEEP"
    store.close()
