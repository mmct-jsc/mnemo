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


def test_skill_documents_auto_proposed_actions() -> None:
    """v5.15.0: the skill must document the daemon-side
    refactor_actions enrichment -- when to pass propose_actions=true,
    how to read the action field, and that it's still never
    auto-applied."""
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert "propose_actions" in text, "skill must document the propose_actions option (v5.15.0)"
    # The action field shape the daemon attaches.
    assert '"kind"' in text or "action.kind" in text or "`action`" in text, (
        "skill must explain the action field the proposer attaches"
    )


def test_skill_auto_actions_still_never_auto_applied() -> None:
    """v5.15.0 anti-goal: even with auto-proposed actions, the skill
    must keep the 'never auto-apply' contract -- the user reviews."""
    text = SKILL_FILE.read_text(encoding="utf-8").lower()
    assert "never auto-appl" in text or "never auto-apply" in text, (
        "skill must preserve the NEVER auto-apply anti-goal even with "
        "the v5.15.0 auto-proposed actions"
    )


def test_skill_documents_code_lens() -> None:
    """v5.16.0: the skill documents the code lens + dead_code +
    how to invoke it."""
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert 'lens="code"' in text or "lens=code" in text or 'lens="code"' in text, (
        "skill must document how to invoke the code lens"
    )
    assert "dead_code" in text, "skill must describe the dead_code detector"
