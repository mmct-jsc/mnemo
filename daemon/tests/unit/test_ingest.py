"""Tests for ingestion: parsing, scanning, reindexing."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from mnemo import ingest
from mnemo.store import Source, Store

# --- parse_file -----------------------------------------------------------


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def test_parse_file_with_full_frontmatter(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "feedback_no_emojis.md",
        """\
        ---
        name: feedback-no-emojis
        description: No emojis in code or commit messages
        type: feedback
        ---
        Hard rule: never include emoji glyphs in committed code.
        """,
    )
    parsed = ingest.parse_file(p, kind="memory_dir", project_key="P1")
    assert parsed.type == "memory_feedback"
    assert parsed.name == "feedback-no-emojis"
    assert parsed.description == "No emojis in code or commit messages"
    assert "Hard rule" in parsed.body
    assert parsed.project_key == "P1"
    assert parsed.hash  # 64-char hex
    assert len(parsed.hash) == 64
    assert parsed.frontmatter_json is not None
    fm = json.loads(parsed.frontmatter_json)
    assert fm["type"] == "feedback"


def test_parse_file_without_frontmatter(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "user_profile.md",
        """\
        # User Profile
        Engineer working on multiple projects.
        """,
    )
    parsed = ingest.parse_file(p, kind="memory_dir")
    # No frontmatter -> infer from filename prefix
    assert parsed.type == "memory_user"
    # No frontmatter name -> filename stem
    assert parsed.name == "user_profile"
    # No description -> first heading
    assert parsed.description == "User Profile"
    assert parsed.frontmatter_json is None


def test_parse_file_infers_type_for_each_prefix(tmp_path: Path) -> None:
    cases = {
        "user_x.md": "memory_user",
        "feedback_x.md": "memory_feedback",
        "project_x.md": "memory_project",
        "reference_x.md": "memory_reference",
    }
    for filename, expected_type in cases.items():
        p = _write(tmp_path / filename, "body")
        parsed = ingest.parse_file(p, kind="memory_dir")
        assert parsed.type == expected_type, f"{filename} should be {expected_type}"


def test_parse_file_unknown_prefix_falls_back(tmp_path: Path) -> None:
    p = _write(tmp_path / "stuff.md", "body")
    parsed = ingest.parse_file(p, kind="memory_dir")
    assert parsed.type == "memory_project"


def test_parse_file_claude_md_default_type(tmp_path: Path) -> None:
    p = _write(tmp_path / "CLAUDE.md", "# Repo notes\n\nbody")
    parsed = ingest.parse_file(p, kind="claude_md")
    assert parsed.type == "project_doc"


def test_parse_file_plan_dir_default_type(tmp_path: Path) -> None:
    p = _write(tmp_path / "design.md", "# Design")
    parsed = ingest.parse_file(p, kind="plan_dir")
    assert parsed.type == "plan_doc"


def test_parse_file_description_falls_back_to_snippet(tmp_path: Path) -> None:
    p = _write(tmp_path / "note.md", "no heading, just text on one line")
    parsed = ingest.parse_file(p, kind="memory_dir")
    assert parsed.description == "no heading, just text on one line"


def test_parse_file_infers_project_key_from_path(tmp_path: Path) -> None:
    nested = tmp_path / "projects" / "D--Repository-aibox-prod-all" / "memory"
    p = _write(nested / "project_x.md", "body")
    parsed = ingest.parse_file(p, kind="memory_dir")
    assert parsed.project_key == "D--Repository-aibox-prod-all"


def test_parse_file_explicit_project_key_wins(tmp_path: Path) -> None:
    nested = tmp_path / "projects" / "D--P1" / "memory"
    p = _write(nested / "project_x.md", "body")
    parsed = ingest.parse_file(p, kind="memory_dir", project_key="OVERRIDE")
    assert parsed.project_key == "OVERRIDE"


def test_parse_file_hash_stable(tmp_path: Path) -> None:
    p = _write(tmp_path / "x.md", "same content")
    h1 = ingest.parse_file(p, kind="memory_dir").hash
    h2 = ingest.parse_file(p, kind="memory_dir").hash
    assert h1 == h2


def test_parse_file_hash_changes_with_content(tmp_path: Path) -> None:
    p = tmp_path / "x.md"
    p.write_text("v1", encoding="utf-8")
    h1 = ingest.parse_file(p, kind="memory_dir").hash
    p.write_text("v2", encoding="utf-8")
    h2 = ingest.parse_file(p, kind="memory_dir").hash
    assert h1 != h2


def test_parse_file_rejects_unknown_kind(tmp_path: Path) -> None:
    p = _write(tmp_path / "x.md", "body")
    with pytest.raises(ValueError, match="unknown source kind"):
        ingest.parse_file(p, kind="bogus")


# --- discover_default_sources --------------------------------------------


def test_discover_finds_global_claude_md(tmp_path: Path) -> None:
    _write(tmp_path / "CLAUDE.md", "global memory")
    sources = ingest.discover_default_sources(tmp_path)
    assert len(sources) == 1
    s = sources[0]
    assert s.kind == "claude_md"
    assert s.path == tmp_path / "CLAUDE.md"
    assert s.project_key is None


def test_discover_finds_project_memory_dirs(tmp_path: Path) -> None:
    (tmp_path / "projects" / "P1" / "memory").mkdir(parents=True)
    (tmp_path / "projects" / "P2" / "memory").mkdir(parents=True)
    sources = ingest.discover_default_sources(tmp_path)
    keys = {s.project_key for s in sources}
    assert keys == {"P1", "P2"}
    for s in sources:
        assert s.kind == "memory_dir"


def test_discover_skips_project_dir_without_memory(tmp_path: Path) -> None:
    (tmp_path / "projects" / "P1").mkdir(parents=True)  # no 'memory' subdir
    sources = ingest.discover_default_sources(tmp_path)
    assert sources == []


def test_discover_is_deterministic(tmp_path: Path) -> None:
    for k in ["P3", "P1", "P2"]:
        (tmp_path / "projects" / k / "memory").mkdir(parents=True)
    sources = ingest.discover_default_sources(tmp_path)
    assert [s.project_key for s in sources] == ["P1", "P2", "P3"]


# --- scan_source ----------------------------------------------------------


def test_scan_source_walks_directory(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    _write(tmp_path / "feedback_a.md", "a")
    _write(tmp_path / "a" / "project_b.md", "b")
    src = Source(
        path=str(tmp_path), kind="memory_dir", project_key=None, last_indexed_at=None, enabled=True
    )
    parsed = list(ingest.scan_source(src))
    names = {p.name for p in parsed}
    assert names == {"feedback_a", "project_b"}


def test_scan_source_skips_memory_md(tmp_path: Path) -> None:
    _write(tmp_path / "MEMORY.md", "index file")
    _write(tmp_path / "feedback_x.md", "real entry")
    src = Source(
        path=str(tmp_path), kind="memory_dir", project_key=None, last_indexed_at=None, enabled=True
    )
    parsed = list(ingest.scan_source(src))
    names = {p.name for p in parsed}
    assert names == {"feedback_x"}


def test_scan_source_single_file(tmp_path: Path) -> None:
    p = _write(tmp_path / "CLAUDE.md", "# Global\nbody")
    src = Source(
        path=str(p), kind="claude_md", project_key=None, last_indexed_at=None, enabled=True
    )
    parsed = list(ingest.scan_source(src))
    assert len(parsed) == 1
    assert parsed[0].name == "CLAUDE"


def test_scan_source_missing_path_yields_nothing(tmp_path: Path) -> None:
    src = Source(
        path=str(tmp_path / "nonexistent"),
        kind="memory_dir",
        project_key=None,
        last_indexed_at=None,
        enabled=True,
    )
    assert list(ingest.scan_source(src)) == []


# --- reindex --------------------------------------------------------------


def _enable_source(
    store: Store, path: Path, kind: str = "memory_dir", project_key: str | None = None
) -> Source:
    store.register_source(str(path), kind, project_key=project_key)
    return [s for s in store.list_sources() if s.path == str(path)][0]


def test_reindex_adds_new_files(tmp_path: Path, store: Store) -> None:
    _write(tmp_path / "feedback_x.md", "first")
    _write(tmp_path / "project_y.md", "second")
    _enable_source(store, tmp_path)
    report = ingest.reindex(store)
    assert report.added == 2
    assert report.updated == 0
    assert report.unchanged == 0
    assert report.removed == 0


def test_reindex_is_idempotent(tmp_path: Path, store: Store) -> None:
    _write(tmp_path / "feedback_x.md", "body")
    _enable_source(store, tmp_path)
    ingest.reindex(store)
    second = ingest.reindex(store)
    assert second.added == 0
    assert second.updated == 0
    assert second.unchanged == 1


def test_reindex_updates_changed_files(tmp_path: Path, store: Store) -> None:
    p = _write(tmp_path / "feedback_x.md", "v1")
    _enable_source(store, tmp_path)
    ingest.reindex(store)
    p.write_text("v2", encoding="utf-8")
    report = ingest.reindex(store)
    assert report.updated == 1
    assert report.added == 0


def test_reindex_removes_vanished_files(tmp_path: Path, store: Store) -> None:
    p = _write(tmp_path / "feedback_x.md", "body")
    _enable_source(store, tmp_path)
    ingest.reindex(store)
    p.unlink()
    report = ingest.reindex(store)
    assert report.removed == 1


def test_reindex_marks_source_indexed(tmp_path: Path, store: Store) -> None:
    _write(tmp_path / "feedback_x.md", "body")
    _enable_source(store, tmp_path)
    assert store.list_sources()[0].last_indexed_at is None
    ingest.reindex(store)
    assert store.list_sources()[0].last_indexed_at is not None


def test_reindex_only_touches_provided_sources(tmp_path: Path, store: Store) -> None:
    a = tmp_path / "A"
    b = tmp_path / "B"
    _write(a / "feedback_x.md", "a")
    _write(b / "feedback_y.md", "b")
    src_a = _enable_source(store, a)
    _enable_source(store, b)
    report = ingest.reindex(store, sources=[src_a])
    assert report.added == 1  # only A was scanned
    assert store.list_nodes(limit=10)[0].source_path.endswith("feedback_x.md")


def test_reindex_handles_source_missing_on_disk(tmp_path: Path, store: Store) -> None:
    missing = tmp_path / "gone"
    _enable_source(store, missing)
    report = ingest.reindex(store)
    assert report.added == 0
    assert report.errors == []


def test_reindex_does_not_delete_nodes_outside_scanned_sources(
    tmp_path: Path, store: Store
) -> None:
    a = tmp_path / "A"
    b = tmp_path / "B"
    _write(a / "feedback_a.md", "a")
    _write(b / "feedback_b.md", "b")
    src_a = _enable_source(store, a)
    src_b = _enable_source(store, b)
    ingest.reindex(store, sources=[src_a, src_b])
    # Now reindex only A - node from B must NOT be deleted.
    report = ingest.reindex(store, sources=[src_a])
    assert report.removed == 0
    assert len(store.list_nodes(limit=10)) == 2


# --- register_default_sources --------------------------------------------


def test_register_default_sources(tmp_path: Path, store: Store) -> None:
    _write(tmp_path / "CLAUDE.md", "global")
    (tmp_path / "projects" / "P1" / "memory").mkdir(parents=True)
    n = ingest.register_default_sources(store, tmp_path)
    assert n == 2
    sources = store.list_sources()
    kinds = {s.kind for s in sources}
    assert kinds == {"claude_md", "memory_dir"}


def test_register_default_sources_idempotent(tmp_path: Path, store: Store) -> None:
    _write(tmp_path / "CLAUDE.md", "global")
    ingest.register_default_sources(store, tmp_path)
    n = ingest.register_default_sources(store, tmp_path)
    assert n == 0  # nothing new
    assert len(store.list_sources()) == 1
