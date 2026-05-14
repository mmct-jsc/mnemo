"""Tests for v2.6 phase 4: auto_router.propose_source + .gitignore merge.

``propose_source(path)`` returns dual-source proposals (docs_dir +
code_repo) so the UI can show "I found X docs + Y code; register
one, the other, or both" before the user confirms.

Gitignore patterns are auto-merged into the source's ``exclude``
field at registration time -- this test file covers the propose-side
discovery; the server endpoint test in test_server lives in phase 5.
"""

from __future__ import annotations

from pathlib import Path

from mnemo import auto_router

# --- Dual proposal: docs-only repo ------------------------------------------


def test_propose_source_docs_only_emits_one_proposal(tmp_path: Path) -> None:
    """A docs/ tree with 5 markdown files emits only the docs_dir proposal."""
    docs = tmp_path / "docs"
    docs.mkdir()
    for i in range(5):
        (docs / f"page{i}.md").write_text(f"# page {i}")
    result = auto_router.propose_source(tmp_path)
    kinds = [p.kind for p in result.proposals]
    assert "docs_dir" in kinds
    assert "code_repo" not in kinds


def test_propose_source_below_docs_threshold_emits_no_proposal(tmp_path: Path) -> None:
    """2 markdown files is below the 3-file threshold for docs_dir."""
    (tmp_path / "a.md").write_text("# a")
    (tmp_path / "b.md").write_text("# b")
    result = auto_router.propose_source(tmp_path)
    assert result.proposals == []


# --- Dual proposal: code-only repo ------------------------------------------


def test_propose_source_code_only_emits_code_repo(tmp_path: Path) -> None:
    """A repo with 12 Python files emits only the code_repo proposal."""
    src = tmp_path / "src"
    src.mkdir()
    for i in range(12):
        (src / f"m{i}.py").write_text(f"def f{i}(): ...")
    result = auto_router.propose_source(tmp_path)
    kinds = [p.kind for p in result.proposals]
    assert "code_repo" in kinds
    assert "docs_dir" not in kinds


def test_propose_source_below_code_threshold_emits_no_code_proposal(tmp_path: Path) -> None:
    """9 source files is below the 10-file threshold for code_repo."""
    src = tmp_path / "src"
    src.mkdir()
    for i in range(9):
        (src / f"m{i}.py").write_text(f"def f{i}(): ...")
    result = auto_router.propose_source(tmp_path)
    kinds = [p.kind for p in result.proposals]
    assert "code_repo" not in kinds


# --- Dual proposal: mixed repo (BOTH) ---------------------------------------


