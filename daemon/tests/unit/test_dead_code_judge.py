"""v5.16.0 -- opt-in LLM judge for the dead_code detector.

The judge filters the deterministic candidates' false positives:
private functions reached by patterns the static call graph misses
(dispatch tables, getattr, decorators, registration callbacks). It
returns is_dead true/false. Sibling to the v5.13.0/v5.14.0 judges +
the v5.15.0 proposer (lesson #109: siblings over a parameterized
abstraction).

Contract this test file locks:

1. ``dead_code_judge_from_env()`` returns None unless
   ``MNEMO_ANALYZE_LLM_JUDGE`` + ``ANTHROPIC_API_KEY`` + anthropic
   are all present (shares the flag with the other auditor judges).
2. ``LLMDeadCodeJudge.judge(name, body, source_path)`` returns
   True (genuinely dead) / False from parsed JSON.
3. Parse / network errors degrade to False (keep the deterministic
   candidate; never falsely promote to 'high').
4. With the judge in ``analyze(lens="code", dead_code_judge=...)``,
   confirmed candidates become severity 'high'; rejected ones drop.
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

from mnemo.store import Node, Store


def _mkcode(*, id: str, name: str, type: str = "code_function") -> Node:
    now = int(time.time())
    return Node(
        id=id,
        type=type,
        name=name,
        description="",
        body=f"def {name}(): ...",
        source_path="/proj/mod.py:1-5",
        source_kind="code",
        project_key="proj",
        frontmatter_json=None,
        hash="",
        created_at=now,
        updated_at=now,
    )


# --- env gate ----------------------------------------------------------


def test_dead_code_judge_from_env_none_by_default() -> None:
    from mnemo.analyzer import dead_code_judge_from_env

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MNEMO_ANALYZE_LLM_JUDGE", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        assert dead_code_judge_from_env() is None


def test_dead_code_judge_from_env_requires_both() -> None:
    from mnemo.analyzer import dead_code_judge_from_env

    with patch.dict(os.environ, {"MNEMO_ANALYZE_LLM_JUDGE": "1"}, clear=False):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        assert dead_code_judge_from_env() is None

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=False):
        os.environ.pop("MNEMO_ANALYZE_LLM_JUDGE", None)
        assert dead_code_judge_from_env() is None


def test_dead_code_judge_from_env_instance_when_present() -> None:
    from mnemo.analyzer import dead_code_judge_from_env

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
        j = dead_code_judge_from_env()
        if anthropic_installed:
            assert j is not None
            assert hasattr(j, "judge")
        else:
            assert j is None


# --- judge() shape -----------------------------------------------------


def test_judge_true_on_confirmed_dead() -> None:
    from mnemo.analyzer import LLMDeadCodeJudge

    resp = MagicMock()
    resp.content = [MagicMock(text='{"is_dead": true, "rationale": "no caller, not dispatched"}')]
    client = MagicMock()
    client.messages.create.return_value = resp

    j = LLMDeadCodeJudge(client=client)
    assert j.judge(name="_orphan", body="def _orphan(): ...", source_path="/p/m.py") is True


def test_judge_false_when_dynamically_dispatched() -> None:
    from mnemo.analyzer import LLMDeadCodeJudge

    resp = MagicMock()
    resp.content = [
        MagicMock(text='{"is_dead": false, "rationale": "selected via a dispatch table"}')
    ]
    client = MagicMock()
    client.messages.create.return_value = resp

    j = LLMDeadCodeJudge(client=client)
    assert j.judge(name="_extract_python", body="...", source_path="/p/code.py") is False


def test_judge_false_on_parse_error() -> None:
    from mnemo.analyzer import LLMDeadCodeJudge

    resp = MagicMock()
    resp.content = [MagicMock(text="not json")]
    client = MagicMock()
    client.messages.create.return_value = resp

    j = LLMDeadCodeJudge(client=client)
    assert j.judge(name="_x", body="...", source_path="/p/m.py") is False


def test_judge_false_on_client_exception() -> None:
    from mnemo.analyzer import LLMDeadCodeJudge

    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("network down")

    j = LLMDeadCodeJudge(client=client)
    assert j.judge(name="_x", body="...", source_path="/p/m.py") is False


def test_judge_records_rationale_log() -> None:
    from mnemo.analyzer import LLMDeadCodeJudge

    resp = MagicMock()
    resp.content = [MagicMock(text='{"is_dead": true, "rationale": "truly unreferenced"}')]
    client = MagicMock()
    client.messages.create.return_value = resp

    j = LLMDeadCodeJudge(client=client)
    j.judge(name="_x", body="...", source_path="/p/m.py")
    assert len(j.rationale_log) == 1
    assert j.rationale_log[0]["name"] == "_x"
    assert j.rationale_log[0]["is_dead"] is True


# --- orchestrator wiring ----------------------------------------------


def test_analyze_dead_code_judge_elevates_confirmed_to_high(tmp_path) -> None:
    from mnemo.analyzer import analyze

    store = Store(tmp_path / "mnemo.db")
    try:
        store.upsert_node(_mkcode(id="f1", name="_dead_helper"))
        confirms = MagicMock()
        confirms.judge.return_value = True
        result = analyze(store, lens="code", dead_code_judge=confirms)
        dead = [f for f in result["findings"] if f["type"] == "dead_code"]
        assert dead, "expected a dead_code finding"
        assert all(f["severity"] == "high" for f in dead), (
            f"confirming judge -> severity high; got {[f['severity'] for f in dead]}"
        )
    finally:
        store.close()


def test_analyze_dead_code_judge_drops_rejected(tmp_path) -> None:
    from mnemo.analyzer import analyze

    store = Store(tmp_path / "mnemo.db")
    try:
        store.upsert_node(_mkcode(id="f1", name="_dispatched_helper"))
        rejects = MagicMock()
        rejects.judge.return_value = False
        result = analyze(store, lens="code", dead_code_judge=rejects)
        dead = [f for f in result["findings"] if f["type"] == "dead_code"]
        assert dead == [], f"rejecting judge -> candidate dropped; got {dead}"
    finally:
        store.close()
