"""v5.28.0 step 2: store-level rekey primitive + legacy lookup.

``rekey_node`` performs an in-place, id-preserving move of a code node
from its legacy ``<file>:<start>-<end>`` key to the stable
``<file>::<qualified>`` key, demoting the line range to frontmatter.
Because every association (edges, vec rows, feedback, audit) is keyed by
node id, the move preserves all of them with ZERO re-embed.
``find_legacy_code_node`` is the reconcile fallback's candidate lookup.
"""

from __future__ import annotations

import json
from pathlib import Path

from mnemo.store import Node, Store


def _code_node(
    store: Store,
    *,
    id: str,
    name: str,
    source_path: str,
    fm: str | None = None,
    type: str = "code_function",
) -> None:
    now = 1_700_000_000
    store.upsert_node(
        Node(
            id=id,
            type=type,
            name=name,
            description=None,
            body=f"def {name}(): ...",
            source_path=source_path,
            source_kind="code_repo",
            project_key="P",
            frontmatter_json=fm,
            hash="h-" + id,
            created_at=now,
            updated_at=now,
        )
    )


def test_rekey_node_preserves_id_and_edges(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _code_node(store, id="mod", name="x.py", source_path="/r/x.py", type="code_module")
    _code_node(store, id="fn", name="login", source_path="/r/x.py:10-20")
    store.add_edge("mod", "fn", "defines", confidence=1.0)

    store.rekey_node("fn", "/r/x.py::login", line_start=10, line_end=20)

    n = store.get_node("fn")
    assert n is not None, "the SAME id must survive the move"
    assert n.source_path == "/r/x.py::login"
    cu = json.loads(n.frontmatter_json)["code_unit"]
    assert (cu["line_start"], cu["line_end"]) == (10, 20)
    # The edge is keyed by node id, so the move preserves it.
    edges = list(store.get_edges(src_id="mod", relation="defines"))
    assert any(e.dst_id == "fn" for e in edges)
    store.close()


def test_rekey_node_preserves_updated_at_and_hash(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _code_node(store, id="fn", name="f", source_path="/r/x.py:1-2")
    before = store.get_node("fn")

    store.rekey_node("fn", "/r/x.py::f", line_start=1, line_end=2)

    after = store.get_node("fn")
    assert after.updated_at == before.updated_at, "a pure move must not bump recency"
    assert after.hash == before.hash, "content unchanged -> hash unchanged -> no re-embed"
    store.close()


def test_rekey_node_merges_into_existing_code_unit(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    fm = json.dumps({"code_unit": {"imports": ["os"], "line_start": 0, "line_end": 0}})
    _code_node(store, id="fn", name="f", source_path="/r/x.py:5-9", fm=fm)

    store.rekey_node("fn", "/r/x.py::f", line_start=5, line_end=9)

    cu = json.loads(store.get_node("fn").frontmatter_json)["code_unit"]
    assert cu["line_start"] == 5
    assert cu["line_end"] == 9
    assert cu["imports"] == ["os"], "other code_unit fields must be preserved"
    store.close()


def test_rekey_node_handles_null_frontmatter(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _code_node(store, id="fn", name="f", source_path="/r/x.py:1-2", fm=None)

    store.rekey_node("fn", "/r/x.py::f", line_start=1, line_end=2)

    cu = json.loads(store.get_node("fn").frontmatter_json)["code_unit"]
    assert (cu["line_start"], cu["line_end"]) == (1, 2)
    store.close()


def test_find_legacy_code_node_matches_by_file_type_name(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _code_node(store, id="fn", name="login", source_path="/r/auth.py:1-5")
    hits = store.find_legacy_code_node("/r/auth.py", "code_function", "login")
    assert [h.id for h in hits] == ["fn"]
    store.close()


def test_find_legacy_code_node_excludes_already_migrated(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _code_node(
        store,
        id="fn",
        name="login",
        source_path="/r/auth.py::login",
        fm=json.dumps({"code_unit": {"line_start": 1, "line_end": 5}}),
    )
    hits = store.find_legacy_code_node("/r/auth.py", "code_function", "login")
    assert hits == [], "a node already on the stable key is not a migration candidate"
    store.close()


def test_find_legacy_code_node_scopes_to_file_and_returns_overloads(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _code_node(store, id="a", name="login", source_path="/r/auth.py:1-5")
    _code_node(store, id="b", name="login", source_path="/r/other.py:1-5")
    # Two same-name decls in ONE file (overloads) -> both are candidates;
    # the reconcile picks by nearest line.
    _code_node(store, id="c", name="login", source_path="/r/auth.py:40-44")
    hits = store.find_legacy_code_node("/r/auth.py", "code_function", "login")
    assert {h.id for h in hits} == {"a", "c"}
    store.close()