def test_propose_source_mixed_emits_both_proposals(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    for i in range(5):
        (docs / f"page{i}.md").write_text(f"# page {i}")
    src = tmp_path / "src"
    src.mkdir()
    for i in range(15):
        (src / f"m{i}.py").write_text(f"def f{i}(): ...")
    result = auto_router.propose_source(tmp_path)
    kinds = {p.kind for p in result.proposals}
    assert kinds == {"docs_dir", "code_repo"}


# --- Proposal shape ---------------------------------------------------------


def test_proposal_has_include_count_est_nodes_sample(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    for i in range(4):
        (docs / f"page{i}.md").write_text(f"# page {i}")
    result = auto_router.propose_source(tmp_path)
    docs_prop = next(p for p in result.proposals if p.kind == "docs_dir")
    assert docs_prop.include_count == 4
    assert docs_prop.est_nodes >= 4
    assert isinstance(docs_prop.sample, list)
    assert len(docs_prop.sample) > 0
    assert isinstance(docs_prop.include_pattern, str)
    assert "*.md" in docs_prop.include_pattern or "docs/" in docs_prop.include_pattern


def test_code_repo_proposal_has_include_pattern_excluding_docs(tmp_path: Path) -> None:
    """code_repo proposal should exclude docs/ in its include or pair with !docs/**."""
    docs = tmp_path / "docs"
    docs.mkdir()
    for i in range(3):
        (docs / f"d{i}.md").write_text("# d")
    src = tmp_path / "src"
    src.mkdir()
    for i in range(12):
        (src / f"m{i}.py").write_text(f"def f{i}(): ...")
    result = auto_router.propose_source(tmp_path)
    code_prop = next(p for p in result.proposals if p.kind == "code_repo")
    assert code_prop.include_count >= 12


# --- .gitignore parsing -----------------------------------------------------


def test_propose_source_finds_root_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("node_modules/\n*.log\ndist/\n")
    src = tmp_path / "src"
    src.mkdir()
    for i in range(12):
        (src / f"m{i}.py").write_text(f"def f{i}(): ...")
    result = auto_router.propose_source(tmp_path)
    assert ".gitignore" in result.gitignore_files_found[0] or any(
        ".gitignore" in p for p in result.gitignore_files_found
    )
    assert "node_modules/" in result.gitignore_excludes or any(
        "node_modules" in pat for pat in result.gitignore_excludes
    )
    assert any("*.log" in pat for pat in result.gitignore_excludes)


def test_propose_source_finds_nested_gitignore(tmp_path: Path) -> None:
    """A nested src/.gitignore's patterns should be prefixed with src/."""
    (tmp_path / ".gitignore").write_text("dist/\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / ".gitignore").write_text("build/\n*.tmp\n")
    for i in range(12):
        (src / f"m{i}.py").write_text(f"def f{i}(): ...")
    result = auto_router.propose_source(tmp_path)
    paths = result.gitignore_files_found
    assert any(".gitignore" in p for p in paths)
    # The nested patterns should be prefixed so `src/build/` is in the merged list.
    excl = result.gitignore_excludes
    assert any("build" in pat and "src" in pat for pat in excl)


def test_propose_source_empty_gitignore_files_when_none(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    for i in range(12):
        (src / f"m{i}.py").write_text(f"def f{i}(): ...")
    result = auto_router.propose_source(tmp_path)
    assert result.gitignore_files_found == []
    assert result.gitignore_excludes == []


# --- Warnings ---------------------------------------------------------------


def test_propose_source_warns_on_large_project(tmp_path: Path) -> None:
    """Force the warning via a tiny threshold so the test is fast."""
    src = tmp_path / "src"
    src.mkdir()
    for i in range(60):
        (src / f"m{i}.py").write_text(f"def f{i}(): ...")
    result = auto_router.propose_source(tmp_path, large_project_threshold=50)
    kinds = [w["kind"] for w in result.warnings]
    assert "large_project" in kinds


def test_propose_source_no_warnings_under_threshold(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    for i in range(12):
        (src / f"m{i}.py").write_text(f"def f{i}(): ...")
    result = auto_router.propose_source(tmp_path, large_project_threshold=10_000)
    assert result.warnings == []


def test_propose_source_missing_path_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(FileNotFoundError):
        auto_router.propose_source(tmp_path / "nonexistent")


# --- Merge gitignore into exclude -------------------------------------------


def test_merge_gitignore_into_exclude_prepends() -> None:
    """When the user supplies a custom exclude, gitignore patterns prepend it."""
    merged = auto_router.merge_gitignore_into_exclude(
        gitignore_patterns=["node_modules/**", "dist/**"],
        user_exclude="my_custom_dir/**, *.tmp",
    )
    # Both gitignore and user patterns end up in the merged result.
    assert "node_modules/**" in merged
    assert "dist/**" in merged
    assert "my_custom_dir/**" in merged
    assert "*.tmp" in merged


def test_merge_gitignore_handles_none_user_exclude() -> None:
    merged = auto_router.merge_gitignore_into_exclude(
        gitignore_patterns=["build/**"],
        user_exclude=None,
    )
    assert "build/**" in merged


def test_merge_gitignore_handles_empty_patterns() -> None:
    merged = auto_router.merge_gitignore_into_exclude(
        gitignore_patterns=[],
        user_exclude="my_custom/**",
    )
    assert "my_custom/**" in merged
