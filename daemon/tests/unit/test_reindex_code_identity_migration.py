"""v5.28.0 step 3: reindex re-keys legacy code nodes in place (lesson #129).

The headline fix: a declaration whose line range shifted (or a whole
pre-v5.28 store) must NOT churn (delete old node + create new id +
re-embed). Instead the reconcile matches the legacy line-range node by
(file, type, name) and re-keys it in place to the stable
``<file>::<qualified_name>`` key -- preserving the node id (hence all
edges / feedback / audit history) and the embedding.
"""

from __future__ import annotations

import json
from pathlib import Path

from mnemo import ingest
from mnemo.store import Source, Store


def _src(path: Path) -> Source:
    return Source(
        path=str(path), kind="code_repo", project_key=None, last_indexed_at=None, enabled=True
    )


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_legacy_keyed_node_is_migrated_in_place_on_reindex(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "auth.py", "def login():\n    return True\n")
    store = Store(tmp_path / "s.db")
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        fn = next(n for n in store.list_nodes() if n.type == "code_function")
        original_id = fn.id
        stable_key = fn.source_path
        assert "::" in stable_key, "the parser must produce the stable key"

        # Simulate a PRE-v5.28 on-disk node: legacy line-range key, with
        # no line range in frontmatter -- exactly the migration input.
        file_part = stable_key.split("::", 1)[0]
        legacy_key = f"{file_part}:1-2"
        store.conn.execute(
            "UPDATE nodes SET source_path = ?, frontmatter_json = ? WHERE id = ?",
            (legacy_key, json.dumps({"code_unit": {"imports": []}}), original_id),
        )
        store.conn.commit()

        # Reindex: the parser emits the stable key, which misses; the
        # fallback re-keys the legacy node in place instead of churning.
        ingest.reindex(store, embedder=None)

        migrated = store.get_node(original_id)
        assert migrated is not None, "legacy node must be re-keyed in place, not deleted"
        assert migrated.source_path == stable_key
        cu = json.loads(migrated.frontmatter_json)["code_unit"]
        assert cu["line_start"] == 1
        assert cu["line_end"] == 2
        # No duplicate function node was created (no churn).
        fns = [n for n in store.list_nodes() if n.type == "code_function"]
        assert len(fns) == 1
        assert fns[0].id == original_id
    finally:
        store.close()


def test_migrated_node_keeps_its_edges(tmp_path: Path) -> None:
    """The whole point of in-place re-key: id-keyed associations survive.
    The module's ``defines`` edge to the function must still resolve."""
    repo = tmp_path / "repo"
    _write(repo / "auth.py", "def login():\n    return True\n")
    store = Store(tmp_path / "s.db")
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        fn = next(n for n in store.list_nodes() if n.type == "code_function")
        original_id = fn.id
        file_part = fn.source_path.split("::", 1)[0]
        store.conn.execute(
            "UPDATE nodes SET source_path = ?, frontmatter_json = ? WHERE id = ?",
            (f"{file_part}:1-2", json.dumps({"code_unit": {}}), original_id),
        )
        store.conn.commit()

        ingest.reindex(store, embedder=None)

        module = next(n for n in store.list_nodes() if n.type == "code_module")
        dst_ids = {e.dst_id for e in store.get_edges(src_id=module.id, relation="defines")}
        assert original_id in dst_ids, "defines edge must point at the SAME (re-keyed) node id"
    finally:
        store.close()


def test_moved_function_keeps_id_and_refreshes_line_range(tmp_path: Path) -> None:
    """A declaration that moves without a body change keeps its id (stable
    key) and refreshes its line-range metadata so full_source / git-log
    overlap stay correct."""
    repo = tmp_path / "repo"
    _write(repo / "m.py", "def a():\n    return 1\n\ndef target():\n    return 2\n")
    store = Store(tmp_path / "s.db")
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        target = next(n for n in store.list_nodes() if n.name == "target")
        original_id = target.id
        assert json.loads(target.frontmatter_json)["code_unit"]["line_start"] == 4

        # Insert blank lines above target; its body bytes are unchanged.
        _write(repo / "m.py", "def a():\n    return 1\n\n\n\n\ndef target():\n    return 2\n")
        ingest.reindex(store, embedder=None)

        target2 = next(n for n in store.list_nodes() if n.name == "target")
        assert target2.id == original_id, "stable key -> same id, no churn on a move"
        cu = json.loads(target2.frontmatter_json)["code_unit"]
        assert cu["line_start"] == 7, "line-range metadata must refresh to the new position"
    finally:
        store.close()
