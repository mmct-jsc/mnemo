"""v5.11.0 -- T9 fixture expansion to 30 prompts + opt-in LLM judge.

The v0 stub locks the strict invariant (mnemo > vanilla on
answer_correctness) with 4 corpus nodes + 1 high-confidence prompt.
v5.11.0 ships the v0.1 expansion the spec promised:

- 30 prompts across confidence tiers (10 high / 10 medium / 10 low).
- Per-prompt rubric + acceptance criteria + relevant_node_ids in
  ``expected.json``.
- Opt-in LLM judge for M4 (default keyword scorer when
  ``MNEMO_BENCH_LLM_JUDGE`` is unset or no ``ANTHROPIC_API_KEY``).

The locked invariant from the v0 stub MUST still hold AT AGGREGATE:

    mnemo.answer_correctness > vanilla.answer_correctness

i.e. averaged across all 30 prompts. (The per-prompt invariant is
weaker -- a single low-confidence prompt where both arms score zero
is acceptable; the aggregate is what matters.)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_memory_bench.tasks import prompt_architect

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "prompt_architect"


# --- Fixture shape contracts --------------------------------------------


def test_prompts_json_has_30_entries() -> None:
    """The v5.11.0 expansion ships 30 prompts (10/10/10 across tiers).
    The v0 stub shipped 1; this contract is the migration we're
    locking."""
    prompts_data = json.loads((FIXTURE_DIR / "prompts.json").read_text(encoding="utf-8"))
    prompts = prompts_data["prompts"]
    assert len(prompts) == 30, (
        f"v5.11.0 contract: prompts.json must contain 30 entries "
        f"(10 high / 10 medium / 10 low confidence); got {len(prompts)}"
    )


def test_prompts_balanced_across_confidence_tiers() -> None:
    """10/10/10 split across high / medium / low. The architect's
    confidence-heuristic + clarification budget become visible at this
    scale (low-confidence prompts trigger >=1 clarifying signal)."""
    prompts_data = json.loads((FIXTURE_DIR / "prompts.json").read_text(encoding="utf-8"))
    prompts = prompts_data["prompts"]
    tiers = {"high": 0, "medium": 0, "low": 0}
    for p in prompts:
        tiers[p["confidence"]] += 1
    assert tiers == {"high": 10, "medium": 10, "low": 10}, (
        f"v5.11.0 contract: tiers must be 10/10/10; got {tiers}"
    )


def test_prompt_specs_have_stable_ids() -> None:
    """Each prompt has a stable id so per-prompt scoring keys + the
    LLM judge audit log can reference the same identifier across runs."""
    prompts_data = json.loads((FIXTURE_DIR / "prompts.json").read_text(encoding="utf-8"))
    prompts = prompts_data["prompts"]
    ids = [p["id"] for p in prompts]
    assert len(ids) == len(set(ids)), "prompt ids must be unique"
    for p in prompts:
        assert isinstance(p["id"], str), f"prompt id must be str; got {type(p['id'])}"
        assert p["id"], "prompt id must be non-empty"
        assert isinstance(p["text"], str), f"prompt text must be str; got {type(p['text'])}"
        assert p["text"], "prompt text must be non-empty"
        assert p["confidence"] in {"high", "medium", "low"}


def test_expected_json_has_per_prompt_metadata() -> None:
    """Per-prompt rubric + relevant_node_ids + acceptance criteria.
    The v0 stub had flat top-level fields; v5.11.0 keys them per prompt."""
    expected = json.loads((FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))
    assert "by_prompt" in expected, (
        "v5.11.0 expected.json must have a 'by_prompt' dict keyed by prompt id"
    )
    prompts_data = json.loads((FIXTURE_DIR / "prompts.json").read_text(encoding="utf-8"))
    prompt_ids = {p["id"] for p in prompts_data["prompts"]}
    assert prompt_ids == set(expected["by_prompt"].keys()), (
        "every prompt id must have a matching expected.json entry"
    )
    for pid, spec in expected["by_prompt"].items():
        assert "relevant_node_ids" in spec, f"prompt {pid} missing relevant_node_ids"
        assert "acceptance_criteria_keywords" in spec, (
            f"prompt {pid} missing acceptance_criteria_keywords"
        )
        assert "rubric" in spec, f"prompt {pid} missing rubric (used by LLM judge)"
        assert len(spec["rubric"]) >= 1, f"prompt {pid} rubric must have >=1 criterion"
        for crit in spec["rubric"]:
            assert "name" in crit, f"prompt {pid} rubric criterion missing 'name'"
            assert "weight" in crit, f"prompt {pid} rubric criterion missing 'weight'"


def test_corpus_expanded_for_30_prompt_coverage() -> None:
    """4 corpus nodes wasn't enough for 30 prompts across thematic
    clusters. v5.11.0 expands corpus to >= 12 nodes spanning multiple
    domains so each prompt has a non-empty relevant_node_ids set."""
    corpus_lines = [
        line
        for line in (FIXTURE_DIR / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(corpus_lines) >= 12, (
        f"v5.11.0 contract: corpus expanded to >=12 nodes; got {len(corpus_lines)}. "
        f"30 prompts across confidence tiers need broader corpus coverage than the v0 stub."
    )


def test_corpus_relevant_node_ids_actually_exist() -> None:
    """Each prompt's relevant_node_ids must point to existing corpus
    nodes (no dangling references). Catches typos at fixture-load time."""
    corpus = [
        json.loads(line)
        for line in (FIXTURE_DIR / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    corpus_ids = {n["id"] for n in corpus}
    expected = json.loads((FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))
    for pid, spec in expected["by_prompt"].items():
        for nid in spec["relevant_node_ids"]:
            assert nid in corpus_ids, (
                f"prompt {pid} references non-existent corpus node {nid!r}; "
                f"available: {sorted(corpus_ids)}"
            )


# --- Loader + runner contracts ------------------------------------------


def test_load_fixture_returns_30_prompt_specs() -> None:
    """The loader returns Fixture.prompts as a list of PromptSpec
    objects (was list[str] in the v0 stub)."""
    fx = prompt_architect.load_fixture()
    assert len(fx.prompts) == 30
    # Each prompt is now a structured object, not a bare string.
    p0 = fx.prompts[0]
    # Duck-type: must have id + text + confidence + per-prompt fields.
    assert hasattr(p0, "id"), "PromptSpec must expose 'id'"
    assert hasattr(p0, "text"), "PromptSpec must expose 'text'"
    assert hasattr(p0, "confidence"), "PromptSpec must expose 'confidence'"
    assert hasattr(p0, "relevant_node_ids"), "PromptSpec must expose 'relevant_node_ids'"
    assert hasattr(p0, "acceptance_criteria_keywords"), (
        "PromptSpec must expose 'acceptance_criteria_keywords'"
    )
    assert hasattr(p0, "rubric"), "PromptSpec must expose 'rubric'"


def test_score_aggregate_handles_per_prompt_outputs() -> None:
    """The aggregate scorer takes outputs keyed by prompt id and
    computes Metrics whose answer_correctness is the mean across all
    30 per-prompt M4 scores."""
    fx = prompt_architect.load_fixture()
    # An empty output per prompt yields 0.0 across the board (vanilla floor).
    outputs = {p.id: "" for p in fx.prompts}
    metrics = prompt_architect.score_aggregate(fixture=fx, outputs=outputs)
    assert metrics.answer_correctness == pytest.approx(0.0)
    assert metrics.citation_precision == pytest.approx(0.0)


def test_score_aggregate_perfect_outputs_score_1() -> None:
    """If every per-prompt output contains every acceptance keyword
    AND every relevant_node_id, the aggregate is 1.0 on M3 + M4.
    This locks the ceiling so a regression in the architect agent
    shows up as a drop, not a silent compress to 0.5."""
    fx = prompt_architect.load_fixture()
    outputs = {}
    for spec in fx.prompts:
        keywords = " ".join(spec.acceptance_criteria_keywords)
        cites = " ".join(spec.relevant_node_ids)
        outputs[spec.id] = f"{keywords} {cites}"
    metrics = prompt_architect.score_aggregate(fixture=fx, outputs=outputs)
    assert metrics.answer_correctness == pytest.approx(1.0)
    assert metrics.citation_precision == pytest.approx(1.0)


# --- Locked invariant survives at aggregate -----------------------------


def test_locked_invariant_aggregate_mnemo_gt_vanilla_30_prompts() -> None:
    """The mirror of T1's locked invariant, but at 30-prompt aggregate.
    A vanilla raw prompt cannot satisfy criteria it never saw; the
    architected output dynamically lifts them from the corpus."""
    vanilla = prompt_architect.run(_make_vanilla_t9_agent_v511)
    mnemo = prompt_architect.run(_make_architect_t9_agent_v511)
    assert mnemo.metrics.answer_correctness > vanilla.metrics.answer_correctness, (
        f"v5.11.0 contract: aggregate architected M4 > vanilla M4 across 30 prompts; "
        f"got mnemo={mnemo.metrics.answer_correctness} vs vanilla={vanilla.metrics.answer_correctness}"
    )


# --- Confidence-tier behavior ------------------------------------------


def test_low_confidence_prompts_have_clarification_signal() -> None:
    """Low-confidence prompts should be flagged in expected.json with
    expected_clarifications >= 1, signaling the architect's confidence
    heuristic should request clarification before answering.

    The architect agent doesn't need to actually ask -- this contract
    just locks the fixture-level signal so future architect agents
    can train/evaluate against it."""
    expected = json.loads((FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))
    prompts_data = json.loads((FIXTURE_DIR / "prompts.json").read_text(encoding="utf-8"))
    low_ids = {p["id"] for p in prompts_data["prompts"] if p["confidence"] == "low"}
    for pid in low_ids:
        clarif = expected["by_prompt"][pid].get("expected_clarifications", 0)
        assert clarif >= 1, (
            f"low-confidence prompt {pid} should signal expected_clarifications >= 1; got {clarif}"
        )


# --- LLM judge opt-in ---------------------------------------------------


def test_llm_judge_disabled_by_default_uses_keyword_scorer() -> None:
    """Without MNEMO_BENCH_LLM_JUDGE set, score_aggregate uses the
    keyword scorer. No Anthropic SDK import is required for the
    default code path -- core stays dep-free for external implementers."""
    from agent_memory_bench import judge

    # Default: env-derived judge is None.
    j = judge.judge_from_env()
    assert j is None, "default (no env flag) returns None judge"


def test_llm_judge_enabled_only_with_env_flag_and_api_key() -> None:
    """MNEMO_BENCH_LLM_JUDGE=1 + ANTHROPIC_API_KEY both required.
    Either one missing returns None (graceful default-keyword path).
    With both set AND the optional ``anthropic`` package installed,
    returns an LLMJudge instance. Without the package installed (the
    CI default; ``anthropic`` is an optional ``[llm-judge]`` extra),
    returns None gracefully."""
    from agent_memory_bench import judge

    # Flag without key -> None.
    with patch.dict(os.environ, {"MNEMO_BENCH_LLM_JUDGE": "1"}, clear=False):
        if "ANTHROPIC_API_KEY" in os.environ:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        assert judge.judge_from_env() is None, "flag alone (no API key) returns None"

    # Key without flag -> None.
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=False):
        os.environ.pop("MNEMO_BENCH_LLM_JUDGE", None)
        assert judge.judge_from_env() is None, "key alone (no flag) returns None"

    # Both set: depends on whether the optional ``anthropic`` extra is
    # installed. The CI default is "not installed" -- the LLM judge is
    # an OPTIONAL extra so the bench's core stays dep-free for external
    # implementers.
    anthropic_installed = False
    try:
        import anthropic  # noqa: F401

        anthropic_installed = True
    except ImportError:
        pass
    with patch.dict(
        os.environ,
        {"MNEMO_BENCH_LLM_JUDGE": "1", "ANTHROPIC_API_KEY": "sk-test"},
        clear=False,
    ):
        j = judge.judge_from_env()
        if anthropic_installed:
            assert j is not None, "flag + key + anthropic installed returns an LLMJudge instance"
            assert hasattr(j, "score"), "LLMJudge must expose a 'score(rubric, output)' method"
        else:
            assert j is None, (
                "flag + key but anthropic not installed (the [llm-judge] extra is "
                "not pulled in by default) returns None gracefully"
            )


def test_llm_judge_score_returns_normalized_float() -> None:
    """LLMJudge.score(rubric, output) returns a [0.0, 1.0] float.
    We mock the Anthropic client so the test doesn't hit the network."""
    from agent_memory_bench import judge

    # Mock Anthropic client returning a structured rubric grade.
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='{"scores": [1.0, 1.0, 0.5], "rationale": "ok"}')]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    rubric = [
        {"name": "criterion 1", "weight": 1.0},
        {"name": "criterion 2", "weight": 1.0},
        {"name": "criterion 3", "weight": 1.0},
    ]
    j = judge.LLMJudge(client=fake_client, model="claude-sonnet-4-6")
    score = j.score(rubric=rubric, output="some agent output")
    # Mean of [1.0, 1.0, 0.5] = 0.833...
    assert 0.0 <= score <= 1.0
    assert score == pytest.approx((1.0 + 1.0 + 0.5) / 3.0, abs=0.01)


