"""v5.28.0 step 6: reindex mtime-skip for code_repo files.

A code file whose mtime is unchanged since its last index is skipped
(not re-parsed) -- the reindex-speed half of v5.28.0. The file_index
table is new this version, so the first post-upgrade reindex records no
mtime and parses everything (running the identity migration); only
subsequent unchanged reindexes skip. Skipped files keep their nodes out
of the deletion sweep.
"""

from __future__ import annotations

from pathlib import Path

from mnemo import ingest
from mnemo.store import Store


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _fn_node(store: Store):
    return next(n for n in store.list_nodes(limit=10**6) if n.type == "code_function")


def test_unchanged_code_file_is_skipped_on_second_reindex(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "m.py", "def f():\n    return 1\n")
    store = Store(tmp_path / "s.db")
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        fn = _fn_node(store)
        # Tamper with the stored body AND hash. The bad hash forces the
        # "updated" branch IF the file is re-parsed (overwriting the
        # sentinel); if the file is skipped, the sentinel survives.
        store.conn.execute(
            "UPDATE nodes SET body = ?, hash = ? WHERE id = ?",
            ("SENTINEL-BODY", "WRONG-HASH", fn.id),
        )
        store.conn.commit()

        # File on disk is untouched -> same mtime -> skip the re-parse.
        ingest.reindex(store, embedder=None)

        assert store.get_node(fn.id).body == "SENTINEL-BODY", (
            "an unchanged code file must be skipped, not re-parsed"
        )
    finally:
        store.close()


def test_changed_code_file_is_reparsed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    f = _write(repo / "m.py", "def f():\n    return 1\n")
    store = Store(tmp_path / "s.db")
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        fn = _fn_node(store)
        store.conn.execute(
            "UPDATE nodes SET body = ?, hash = ? WHERE id = ?",
            ("SENTINEL-BODY", "WRONG-HASH", fn.id),
        )
        store.conn.commit()

        # Change the file -> new mtime + new content -> must re-parse.
        f.write_text("def f():\n    return 99\n", encoding="utf-8")
        ingest.reindex(store, embedder=None)

        assert store.get_node(fn.id).body != "SENTINEL-BODY", "a changed file must be re-parsed"
    finally:
        store.close()


def test_skip_does_not_orphan_the_files_nodes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "m.py", "def f():\n    return 1\n\ndef g():\n    return 2\n")
    store = Store(tmp_path / "s.db")
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        count = store.count_nodes_total()

        report = ingest.reindex(store, embedder=None)  # unchanged -> skipped

        assert report.removed == 0, "skipped files must not be orphan-swept"
        assert store.count_nodes_total() == count, (
            "node count must be stable across a no-op reindex"
        )
    finally:
        store.close()


def test_mtime_skip_is_code_repo_only(tmp_path: Path) -> None:
    """memory_dir files are cheap and benefit from frontmatter re-parse;
    mtime-skip must not apply to them (the body sentinel is overwritten)."""
    mem = tmp_path / "memory"
    _write(
        mem / "feedback_x.md",
        "---\nname: rule-x\ndescription: d\ntype: feedback\n---\nbody\n",
    )
    store = Store(tmp_path / "s.db")
    try:
        store.register_source(str(mem), "memory_dir")
        ingest.reindex(store, embedder=None)
        node = next(n for n in store.list_nodes(limit=10**6) if n.name == "rule-x")
        store.conn.execute(
            "UPDATE nodes SET body = ?, hash = ? WHERE id = ?",
            ("SENTINEL", "WRONG-HASH", node.id),
        )
        store.conn.commit()

        ingest.reindex(store, embedder=None)

        assert store.get_node(node.id).body != "SENTINEL", "memory files are not mtime-skipped"
    finally:
        store.close()
