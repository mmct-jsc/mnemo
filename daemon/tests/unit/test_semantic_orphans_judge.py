"""v5.14.0 -- opt-in LLM judge for semantic_orphans detection.

The judge escalates deterministic candidate orphans (from
``detect_semantic_orphans``) to Claude for a binary "needs definition
or not" decision. SIBLING to v5.13.0's ``LLMContradictionJudge``
(different prompt, different return semantics) rather than a shared
abstraction -- one judge class per detector keeps prompts simple.

Contract this test file locks:

1. ``semantic_orphan_judge_from_env()`` returns ``None`` when env
   flag is unset OR the API key is missing OR the anthropic package
   isn't installed. Shares ``MNEMO_ANALYZE_LLM_JUDGE`` +
   ``MNEMO_ANALYZE_JUDGE_MODEL`` with the contradictions judge.
2. ``semantic_orphan_judge_from_env()`` returns an
   ``LLMSemanticOrphanJudge`` instance only when ALL THREE are
   present.
3. ``LLMSemanticOrphanJudge.judge(concept, context)`` returns
   ``True | False`` from a parsed JSON response.
4. Parse failures degrade to ``False`` (the candidate is DROPPED,
   not surfaced as 'high' or 'candidate' -- the judge is
   authoritative when enabled).
5. With the judge enabled in ``analyze(...)``, confirmed concepts
   get severity ``high``; rejected concepts are dropped.
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

from mnemo.store import Node, Store


def _mknode(
    *,
    id: str,
    type: str = "memory_feedback",
    name: str | None = None,
    description: str = "",
    body: str = "",
) -> Node:
    now = int(time.time())
    return Node(
        id=id,
        type=type,
        name=name if name is not None else id.split("/", 1)[-1],
        description=description,
        body=body,
        source_path=f"/tmp/{id}.md",
        source_kind="memory",
        project_key=None,
        frontmatter_json=None,
        hash="",
        created_at=now,
        updated_at=now,
    )


# --- semantic_orphan_judge_from_env() opt-in gate ----------------------


def test_orphan_judge_from_env_returns_none_by_default() -> None:
    """No env flag set -> no judge. The deterministic path stays
    intact + free."""
    from mnemo.analyzer import semantic_orphan_judge_from_env

    # Ensure both env vars are absent in this test process WITHOUT
    # wiping HOME (anthropic.Anthropic() needs it to expand ~).
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MNEMO_ANALYZE_LLM_JUDGE", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        assert semantic_orphan_judge_from_env() is None


def test_orphan_judge_from_env_requires_both_flag_and_api_key() -> None:
    """Flag alone or key alone -> None. Both required to opt in."""
    from mnemo.analyzer import semantic_orphan_judge_from_env

    # Flag alone (key absent).
    with patch.dict(os.environ, {"MNEMO_ANALYZE_LLM_JUDGE": "1"}, clear=False):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        assert semantic_orphan_judge_from_env() is None, "flag alone should not enable the judge"

    # Key alone (flag absent).
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=False):
        os.environ.pop("MNEMO_ANALYZE_LLM_JUDGE", None)
        assert semantic_orphan_judge_from_env() is None, "key alone should not enable the judge"


def test_orphan_judge_from_env_returns_instance_when_all_present() -> None:
    """Both flag + key set AND anthropic package importable -> an
    LLMSemanticOrphanJudge instance.

    If the anthropic package isn't installed in the test env, the
    helper degrades to None (CI-friendly)."""
    from mnemo.analyzer import semantic_orphan_judge_from_env

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
        j = semantic_orphan_judge_from_env()
        if anthropic_installed:
            assert j is not None, "flag + key + anthropic installed -> instance"
            assert hasattr(j, "judge"), (
                "LLMSemanticOrphanJudge must expose a 'judge(concept, context)' method"
            )
        else:
            assert j is None, "no anthropic package -> graceful None"


# --- LLMSemanticOrphanJudge.judge() shape ------------------------------


def test_orphan_judge_returns_true_for_project_specific_term() -> None:
    """The judge returns True for a clear project-specific orphan
    (e.g. PetroLimexEdgeOrchestrator)."""
    from mnemo.analyzer import LLMSemanticOrphanJudge

    fake_response = MagicMock()
    fake_response.content = [
        MagicMock(
            text='{"needs_definition": true, "rationale": "project-specific component, undefined"}'
        )
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    j = LLMSemanticOrphanJudge(client=fake_client, model="claude-sonnet-4-6")
    result = j.judge(
        concept="PetroLimexEdgeOrchestrator",
        context="We orchestrate via PetroLimexEdgeOrchestrator daily.",
    )
    assert result is True


def test_orphan_judge_returns_false_for_common_term() -> None:
    """For a common term (Redis, MQTT, JSON), the judge returns
    False -- not every common library needs a graph node."""
    from mnemo.analyzer import LLMSemanticOrphanJudge

    fake_response = MagicMock()
    fake_response.content = [
        MagicMock(text='{"needs_definition": false, "rationale": "common library, well-known"}')
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    j = LLMSemanticOrphanJudge(client=fake_client, model="claude-sonnet-4-6")
    result = j.judge(concept="Redis", context="We use Redis for caching.")
    assert result is False


def test_orphan_judge_returns_false_on_parse_error() -> None:
    """A garbled response from the model degrades to False -- the
    concept is DROPPED (the judge is authoritative when enabled)."""
    from mnemo.analyzer import LLMSemanticOrphanJudge

    fake_response = MagicMock()
    fake_response.content = [MagicMock(text="not-valid-json")]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    j = LLMSemanticOrphanJudge(client=fake_client, model="claude-sonnet-4-6")
    result = j.judge(concept="MysteryConcept", context="Used here.")
    assert result is False


def test_orphan_judge_returns_false_on_client_exception() -> None:
    """Network errors / SDK errors -> False (graceful)."""
    from mnemo.analyzer import LLMSemanticOrphanJudge

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("network down")

    j = LLMSemanticOrphanJudge(client=fake_client, model="claude-sonnet-4-6")
    result = j.judge(concept="X", context="...")
    assert result is False


def test_orphan_judge_records_rationale_log() -> None:
    """The judge keeps an audit trail of decisions for operator
    review post-sweep."""
    from mnemo.analyzer import LLMSemanticOrphanJudge

    fake_response = MagicMock()
    fake_response.content = [
        MagicMock(text='{"needs_definition": true, "rationale": "project-specific term"}')
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    j = LLMSemanticOrphanJudge(client=fake_client, model="claude-sonnet-4-6")
    j.judge(concept="ProjectTerm", context="We use ProjectTerm.")
    assert len(j.rationale_log) == 1
    entry = j.rationale_log[0]
    assert entry["concept"] == "ProjectTerm"
    assert entry["needs_definition"] is True
    assert "project-specific" in entry["rationale"].lower()


# --- analyze() with judge wires through --------------------------------


def test_analyze_with_orphan_judge_elevates_confirmed_to_high(tmp_path) -> None:
    """When ``analyze(orphan_judge=...)`` is called, confirmed
    candidates get severity 'high'; rejected ones disappear."""
    from mnemo.analyzer import analyze

    store = Store(tmp_path / "mnemo.db")
    try:
        source = _mknode(
            id="memory_feedback/uses-orphan",
            name="Notes",
            body="We rely on the OrphanedComponent abstraction heavily.",
        )
        store.upsert_node(source)

        # A judge that always confirms.
        always_confirms = MagicMock()
        always_confirms.judge.return_value = True

        result = analyze(
            store,
            types=["semantic_orphans"],
            orphan_judge=always_confirms,
        )
        orphans = [f for f in result["findings"] if f["type"] == "semantic_orphan"]
        assert orphans, "expected at least one semantic_orphan finding"
        assert all(f["severity"] == "high" for f in orphans), (
            f"with confirming judge, severity should be 'high'; got {[f['severity'] for f in orphans]}"
        )
    finally:
        store.close()


def test_analyze_with_orphan_judge_drops_rejected_candidates(tmp_path) -> None:
    """A judge that says "no" -> the candidate is filtered out
    (not returned with severity=candidate either)."""
    from mnemo.analyzer import analyze

    store = Store(tmp_path / "mnemo.db")
    try:
        source = _mknode(
            id="memory_feedback/uses-common",
            name="Notes",
            body="We use Redis and PostgreSQL for caching and persistence.",
        )
        store.upsert_node(source)

        # A judge that always rejects.
        always_rejects = MagicMock()
        always_rejects.judge.return_value = False

        result = analyze(
            store,
            types=["semantic_orphans"],
            orphan_judge=always_rejects,
        )
        orphans = [f for f in result["findings"] if f["type"] == "semantic_orphan"]
        assert orphans == [], (
            f"with rejecting judge, all candidates should be dropped; got {orphans}"
        )
    finally:
        store.close()
