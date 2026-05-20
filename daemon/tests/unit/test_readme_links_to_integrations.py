"""README must link to the integrations index so non-Claude users find us.

Phase 1.4 of the enterprise execution plan. Companion to the existing
``test_readme_links_to_docs`` closed-set (in
``daemon/tests/unit/test_docs.py``): adding the integrations link is
ADDITIVE there -- it does not replace any of the 6 required links.

The index itself must list both Phase 1 flagship hosts so the
provider-neutral positioning is genuinely two-clicks-from-the-top of
the repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def readme() -> str:
    return (REPO_ROOT / "README.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def integrations_index() -> str:
    p = REPO_ROOT / "docs" / "integrations" / "README.md"
    assert p.is_file(), (
        f"docs/integrations/README.md not found at {p}; Phase 1.4 requires the index "
        "so README's provider-neutral section can link to it."
    )
    return p.read_text(encoding="utf-8")


def test_readme_links_to_integrations_index(readme: str) -> None:
    """README must reference ``docs/integrations`` (the index path) so a
    fresh visitor finds the non-Claude mount guides without having to
    grep the repo."""
    assert "docs/integrations" in readme, (
        "README.md must include a link to docs/integrations/ (the "
        "provider-neutral positioning that Phase 1 ships)."
    )


def test_integrations_index_lists_both_phase_1_picks(integrations_index: str) -> None:
    """The Phase 1.1 picks (Cursor + OpenAI Agents SDK) must each be
    reachable from the index. Lowercase comparison: index can format
    "Cursor" / "cursor" / "Cursor IDE" / etc."""
    body = integrations_index.lower()
    assert "cursor" in body, "integrations index must list Cursor (the IDE-embedded flagship)."
    assert "openai" in body or "agents-sdk" in body, (
        "integrations index must list the OpenAI Agents SDK (the agent-loop flagship)."
    )


def test_integrations_index_references_the_mount_guides(integrations_index: str) -> None:
    """Beyond mentioning the hosts by name, the index must actually link
    to the per-host mount docs so the click-through works."""
    assert "cursor.md" in integrations_index, "integrations index must link to cursor.md."
    assert "openai-agents-sdk.md" in integrations_index, (
        "integrations index must link to openai-agents-sdk.md."
    )


def test_integrations_index_explains_the_picks(integrations_index: str) -> None:
    """The index must point at PICKS.md so the rubric + deferred
    candidates are one click away (the strategy doc's anti-goal review
    cadence needs that visible receipt)."""
    assert "PICKS.md" in integrations_index, (
        "integrations index must link to PICKS.md so the rubric + "
        "deferred candidates have a visible receipt."
    )
