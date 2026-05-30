"""v5.15.0 -- env gate for the refactor_actions LLM proposer.

Mirrors the v5.13.0 ``judge_from_env`` + v5.14.0
``semantic_orphan_judge_from_env`` opt-in pattern, but with its own
env flag so action-proposal can be toggled independently of the
detection judges.

Contract this test file locks:

1. ``refactor_proposer_from_env()`` returns ``None`` when the
   ``MNEMO_ANALYZE_PROPOSE_ACTIONS`` flag is unset OR
   ``ANTHROPIC_API_KEY`` is missing OR the anthropic package isn't
   installed.
2. It returns an ``LLMRefactorProposer`` instance only when ALL
   THREE are present.
3. The judge model override (``MNEMO_ANALYZE_JUDGE_MODEL``) is
   shared -- one model knob for every LLM-augmented auditor feature.
"""

from __future__ import annotations

import os
from unittest.mock import patch


def test_proposer_from_env_returns_none_by_default() -> None:
    """No env flag -> no proposer. The deterministic path stays
    free."""
    from mnemo.analyzer import refactor_proposer_from_env

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MNEMO_ANALYZE_PROPOSE_ACTIONS", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        assert refactor_proposer_from_env() is None


def test_proposer_from_env_requires_both_flag_and_key() -> None:
    """Flag alone or key alone -> None. Both required to opt in."""
    from mnemo.analyzer import refactor_proposer_from_env

    with patch.dict(os.environ, {"MNEMO_ANALYZE_PROPOSE_ACTIONS": "1"}, clear=False):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        assert refactor_proposer_from_env() is None, "flag alone should not enable"

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=False):
        os.environ.pop("MNEMO_ANALYZE_PROPOSE_ACTIONS", None)
        assert refactor_proposer_from_env() is None, "key alone should not enable"


def test_proposer_from_env_independent_of_judge_flag() -> None:
    """The proposer flag is independent of the detection-judge flag:
    MNEMO_ANALYZE_LLM_JUDGE alone must NOT enable the action
    proposer."""
    from mnemo.analyzer import refactor_proposer_from_env

    with patch.dict(
        os.environ,
        {"MNEMO_ANALYZE_LLM_JUDGE": "1", "ANTHROPIC_API_KEY": "sk-test"},
        clear=False,
    ):
        os.environ.pop("MNEMO_ANALYZE_PROPOSE_ACTIONS", None)
        assert refactor_proposer_from_env() is None, (
            "the detection-judge flag must not enable the action proposer"
        )


def test_proposer_from_env_returns_instance_when_all_present() -> None:
    """Both flag + key set AND anthropic importable -> an
    LLMRefactorProposer instance. Degrades to None when anthropic
    isn't installed (CI-friendly)."""
    from mnemo.analyzer import refactor_proposer_from_env

    anthropic_installed = False
    try:
        import anthropic  # noqa: F401

        anthropic_installed = True
    except ImportError:
        pass

    with patch.dict(
        os.environ,
        {"MNEMO_ANALYZE_PROPOSE_ACTIONS": "1", "ANTHROPIC_API_KEY": "sk-test"},
        clear=False,
    ):
        p = refactor_proposer_from_env()
        if anthropic_installed:
            assert p is not None, "flag + key + anthropic -> instance"
            assert hasattr(p, "propose"), (
                "LLMRefactorProposer must expose a 'propose(finding, node_bodies)' method"
            )
        else:
            assert p is None, "no anthropic package -> graceful None"
