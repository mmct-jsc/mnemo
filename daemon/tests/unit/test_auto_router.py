"""v2.0 phase 2: auto-router heuristic tests.

The router classifies an unknown path into one of the SOURCE_KINDS values
based on filesystem heuristics, then a dry-run preview tells the user
what would be indexed before any DB write happens.

Heuristics, in decision order:
1. ``.git/`` directory present AND >= 1 recognized source file -> code_repo (high)
2. >= 1 markdown file with frontmatter ``type:`` field -> memory_dir (high)
3. >= 2 markdown files without frontmatter AND 0 source files -> docs_dir (medium)
4. None of the above -> (None, "low")

50k file safety ceiling: refuses to register a source whose recognized
source-file count (after default skip-dirs) exceeds ``SAFETY_CEILING``.
``--force`` / ``force=True`` overrides.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mnemo import auto_router
from mnemo.auto_router import (
    DEFAULT_SKIP_DIRS,
    SAFETY_CEILING,
    PreviewBreakdown,
    PreviewResult,
    preview,
    propose_kind,
    scan_path,
)


def _write(path: Path, body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# --- propose_kind ----------------------------------------------------------


def _breakdown(**overrides: object) -> PreviewBreakdown:
    base: dict[str, object] = {
        "by_ext": {},
        "total_files": 0,
        "md_with_frontmatter": 0,
        "md_without_frontmatter": 0,
        "has_git": False,
    }
    base.update(overrides)
    return PreviewBreakdown(**base)  # type: ignore[arg-type]


def test_propose_kind_code_repo_when_git_plus_source_files() -> None:
    b = _breakdown(by_ext={".py": 5}, total_files=5, has_git=True)
    kind, conf = propose_kind(b)
    assert kind == "code_repo"
    assert conf == "high"


def test_propose_kind_code_repo_requires_source_files_not_just_git() -> None:
    # .git dir present but only README -- not a code_repo signal yet.
    # Falls through to docs_dir if the readmes meet that bar, else low.
    b = _breakdown(
        by_ext={".md": 3},
        total_files=3,
        md_without_frontmatter=3,
        has_git=True,
    )
    kind, _ = propose_kind(b)
    # 3 plain md + 0 src files -> docs_dir wins. The point: not code_repo.
    assert kind != "code_repo"


def test_propose_kind_memory_dir_on_typed_frontmatter() -> None:
    b = _breakdown(
        by_ext={".md": 2},
        total_files=2,
        md_with_frontmatter=1,
        md_without_frontmatter=1,
    )
    kind, conf = propose_kind(b)
    assert kind == "memory_dir"
    assert conf == "high"


def test_propose_kind_docs_dir_on_plain_markdown() -> None:
    b = _breakdown(
        by_ext={".md": 3},
        total_files=3,
        md_without_frontmatter=3,
    )
    kind, conf = propose_kind(b)
    assert kind == "docs_dir"
    assert conf == "medium"


def test_propose_kind_docs_dir_requires_at_least_two_plain_md() -> None:
    b = _breakdown(by_ext={".md": 1}, total_files=1, md_without_frontmatter=1)
    kind, _ = propose_kind(b)
    assert kind != "docs_dir"


def test_propose_kind_docs_dir_blocked_by_source_files() -> None:
    # If source files coexist with plain markdown, prefer code_repo (which
    # requires .git, so this falls to None) over docs_dir to avoid
    # silently misclassifying a code repo as a docs dump.
    b = _breakdown(
        by_ext={".py": 2, ".md": 3},
        total_files=5,
        md_without_frontmatter=3,
    )
    kind, _ = propose_kind(b)
    assert kind != "docs_dir"


def test_propose_kind_falls_through_to_low_on_empty() -> None:
    kind, conf = propose_kind(_breakdown())
    assert kind is None
    assert conf == "low"


def test_propose_kind_memory_dir_outranks_docs_dir() -> None:
    # A directory with one typed frontmatter file and many plain markdowns
    # should still classify as memory_dir -- the explicit typed entry is
    # the signal that the user is using the memory format.
    b = _breakdown(
        by_ext={".md": 10},
        total_files=10,
        md_with_frontmatter=1,
        md_without_frontmatter=9,
    )
    kind, _ = propose_kind(b)
    assert kind == "memory_dir"


# --- scan_path -------------------------------------------------------------


def test_scan_path_counts_by_extension(tmp_path: Path) -> None:
    _write(tmp_path / "main.py", "x = 1\n")
    _write(tmp_path / "lib.py", "y = 2\n")
    _write(tmp_path / "notes.md", "# notes\n")
    b = scan_path(tmp_path)
    assert b.by_ext[".py"] == 2
    assert b.by_ext[".md"] == 1
    assert b.total_files == 3


def test_scan_path_skips_default_dirs(tmp_path: Path) -> None:
    _write(tmp_path / "main.py", "x = 1\n")
    _write(tmp_path / "node_modules" / "noisy.js", "// vendor\n")
    _write(tmp_path / ".venv" / "lib" / "site-packages" / "foo.py", "pass\n")
    _write(tmp_path / "__pycache__" / "main.cpython-311.pyc", "")
    b = scan_path(tmp_path)
    # Only the top-level main.py counts.
    assert b.by_ext.get(".py", 0) == 1
    assert b.by_ext.get(".js", 0) == 0


def test_scan_path_detects_git_dir(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    _write(tmp_path / "main.py", "x = 1\n")
    b = scan_path(tmp_path)
    assert b.has_git is True


def test_scan_path_distinguishes_md_with_and_without_frontmatter(tmp_path: Path) -> None:
    _write(
        tmp_path / "typed.md",
        "---\nname: typed-doc\ntype: feedback\n---\nbody\n",
    )
    _write(tmp_path / "plain.md", "# Just a markdown\n\nbody\n")
    _write(tmp_path / "untyped.md", "---\nname: no-type\n---\nbody\n")
    b = scan_path(tmp_path)
    # `typed.md` counts as with-frontmatter; `plain.md` AND `untyped.md`
    # both count as without (because untyped.md has no `type:` field --
    # the heuristic is `type:`, not `---`).
    assert b.md_with_frontmatter == 1
    assert b.md_without_frontmatter == 2


def test_scan_path_returns_empty_for_missing_path(tmp_path: Path) -> None:
    b = scan_path(tmp_path / "nonexistent")
    assert b.total_files == 0
    assert b.has_git is False


def test_scan_path_single_file(tmp_path: Path) -> None:
    p = _write(tmp_path / "CLAUDE.md", "# Project\nbody\n")
    b = scan_path(p)
    assert b.total_files == 1
    assert b.by_ext[".md"] == 1


# --- preview (full entry point) -------------------------------------------


def test_preview_classifies_a_typical_code_repo(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    _write(tmp_path / "main.py", "x = 1\n")
    _write(tmp_path / "lib.py", "y = 2\n")
    _write(tmp_path / "README.md", "# repo\n")
    r = preview(tmp_path)
    assert r.proposed_kind == "code_repo"
    assert r.confidence == "high"
    assert r.breakdown.has_git is True
    assert r.breakdown.by_ext[".py"] == 2


def test_preview_classifies_a_typical_memory_dir(tmp_path: Path) -> None:
    _write(
        tmp_path / "feedback_x.md",
        "---\nname: feedback_x\ntype: feedback\n---\nbody\n",
    )
    _write(
        tmp_path / "project_y.md",
        "---\nname: project_y\ntype: project\n---\nbody\n",
    )
    r = preview(tmp_path)
    assert r.proposed_kind == "memory_dir"
    assert r.confidence == "high"


def test_preview_classifies_a_typical_docs_dir(tmp_path: Path) -> None:
    _write(tmp_path / "guide.md", "# Guide\n")
    _write(tmp_path / "intro.md", "# Intro\n")
    _write(tmp_path / "ref.md", "# Ref\n")
    r = preview(tmp_path)
    assert r.proposed_kind == "docs_dir"
    assert r.confidence == "medium"


def test_preview_returns_low_on_an_empty_dir(tmp_path: Path) -> None:
    r = preview(tmp_path)
    assert r.proposed_kind is None
    assert r.confidence == "low"


def test_preview_raises_on_nonexistent_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        preview(tmp_path / "does-not-exist")


def test_preview_flags_safety_ceiling_when_exceeded(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # The real 50k ceiling would need 50k files to trip; for unit-test
    # speed we shrink it to 3, write 5 .py files, and assert the flag.
    monkeypatch.setattr(auto_router, "SAFETY_CEILING", 3)
    (tmp_path / ".git").mkdir()
    for i in range(5):
        _write(tmp_path / f"f{i}.py", "x = 1\n")
    r = preview(tmp_path)
    assert r.exceeds_safety_ceiling is True


def test_preview_force_overrides_safety_ceiling(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(auto_router, "SAFETY_CEILING", 3)
    (tmp_path / ".git").mkdir()
    for i in range(5):
        _write(tmp_path / f"f{i}.py", "x = 1\n")
    r = preview(tmp_path, force=True)
    assert r.exceeds_safety_ceiling is False


def test_preview_below_ceiling_is_not_flagged(tmp_path: Path) -> None:
    # Sanity: a tiny repo never trips the real 50k ceiling.
    (tmp_path / ".git").mkdir()
    _write(tmp_path / "main.py", "x = 1\n")
    r = preview(tmp_path)
    assert r.exceeds_safety_ceiling is False


# --- Constants -------------------------------------------------------------


def test_safety_ceiling_is_50k() -> None:
    assert SAFETY_CEILING == 50_000


def test_default_skip_dirs_includes_common_offenders() -> None:
    # Anchor these so future churn must update the test deliberately.
    for d in (".git", "node_modules", "__pycache__", ".venv", "target", "dist", "build"):
        assert d in DEFAULT_SKIP_DIRS


# --- Result shape ---------------------------------------------------------


def test_preview_result_is_dataclass_with_expected_fields(tmp_path: Path) -> None:
    r = preview(tmp_path)
    assert isinstance(r, PreviewResult)
    assert isinstance(r.breakdown, PreviewBreakdown)
    # Spot-check the field set so downstream callers (UI, server) can
    # rely on the shape.
    assert hasattr(r, "path")
    assert hasattr(r, "proposed_kind")
    assert hasattr(r, "confidence")
    assert hasattr(r, "exceeds_safety_ceiling")
