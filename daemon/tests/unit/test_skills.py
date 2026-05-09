"""Tests for the seven workflow skills shipped with the plugin."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = REPO_ROOT / "skills"

EXPECTED_SKILLS = {
    "mnemo-implement-platform",
    "mnemo-debug",
    "mnemo-refactor",
    "mnemo-add-knowledge",
    "mnemo-query-knowledge",
    "mnemo-onboard-project",
    "mnemo-review",
}


@pytest.fixture(scope="module")
def skill_files() -> dict[str, Path]:
    return {d.name: d / "SKILL.md" for d in SKILLS_DIR.iterdir() if d.is_dir()}


def test_all_seven_skills_exist(skill_files: dict[str, Path]) -> None:
    missing = EXPECTED_SKILLS - set(skill_files)
    assert not missing, f"missing skills: {missing}"
    for path in skill_files.values():
        assert path.is_file(), f"{path} not found"


def test_skill_files_have_frontmatter(skill_files: dict[str, Path]) -> None:
    for name, path in skill_files.items():
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{name}: no frontmatter"
        # Frontmatter ends at the second '---' line
        parts = text.split("---\n", 2)
        assert len(parts) >= 3, f"{name}: malformed frontmatter"
        fm = parts[1]
        assert "name:" in fm, f"{name}: missing 'name' field"
        assert "description:" in fm, f"{name}: missing 'description' field"


def test_skill_name_matches_directory(skill_files: dict[str, Path]) -> None:
    for dir_name, path in skill_files.items():
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^name:\s*(\S+)\s*$", text, re.MULTILINE)
        assert match is not None, f"{dir_name}: no name field"
        assert match.group(1) == dir_name, (
            f"name mismatch: dir={dir_name} frontmatter={match.group(1)}"
        )


def test_skill_descriptions_start_with_use_when(skill_files: dict[str, Path]) -> None:
    for name, path in skill_files.items():
        text = path.read_text(encoding="utf-8")
        # "Use when" lets Claude's skill-discovery match by trigger
        match = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
        assert match is not None, f"{name}: no description"
        desc = match.group(1).strip()
        assert desc.lower().startswith("use when"), (
            f"{name}: description should start with 'Use when' (got: {desc[:60]}...)"
        )


def test_skill_files_have_section_headers(skill_files: dict[str, Path]) -> None:
    """Each skill must have at least one ## section beyond the title."""
    for name, path in skill_files.items():
        text = path.read_text(encoding="utf-8")
        sections = re.findall(r"^##\s+\S", text, re.MULTILINE)
        assert len(sections) >= 2, (
            f"{name}: needs at least 2 second-level sections, found {len(sections)}"
        )


def test_skill_files_mention_mnemo_cli(skill_files: dict[str, Path]) -> None:
    """Each skill should give Claude at least one concrete `mnemo` invocation."""
    for name, path in skill_files.items():
        text = path.read_text(encoding="utf-8")
        assert "mnemo " in text, f"{name}: never references the mnemo CLI"


def test_skill_type_declared(skill_files: dict[str, Path]) -> None:
    """Skills declare themselves rigid or flexible in the body."""
    for name, path in skill_files.items():
        text = path.read_text(encoding="utf-8").lower()
        assert "type:" in text, f"{name}: missing 'Type:' declaration"
        assert "rigid" in text or "flexible" in text, f"{name}: doesn't declare rigid or flexible"
