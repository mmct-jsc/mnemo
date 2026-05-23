"""v5.12.0 -- mnemo:knowledge-auditor skill markdown contract.

Mirror of test_prompt_architect_skill.py / test_mnemo_prompt_slash_command.py
patterns. Verifies the SKILL.md exists, has the expected frontmatter,
and names the canonical workflow (call mnemo_analyze; group by
severity; propose actions via existing primitives).

We don't execute the skill -- the skill loader does that at agent
runtime. The test verifies the FILE CONTRACT so a typo / rename
surfaces as a unit-test failure.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = REPO_ROOT / "skills" / "mnemo-knowledge-auditor"
SKILL_FILE = SKILL_DIR / "SKILL.md"


def test_skill_file_exists() -> None:
    assert SKILL_FILE.is_file(), f"missing skill at {SKILL_FILE}"


def test_skill_has_frontmatter_name() -> None:
    """The skill loader resolves by frontmatter ``name`` (preferred) or
    directory name. Ensure the frontmatter declares the canonical
    name."""
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "skill must start with YAML frontmatter"
    head, _, _body = text[4:].partition("\n---")
    # Existing skills (mnemo-prompt-architect, mnemo-debug, etc.) use a
    # dash form in the frontmatter name; documentation often refers to
    # the colon form ("mnemo:knowledge-auditor"). Both shapes are
    # acceptable -- the loader resolves either.
    assert "name: mnemo-knowledge-auditor" in head or "name: mnemo:knowledge-auditor" in head, (
        "skill frontmatter must declare 'name: mnemo-knowledge-auditor' "
        "(or the colon variant); matches the canonical skill identifier."
    )


def test_skill_references_mnemo_analyze_tool() -> None:
    """The skill's workflow MUST call mnemo_analyze (it's the only
    auditor entry point in v5.12.0)."""
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert "mnemo_analyze" in text, "skill body must reference the mnemo_analyze MCP tool"


def test_skill_groups_by_severity() -> None:
    """The skill's report instructions group findings by severity --
    high before medium before low -- so users see the most urgent
    items first."""
    text = SKILL_FILE.read_text(encoding="utf-8")
    for severity in ("high", "medium", "low"):
        assert severity in text.lower(), (
            f"skill should mention {severity!r} severity in its workflow"
        )


def test_skill_proposes_action_primitives() -> None:
    """For each finding type, the skill should propose at least one
    concrete action using existing mnemo_* primitives. Phase 1
    anti-goal: no silent edits, so the skill PROPOSES, never executes
    automatically."""
    text = SKILL_FILE.read_text(encoding="utf-8")
    # Existing primitives the auditor can recommend.
    found_primitives = sum(
        1 for primitive in ("mnemo_update_node", "mnemo_delete_node") if primitive in text
    )
    assert found_primitives >= 1, (
        "skill should propose at least one concrete action primitive "
        "(mnemo_update_node / mnemo_delete_node) per its design contract"
    )