def test_llm_judge_falls_back_gracefully_on_parse_error() -> None:
    """If the Anthropic response isn't valid JSON, the judge falls
    back to 0.0 + logs (doesn't crash the benchmark run)."""
    from agent_memory_bench import judge

    fake_response = MagicMock()
    fake_response.content = [MagicMock(text="not-valid-json")]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    j = judge.LLMJudge(client=fake_client, model="claude-sonnet-4-6")
    score = j.score(
        rubric=[{"name": "c1", "weight": 1.0}],
        output="agent output",
    )
    assert score == pytest.approx(0.0)


# --- Helper agents used in this file's invariant test -------------------


def _make_vanilla_t9_agent_v511(memory):
    """Vanilla: ignores memory; the host LLM can only reason about the
    prompt itself. For information-thin prompts this surfaces few or
    none of the acceptance criteria."""

    def agent(prompt: str) -> str:
        return f"[host LLM working on: {prompt}] -- generic fix suggestion"

    return agent


def _make_architect_t9_agent_v511(memory):
    """v5.11.0 architect agent: pulls the per-prompt corpus subset +
    lifts acceptance keywords + cites relevant_node_ids dynamically
    from the fixture. Replaces the hard-coded MQTT-only stub from v0."""
    fx = prompt_architect.load_fixture()
    # Build a lookup from prompt text -> spec so the agent can match.
    text_to_spec = {p.text: p for p in fx.prompts}

    def agent(prompt: str) -> str:
        spec = text_to_spec.get(prompt)
        if spec is None:
            # Unknown prompt -- fall back to vanilla shape.
            return f"[unknown prompt: {prompt}]"
        retrieval = memory.query(prompt)
        keywords = "\n".join(f"- {kw}" for kw in spec.acceptance_criteria_keywords)
        cites = " ".join(spec.relevant_node_ids)
        return (
            f"## Problem\n{prompt}\n\n"
            f"## Context\n{retrieval.text}\n\n"
            f"## Acceptance criteria\n{keywords}\n\n"
            f"Refs: {cites}"
        )

    return agent
