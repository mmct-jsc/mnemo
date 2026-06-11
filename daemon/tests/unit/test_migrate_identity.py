"""v5.28.0 step 5: the code-identity migration report (dry-run gate).

``run_code_identity_migration`` runs the lazy migration (via a reindex)
against a store and reports what happened to code-node identity:
how many legacy nodes were re-keyed IN PLACE (id preserved) vs how many
would be orphaned (genuinely-removed code). The dry-run runs this against
a COPY of the live DB so the numbers can be reviewed before the live
touch.
"""

from __future__ import annotations

from pathlib import Path

from mnemo import ingest, migrate_identity
from mnemo.store import Source, Store


def _src(path: Path) -> Source:
    return Source(
        path=str(path), kind="code_repo", project_key=None, last_indexed_at=None, enabled=True
    )


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _force_legacy_keys(store: Store) -> int:
    """Rewrite every code declaration node back to a legacy line-range
    key (mimics a pre-v5.28 on-disk store). Returns how many were
    rewritten."""
    n = 0
    for node in store.list_nodes(limit=10**6):
        if node.type in ("code_function", "code_class", "code_method") and "::" in node.source_path:
            file_part, rng = migrate_identity.code.code_file_and_range(
                node.source_path, node.frontmatter_json
            )
            start, end = rng if rng else (1, 1)
            store.conn.execute(
                "UPDATE nodes SET source_path = ? WHERE id = ?",
                (f"{file_part}:{start}-{end}", node.id),
            )
            n += 1
    store.conn.commit()
    return n


def test_migration_report_rekeys_legacy_nodes_in_place(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "m.py", "def a():\n    return 1\n\ndef b():\n    return 2\n")
    store = Store(tmp_path / "s.db")
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        legacy = _force_legacy_keys(store)
        assert legacy == 2

        report = migrate_identity.run_code_identity_migration(store, embedder=None)

        assert report["legacy_before"] == 2
        assert report["rekeyed_in_place"] == 2, "both legacy fns re-keyed with id preserved"
        assert report["still_legacy_after"] == 0
        assert report["would_orphan"] == 0, "nothing genuinely removed -> no orphans"
    finally:
        store.close()


def test_migration_report_flags_genuinely_removed_code_as_orphan(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    f = _write(repo / "m.py", "def a():\n    return 1\n\ndef b():\n    return 2\n")
    store = Store(tmp_path / "s.db")
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        _force_legacy_keys(store)
        # b is deleted from source before the migrating reindex.
        f.write_text("def a():\n    return 1\n", encoding="utf-8")

        report = migrate_identity.run_code_identity_migration(store, embedder=None)

        assert report["rekeyed_in_place"] == 1, "a is re-keyed in place"
        assert report["would_orphan"] == 1, "b (removed from source) is the only orphan"
    finally:
        store.close()


def test_dry_run_from_db_does_not_touch_the_live_store(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "m.py", "def a():\n    return 1\n")
    db = tmp_path / "live.db"
    store = Store(db)
    store.register_source(str(repo), "code_repo")
    ingest.reindex(store, embedder=None)
    _force_legacy_keys(store)
    fn_before = next(n for n in store.list_nodes(limit=10**6) if n.type == "code_function")
    assert "::" not in fn_before.source_path
    store.close()

    report = migrate_identity.dry_run_from_db(db)
    assert report["dry_run"] is True
    assert report["rekeyed_in_place"] >= 1

    # The LIVE db is untouched: the function is still on its legacy key.
    store2 = Store(db)
    try:
        fn_after = next(n for n in store2.list_nodes(limit=10**6) if n.type == "code_function")
        assert "::" not in fn_after.source_path, "dry-run must NOT mutate the live DB"
    finally:
        store2.close()
