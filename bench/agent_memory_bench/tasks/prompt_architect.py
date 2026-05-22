"""T9 -- "prompt architect makes the host LLM's output satisfy more
acceptance criteria than a raw prompt does".

Spec section: see ``docs/benchmark/agent-memory-spec-v0.md`` (T9
appendix). The fixture lives at ``bench/fixtures/prompt_architect/``.

The strict invariant this task enforces:

    mnemo.answer_correctness > vanilla.answer_correctness

This MIRRORS T1's locked invariant but in the OPPOSITE direction
-- T1 says vanilla re-derives MORE; T9 says mnemo's architected
prompt yields BETTER downstream satisfaction. The substrate
("typed Graph-RAG context is the wedge") fails if either
invariant inverts.

The v0 stub shipped 4 corpus nodes + 1 prompt with flat
top-level fields. v5.11.0 expands to 18 corpus nodes + 30 prompts
across 10/10/10 confidence tiers, with per-prompt metadata
(relevant_node_ids, acceptance_criteria_keywords, rubric,
expected_clarifications) under ``expected.json``'s ``by_prompt``
dict. The scorer aggregates the per-prompt M3/M4 into a single
Metrics object.

The LLM judge for M4 is opt-in via ``MNEMO_BENCH_LLM_JUDGE=1`` +
``ANTHROPIC_API_KEY``; without both, scoring falls back to the
keyword scorer (default + CI-friendly).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agent_memory_bench.runner import Memory, Metrics, Retrieval, TaskResult

FIXTURE_DIR: Path = Path(__file__).resolve().parent.parent.parent / "fixtures" / "prompt_architect"
TASK_ID: str = "prompt_architect"


Confidence = Literal["high", "medium", "low"]


@dataclass
class PromptSpec:
    """One prompt + its per-prompt expected metadata.

    v5.11.0 promotes prompts from bare strings to structured
    PromptSpec objects carrying everything the scorer needs (no
    second lookup by id). Keeps the runner's contract simple while
    making per-prompt scoring deterministic."""

    id: str
    confidence: Confidence
    text: str
    relevant_node_ids: set[str]
    acceptance_criteria_keywords: list[str]
    rubric: list[dict[str, Any]]
    expected_clarifications: int = 0


@dataclass
class Fixture:
    """Parsed contents of T9's fixture directory."""

    corpus: list[dict]
    """The memory nodes the architect can pull. v5.11.0 ships 18 nodes
    spanning 6 thematic clusters (MQTT auth / daemon lifecycle /
    retrieval / build / UI / policy) so 30 prompts across confidence
    tiers each have a non-empty relevant subset."""

    prompts: list[PromptSpec]
    """The 30 prompts (10 high / 10 medium / 10 low) the architect
    transforms. v0 stub used list[str] with shared metadata; v5.11.0
    keys metadata per-prompt."""

    invariant: str = ""
    """Free-text description of the locked invariant for audit."""


