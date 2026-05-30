"""v5.18.0 -- opt-in LLM cohesion judge for the god_object detector.

The 5th LLM helper, built on the v5.17.1 ``_LLMHelper`` base. It
re-grades god_object candidates: a cohesive single-responsibility
facade (a Store, a domain Service) is DROPPED; a grab-bag of
unrelated responsibilities is escalated to severity ``high``.

Contract:

1. ``god_object_judge_from_env()`` returns None unless
   ``MNEMO_ANALYZE_LLM_JUDGE`` + ``ANTHROPIC_API_KEY`` + anthropic
   are all present (shares the flag with the other auditor judges).
2. ``LLMCohesionJudge.judge(kind, name, members)`` returns True
   (should split / grab-bag) / False (cohesive) from parsed JSON.
3. Parse / network errors degrade to False (cohesive -> the
   candidate is dropped; never falsely escalates).
4. The member-name list reaches the model (it's the cohesion
   signal).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


def test_cohesion_judge_from_env_none_by_default() -> None:
    from mnemo.analyzer import god_object_judge_from_env

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MNEMO_ANALYZE_LLM_JUDGE", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        assert god_object_judge_from_env() is None


def test_cohesion_judge_from_env_requires_both() -> None:
    from mnemo.analyzer import god_object_judge_from_env

    with patch.dict(os.environ, {"MNEMO_ANALYZE_LLM_JUDGE": "1"}, clear=False):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        assert god_object_judge_from_env() is None

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=False):
        os.environ.pop("MNEMO_ANALYZE_LLM_JUDGE", None)
        assert god_object_judge_from_env() is None


def test_cohesion_judge_from_env_instance_when_present() -> None:
    from mnemo.analyzer import god_object_judge_from_env

    anthropic_installed = False
    try:
        import anthropic  # noqa: F401

        anthropic_installed = True
    except ImportError:
        pass

    with patch.dict(
        os.environ,
        {"MNEMO_ANALYZE_LLM_JUDGE": "1", "ANTHROPIC_API_KEY": "sk-test"},
        clear=False,
    ):
        j = god_object_judge_from_env()
        if anthropic_installed:
            assert j is not None
            assert hasattr(j, "judge")
        else:
            assert j is None


def test_judge_true_on_grab_bag() -> None:
    from mnemo.analyzer import LLMCohesionJudge

    resp = MagicMock()
    resp.content = [
        MagicMock(text='{"should_split": true, "rationale": "unrelated responsibilities"}')
    ]
    client = MagicMock()
    client.messages.create.return_value = resp

    j = LLMCohesionJudge(client=client)
    assert (
        j.judge(kind="class", name="KitchenSink", members=["parse_pdf", "send_email", "calc_tax"])
        is True
    )


def test_judge_false_on_cohesive_facade() -> None:
    from mnemo.analyzer import LLMCohesionJudge

    resp = MagicMock()
    resp.content = [
        MagicMock(text='{"should_split": false, "rationale": "cohesive storage facade"}')
    ]
    client = MagicMock()
    client.messages.create.return_value = resp

    j = LLMCohesionJudge(client=client)
    assert (
        j.judge(kind="class", name="Store", members=["get_node", "list_nodes", "upsert_node"])
        is False
    )


def test_judge_false_on_parse_error() -> None:
    from mnemo.analyzer import LLMCohesionJudge

    resp = MagicMock()
    resp.content = [MagicMock(text="not json")]
    client = MagicMock()
    client.messages.create.return_value = resp

    j = LLMCohesionJudge(client=client)
    assert j.judge(kind="class", name="X", members=["a", "b"]) is False


def test_judge_false_on_client_error() -> None:
    from mnemo.analyzer import LLMCohesionJudge

    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("network down")

    j = LLMCohesionJudge(client=client)
    assert j.judge(kind="module", name="m.py", members=["a"]) is False


def test_judge_records_rationale_log() -> None:
    from mnemo.analyzer import LLMCohesionJudge

    resp = MagicMock()
    resp.content = [MagicMock(text='{"should_split": true, "rationale": "split it"}')]
    client = MagicMock()
    client.messages.create.return_value = resp

    j = LLMCohesionJudge(client=client)
    j.judge(kind="class", name="Big", members=["x", "y"])
    assert len(j.rationale_log) == 1
    entry = j.rationale_log[0]
    assert entry["name"] == "Big"
    assert entry["should_split"] is True
    assert entry["parsed_ok"] is True


def test_judge_sends_members_to_model() -> None:
    """The member names are the cohesion signal -- they must reach the
    model's user message."""
    from mnemo.analyzer import LLMCohesionJudge

    resp = MagicMock()
    resp.content = [MagicMock(text='{"should_split": false, "rationale": "ok"}')]
    client = MagicMock()
    client.messages.create.return_value = resp

    j = LLMCohesionJudge(client=client)
    j.judge(kind="class", name="Widget", members=["frobnicate", "wibble"])
    user_msg = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "frobnicate" in user_msg
    assert "wibble" in user_msg
    assert "Widget" in user_msg
