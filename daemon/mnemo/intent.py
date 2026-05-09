"""Pattern-based intent classification.

Maps a free-text prompt to a set of intent tags, then to per-node-type
priority weights used by the retrieval scorer. Pure regex, no LLM, so it
runs in microseconds and is fully deterministic.

Tags are non-exclusive; a prompt may match multiple. ``"none"`` is returned
only when no other tag matches, and uses a balanced default type weighting.
"""

from __future__ import annotations

import re

INTENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "debug": re.compile(
        r"\b(error|fail|failure|failing|bug|broken|crash|crashed|stack[- ]?trace|"
        r"exception|traceback|unresolved|debug|why is)\b",
        re.IGNORECASE,
    ),
    "feedback-recall": re.compile(
        r"\b(prefer|preference|always|never|don'?t|do not|hard rule|reminder|remember|recall)\b",
        re.IGNORECASE,
    ),
    "project-context": re.compile(
        r"\b(this repo|this project|here|in our|our setup|on this|in this codebase)\b",
        re.IGNORECASE,
    ),
    "design": re.compile(
        r"\b(design|architecture|architect|approach|pattern|how should|how would|"
        r"best way|propose)\b",
        re.IGNORECASE,
    ),
    "reference": re.compile(
        r"\b(where is|what is|find|lookup|location of|locate|reference)\b",
        re.IGNORECASE,
    ),
}


# tag -> {node_type: priority weight in [0, 1]}
TYPE_PRIORITY: dict[str, dict[str, float]] = {
    "debug": {
        "memory_project": 1.0,
        "memory_feedback": 0.7,
        "plan_doc": 0.5,
        "project_doc": 0.5,
    },
    "feedback-recall": {
        "memory_feedback": 1.0,
        "memory_user": 0.8,
        "memory_project": 0.4,
    },
    "project-context": {
        "memory_project": 1.0,
        "project_doc": 0.9,
        "plan_doc": 0.6,
    },
    "design": {
        "plan_doc": 1.0,
        "memory_project": 0.7,
        "project_doc": 0.6,
        "memory_feedback": 0.4,
    },
    "reference": {
        "memory_reference": 1.0,
        "memory_project": 0.5,
        "project_doc": 0.4,
    },
    # Balanced default when nothing pattern-matches.
    "none": {
        "memory_user": 0.5,
        "memory_feedback": 0.6,
        "memory_project": 0.6,
        "memory_reference": 0.4,
        "project_doc": 0.5,
        "plan_doc": 0.4,
    },
}


def classify_intent(prompt: str) -> set[str]:
    """Return all matching intent tags, or ``{"none"}`` when nothing matches."""
    tags = {tag for tag, pattern in INTENT_PATTERNS.items() if pattern.search(prompt)}
    return tags or {"none"}


def type_priority_for(tags: set[str]) -> dict[str, float]:
    """Combined per-type priority across all matched tags (max-pool).

    If any tag boosts a given type, the strongest boost wins. This makes the
    function monotonic in tag count: adding a tag never lowers a type's weight.
    """
    out: dict[str, float] = {}
    for tag in tags:
        weights = TYPE_PRIORITY.get(tag, {})
        for t, w in weights.items():
            cur = out.get(t, 0.0)
            if w > cur:
                out[t] = w
    return out
