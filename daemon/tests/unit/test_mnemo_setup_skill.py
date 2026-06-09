"""v5.24.0 workstream A -- the mnemo-setup skill (AI-executable install).

Satisfies the user's "have an AI install it for you": a skill the agent
activates on "install mnemo" / "set up mnemo here" that walks the whole
install chain (engine -> MCP registration -> the /plugin commands -> doctor).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL = REPO_ROOT / "skills" / "mnemo-setup" / "SKILL.md"


def test_skill_exists() -> None:
    assert SKILL.is_file()


def test_skill_has_frontmatter() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md needs YAML frontmatter"
    fm = text.split("---\n")[1]
    assert "name:" in fm
    assert "mnemo-setup" in fm
    assert "description:" in fm


def test_skill_body_names_the_install_chain() -> None:
    """Every link the agent must drive, so a cold session can install mnemo."""
    text = SKILL.read_text(encoding="utf-8").lower()
    assert "install.sh" in text or "install.ps1" in text  # the engine
    assert "claude mcp add" in text  # MCP tools
    assert "/plugin marketplace add" in text  # register the plugin
    assert "/plugin install" in text  # enable it
    assert "mnemo doctor" in text  # verify the result
