"""v5.21.0 -- the companion (Mnem) must KNOW + be able to drive every
mnemo feature. The system prompt is the awareness contract: it has to
name the core tool families (incl. the knowledge auditor) so Mnem
reaches for them instead of guessing or claiming it can't.

Cheap regression guard -- if a future feature lands without being added
to Mnem's toolbelt prompt, this fails.
"""

from __future__ import annotations

from mnemo.chat import DEFAULT_SYSTEM


def test_prompt_mentions_the_auditor() -> None:
    assert "mnemo_analyze" in DEFAULT_SYSTEM, (
        "Mnem must know about the knowledge auditor (mnemo_analyze)"
    )


def test_prompt_mentions_audit_queue() -> None:
    assert "mnemo_audit_queue" in DEFAULT_SYSTEM, (
        "Mnem must know about the proactive audit queue (mnemo_audit_queue)"
    )


def test_prompt_mentions_apply_finding() -> None:
    assert "mnemo_apply_finding" in DEFAULT_SYSTEM, (
        "Mnem must know about confirm-then-apply (mnemo_apply_finding)"
    )


def test_prompt_enumerates_core_tool_families() -> None:
    # One representative tool per capability area.
    for tool in (
        "mnemo_query",  # retrieve
        "mnemo_analyze",  # audit
        "mnemo_create_node",  # edit knowledge
        "mnemo_update_node",
        "mnemo_run_skill",  # skills
        "mnemo_add_source",  # sources
        "mnemo_navigate",  # navigation
        "mnemo_page_context",  # in-page awareness
    ):
        assert tool in DEFAULT_SYSTEM, f"system prompt should name {tool}"


def test_prompt_mentions_auditor_detector_vocabulary() -> None:
    low = DEFAULT_SYSTEM.lower()
    # Mnem should know what the auditor looks for, agnostic + code lens.
    assert "stale" in low
    assert "orphan" in low
    assert "dead_code" in low or "dead code" in low


def test_prompt_keeps_navigate_last_discipline() -> None:
    low = DEFAULT_SYSTEM.lower()
    assert "navigate" in low, "preserve the navigate-last discipline"
    assert "final" in low, "navigate should be the FINAL action"
