"""Tests for intent classification."""

from __future__ import annotations

from mnemo.intent import classify_intent, type_priority_for

# --- classify_intent ------------------------------------------------------


def test_debug_pattern() -> None:
    assert "debug" in classify_intent("getting an error in production")
    assert "debug" in classify_intent("crash on startup")
    assert "debug" in classify_intent("stack trace from production")


def test_feedback_recall_pattern() -> None:
    assert "feedback-recall" in classify_intent("user always prefers tabs over spaces")
    assert "feedback-recall" in classify_intent("hard rule: no emojis in commit messages")
    assert "feedback-recall" in classify_intent("reminder: this user prefers terse output")


def test_project_context_pattern() -> None:
    assert "project-context" in classify_intent("how does this repo handle deploy")
    assert "project-context" in classify_intent("in our setup, what's the env layout")


def test_design_pattern() -> None:
    assert "design" in classify_intent("how should I architect this feature")
    assert "design" in classify_intent("what's the best design pattern")
    assert "design" in classify_intent("propose an approach for the cache layer")


def test_reference_pattern() -> None:
    assert "reference" in classify_intent("where is the deploy script")
    assert "reference" in classify_intent("find the location of the dashboard")


def test_no_match_returns_none() -> None:
    assert classify_intent("random unrelated stuff abc") == {"none"}


def test_multiple_tags_match() -> None:
    tags = classify_intent("error in this repo - what design fits")
    assert "debug" in tags
    assert "project-context" in tags
    assert "design" in tags
    assert "none" not in tags


def test_classify_is_case_insensitive() -> None:
    assert "debug" in classify_intent("ERROR on startup")


# --- type_priority_for ----------------------------------------------------


def test_type_priority_for_single_tag() -> None:
    weights = type_priority_for({"feedback-recall"})
    assert weights["memory_feedback"] == 1.0
    assert weights["memory_user"] == 0.8


def test_type_priority_for_multiple_tags_max_pools() -> None:
    weights = type_priority_for({"debug", "feedback-recall"})
    # debug gives memory_project = 1.0; feedback-recall gives memory_feedback = 1.0
    assert weights["memory_project"] == 1.0
    assert weights["memory_feedback"] == 1.0


def test_type_priority_for_none_balanced() -> None:
    weights = type_priority_for({"none"})
    assert all(0.0 <= w <= 1.0 for w in weights.values())
    assert "memory_feedback" in weights
    assert "memory_project" in weights


def test_type_priority_unknown_type_missing_not_zero() -> None:
    weights = type_priority_for({"reference"})
    # 'memory_user' isn't boosted by 'reference'; should be missing (not 0).
    assert "memory_user" not in weights
