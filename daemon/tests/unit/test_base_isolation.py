"""Unit tests for v1.1 phase 5b: BASE flag + project-isolation hard-filter.

Covers:
- Schema migration adds the `base` column to existing DBs (idempotent).
- Node dataclass round-trips ``base`` through upsert + get.
- ingest._resolve_base_flag handles bool / 'true' / 'yes' / etc.
- list_nodes(project_key=X) returns project's nodes plus BASE-flagged.
- count_nodes(project_key=X) includes BASE in counts.
- include_base=False suppresses BASE union (admin / debug usage).
"""

from __future__ import annotations

from pathlib import Path

from mnemo.ingest import _resolve_base_flag
from mnemo.store import Node, Store


def _make_node(name: str, project_key: str | None = None, base: bool = False) -> Node:
    return Node.new(
        type="memory_user",
        name=name,
        body="body of " + name,
        source_path=f"/tmp/{name}.md",
        source_kind="memory_dir",
        description="desc",
        project_key=project_key,
        hash="hash-" + name,
        base=base,
    )


# --- frontmatter parse ----------------------------------------------------


def test_resolve_base_flag_truthy_strings() -> None:
    assert _resolve_base_flag({"base": True}) is True
    assert _resolve_base_flag({"base": "true"}) is True
    assert _resolve_base_flag({"base": "True"}) is True
    assert _resolve_base_flag({"base": "yes"}) is True
    assert _resolve_base_flag({"base": "1"}) is True
    assert _resolve_base_flag({"base": 1}) is True


def test_resolve_base_flag_falsy() -> None:
    assert _resolve_base_flag({}) is False
    assert _resolve_base_flag({"base": False}) is False
    assert _resolve_base_flag({"base": "false"}) is False
    assert _resolve_base_flag({"base": "no"}) is False
    assert _resolve_base_flag({"base": ""}) is False
    assert _resolve_base_flag({"base": None}) is False


# --- schema + round-trip --------------------------------------------------


def test_node_round_trips_base_flag(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    n = _make_node("a", project_key="proj-a", base=True)
    store.upsert_node(n)
    fetched = store.get_node(n.id)
    assert fetched is not None
    assert fetched.base is True


def test_default_node_is_not_base(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    n = _make_node("a")
    store.upsert_node(n)
    assert store.get_node(n.id).base is False  # type: ignore[union-attr]


# --- list_nodes / count_nodes BASE behavior -------------------------------


def _seed_three_projects(store: Store) -> None:
    """proj-a: 2 regular, 1 base. proj-b: 2 regular. no-project: 1 base."""
    store.upsert_node(_make_node("a1", project_key="proj-a"))
    store.upsert_node(_make_node("a2", project_key="proj-a"))
    store.upsert_node(_make_node("a-base", project_key="proj-a", base=True))
    store.upsert_node(_make_node("b1", project_key="proj-b"))
    store.upsert_node(_make_node("b2", project_key="proj-b"))
    store.upsert_node(_make_node("global-base", project_key=None, base=True))


def test_list_nodes_filtered_to_project_includes_base(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _seed_three_projects(store)
    nodes = store.list_nodes(project_key="proj-a", limit=100)
    names = sorted(n.name for n in nodes)
    # proj-a's 3 (including a-base) + the global-base node
    assert names == ["a-base", "a1", "a2", "global-base"]


def test_list_nodes_filtered_strict_excludes_base(tmp_path: Path) -> None:
    """include_base=False is the strict-strict mode for admin views."""
    store = Store(tmp_path / "t.db")
    _seed_three_projects(store)
    nodes = store.list_nodes(project_key="proj-a", limit=100, include_base=False)
    names = sorted(n.name for n in nodes)
    assert names == ["a-base", "a1", "a2"]


def test_list_nodes_no_project_filter_returns_everything(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _seed_three_projects(store)
    nodes = store.list_nodes(limit=100)
    assert len(nodes) == 6


def test_count_nodes_with_project_includes_base(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _seed_three_projects(store)
    counts = store.count_nodes(project_key="proj-a")
    # All 4 visible nodes (a1, a2, a-base, global-base) are memory_user.
    assert counts == {"memory_user": 4}


def test_count_nodes_strict_excludes_base(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _seed_three_projects(store)
    counts = store.count_nodes(project_key="proj-a", include_base=False)
    assert counts == {"memory_user": 3}


# --- migration ------------------------------------------------------------


def test_migration_idempotent(tmp_path: Path) -> None:
    """Re-opening a Store should not error even though the column already
    exists from the first init's migration. We verify _ensure_columns is
    a no-op the second time around."""
    db = tmp_path / "t.db"
    Store(db).close()
    # Reopening must not crash.
    s2 = Store(db)
    n = _make_node("after-migrate", base=True)
    s2.upsert_node(n)
    assert s2.get_node(n.id).base is True  # type: ignore[union-attr]
