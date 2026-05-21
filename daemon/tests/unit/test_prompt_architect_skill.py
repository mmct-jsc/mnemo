"""v5 phase 2: ``mnemo-prompt-architect`` skill markdown contract.

The skill is the canonical primitive for v5 (the design doc's choice
for Q3). Any MCP host gets v5 for free by invoking it via the
existing ``mnemo_run_skill`` tool -- there's no new tool surface.

These tests pin the SKILL.md contract:

- Lives at ``skills/mnemo-prompt-architect/SKILL.md`` so the
  package-relative loader in ``agent_tools._skills_root`` finds it.
- Frontmatter ``name`` matches the directory.
- Frontmatter ``description`` starts with "Use when" so Claude
  Code's skill-discovery picks it up by trigger.
- Body declares the skill's purpose, the analysis steps, and the
  expected output shape (Problem / Context / Files / Acceptance /
  Anti-patterns / Prompt sections per design doc S6).
- ``mnemo_list_skills`` lists it via its frontmatter.
- ``mnemo_run_skill('mnemo-prompt-architect')`` returns the
  ``_skill`` sentinel with the body as guidance.
"""

from __future__ import annotations

import re
from pathlib import Path

from mnemo.agent_tools import TOOLS
from mnemo.store import Store
from tests.unit.test_agent_skills import _ctx

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = REPO_ROOT / "skills" / "mnemo-prompt-architect"
SKILL_MD = SKILL_DIR / "SKILL.md"


# --- File-on-disk contract ------------------------------------------------


def test_skill_md_exists() -> None:
    assert SKILL_MD.is_file(), f"{SKILL_MD} not found"


def test_skill_frontmatter_name_matches_directory() -> None:
    text = SKILL_MD.read_text(encoding="utf-8")
    match = re.search(r"^name:\s*(\S+)\s*$", text, re.MULTILINE)
    assert match is not None, "no 'name' field"
    assert match.group(1) == "mnemo-prompt-architect"


def test_skill_description_starts_with_use_when() -> None:
    text = SKILL_MD.read_text(encoding="utf-8")
    match = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
    assert match is not None, "no description"
    desc = match.group(1).strip()
    assert desc.lower().startswith("use when"), (
        f"description must start with 'Use when' (got: {desc[:60]}...)"
    )


def test_skill_body_declares_output_sections() -> None:
    """Per the v5 design doc S6, the architected output has six
    sections. The skill must instruct the agent to emit them."""
    text = SKILL_MD.read_text(encoding="utf-8").lower()
    for section in ("problem", "context", "files", "acceptance", "anti-pattern", "prompt"):
        assert section in text, f"skill body should reference '{section}' output section"


def test_skill_body_cites_mnemo_query_tool() -> None:
    """The analysis pass relies on mnemo_query for retrieval."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "mnemo_query" in text, "skill should tell the agent to call mnemo_query"


def test_skill_body_mentions_exclude_local_only() -> None:
    """The skill must instruct the agent to opt into the v5 phase 1
    local_only filter when fetching context for paste-bound output."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "exclude_local_only" in text or "local_only" in text, (
        "skill should reference the local_only filter (paste-safety contract)"
    )


def test_skill_body_uses_citation_tag_format() -> None:
    """The skill must show the [mnemo:<id>] citation pattern from S6."""
    text = SKILL_MD.read_text(encoding="utf-8")
    # Allow either literal ``[mnemo:`` or a backticked example.
    assert "mnemo:" in text, "skill should demonstrate the [mnemo:<id>] citation format"


# --- Tool-surface contract ------------------------------------------------


def test_list_skills_includes_prompt_architect(store: Store) -> None:
    out = TOOLS["mnemo_list_skills"].fn(_ctx(store))
    names = {s["name"] for s in out["skills"]}
    assert "mnemo-prompt-architect" in names


def test_run_skill_loads_prompt_architect(store: Store) -> None:
    out = TOOLS["mnemo_run_skill"].fn(_ctx(store), skill_name="mnemo-prompt-architect")
    assert "_skill" in out
    assert out["_skill"]["name"] == "mnemo-prompt-architect"
    assert "mnemo_query" in out["_skill"]["guidance"]