def load_fixture(fixture_dir: Path = FIXTURE_DIR) -> Fixture:
    """Parse ``corpus.jsonl`` + ``prompts.json`` + ``expected.json``."""
    corpus = [
        json.loads(line)
        for line in (fixture_dir / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    prompts_data = json.loads((fixture_dir / "prompts.json").read_text(encoding="utf-8"))
    expected = json.loads((fixture_dir / "expected.json").read_text(encoding="utf-8"))

    by_prompt = expected.get("by_prompt", {})
    specs: list[PromptSpec] = []
    for p in prompts_data["prompts"]:
        meta = by_prompt.get(p["id"], {})
        specs.append(
            PromptSpec(
                id=p["id"],
                confidence=p["confidence"],
                text=p["text"],
                relevant_node_ids=set(meta.get("relevant_node_ids", [])),
                acceptance_criteria_keywords=list(meta.get("acceptance_criteria_keywords", [])),
                rubric=list(meta.get("rubric", [])),
                expected_clarifications=int(meta.get("expected_clarifications", 0)),
            )
        )

    return Fixture(
        corpus=corpus,
        prompts=specs,
        invariant=expected.get("invariant", ""),
    )


def _make_tracking_memory(corpus: list[dict]) -> tuple[Memory, list[str]]:
    """Build a Memory that returns the full corpus on every query.

    Mirrors T1's tracking memory. The architect agent makes ONE
    query per prompt (to assemble the prompt); the vanilla agent
    makes zero (it just echoes the raw prompt to the host)."""
    queries: list[str] = []

    class _TrackingMemory:
        def query(self, prompt: str, max_tokens: int = 800) -> Retrieval:
            queries.append(prompt)
            text = "\n".join(f"[{n['id']}] {n['body']}" for n in corpus)
            hit_ids = [n["id"] for n in corpus]
            return Retrieval(
                text=text[:max_tokens],
                hit_ids=hit_ids,
                tokens_used=min(len(text), max_tokens),
            )

        def feedback(self, hit_id: str, direction) -> None:  # noqa: ARG002
            return None

    return _TrackingMemory(), queries


# --- Scoring -----------------------------------------------------------


@dataclass
class PromptScore:
    """Per-prompt scoring breakdown for audit + LLM judge logs."""

    prompt_id: str
    answer_correctness: float
    citation_precision: float


def _score_keywords(spec: PromptSpec, output: str) -> float:
    """Default keyword-based M4 scorer. Fraction of acceptance
    keywords surfaced in the output."""
    if not spec.acceptance_criteria_keywords:
        return 0.0
    matched = sum(1 for kw in spec.acceptance_criteria_keywords if kw.lower() in output.lower())
    return matched / len(spec.acceptance_criteria_keywords)


def _score_citations(spec: PromptSpec, output: str) -> float:
    """Per-prompt M3. Fraction of relevant_node_ids cited in the output."""
    if not spec.relevant_node_ids:
        return 0.0
    cited = sum(1 for nid in spec.relevant_node_ids if nid in output)
    return cited / len(spec.relevant_node_ids)


def score_prompt(
    spec: PromptSpec,
    output: str,
    *,
    judge: Any | None = None,
) -> PromptScore:
    """Score a single prompt's output.

    If a judge is provided + the prompt has a non-empty rubric, the
    judge.score(rubric=..., output=...) call replaces the keyword
    scorer for M4. M3 (citation_precision) always uses the
    deterministic citation check."""
    if judge is not None and spec.rubric:
        m4 = judge.score(rubric=spec.rubric, output=output)
    else:
        m4 = _score_keywords(spec, output)
    m3 = _score_citations(spec, output)
    return PromptScore(
        prompt_id=spec.id,
        answer_correctness=float(m4),
        citation_precision=float(m3),
    )


def score_aggregate(
    *,
    fixture: Fixture,
    outputs: Mapping[str, str],
    judge: Any | None = None,
) -> Metrics:
    """Aggregate per-prompt scores into a single Metrics object.

    M3 + M4 are means across all prompts (equal weight). M1
    (rederivation_rate) doesn't apply to T9 (single-turn task per
    prompt). M2 (tokens_in/out) is summed from the per-prompt
    lengths."""
    if not fixture.prompts:
        return Metrics()
    per_prompt = [
        score_prompt(spec, outputs.get(spec.id, ""), judge=judge) for spec in fixture.prompts
    ]
    mean_m4 = sum(p.answer_correctness for p in per_prompt) / len(per_prompt)
    mean_m3 = sum(p.citation_precision for p in per_prompt) / len(per_prompt)
    tokens_in = sum(len(spec.text) for spec in fixture.prompts) // 4
    tokens_out = sum(len(outputs.get(spec.id, "")) for spec in fixture.prompts) // 4
    return Metrics(
        rederivation_rate=0.0,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        citation_precision=mean_m3,
        answer_correctness=mean_m4,
    )


# Backward-compat shim. The v0 test (tests/test_prompt_architect.py)
# imports ``score(fixture=..., output=...)`` with a flat string output.
# We adapt it to the aggregate-shape by feeding the same output for
# every prompt -- v0 has 1 prompt so the result is identical.
def score(*, fixture: Fixture, output: str, judge: Any | None = None) -> Metrics:
    """v0 stub compatibility: single-output scoring. Routes through
    score_aggregate with the same output for every prompt."""
    outputs = {spec.id: output for spec in fixture.prompts}
    return score_aggregate(fixture=fixture, outputs=outputs, judge=judge)


# --- Runner ------------------------------------------------------------


def run(
    agent_factory: Callable[[Memory], Callable[[str], str]],
    *,
    fixture: Fixture | None = None,
    judge: Any | None = None,
) -> TaskResult:
    """Drive a single agent through all of T9's prompts.

    ``agent_factory`` takes the tracking ``Memory`` and returns the
    actual agent callable; the agent closes over the memory handle
    so it can architect (or not) each prompt before answering.

    Each prompt's output is collected by id. The aggregate Metrics
    is returned in ``TaskResult.metrics``; the per-prompt outputs
    are JSON-encoded into ``TaskResult.output`` so downstream
    auditors can inspect them.
    """
    fx = fixture or load_fixture()
    memory, _queries = _make_tracking_memory(fx.corpus)
    agent = agent_factory(memory)

    outputs: dict[str, str] = {}
    for spec in fx.prompts:
        outputs[spec.id] = agent(spec.text)

    metrics = score_aggregate(fixture=fx, outputs=outputs, judge=judge)
    return TaskResult(
        task_id=TASK_ID,
        output=json.dumps(outputs),
        metrics=metrics,
    )
