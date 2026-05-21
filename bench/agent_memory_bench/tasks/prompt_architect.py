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

The v0.1 scorer is keyword-based against
``acceptance_criteria_keywords``. An LLM judge lands in v0.2
behind an opt-in flag the same way T1's M4 will.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent_memory_bench.runner import Memory, Metrics, Retrieval, TaskResult

FIXTURE_DIR: Path = Path(__file__).resolve().parent.parent.parent / "fixtures" / "prompt_architect"
TASK_ID: str = "prompt_architect"


@dataclass
class Fixture:
    """Parsed contents of T9's fixture directory."""

    corpus: list[dict]
    """The memory nodes the architect can pull (incl. one local_only
    candidate in v5.x; the v5.0 stub keeps everything visible)."""

    prompts: list[str]
    """The raw user prompts the architect transforms. v5.0 ships ONE
    high-confidence prompt; v5.x expands to the 30 promised in the
    design doc (10 high / 10 medium / 10 low)."""

    relevant_node_ids: set[str]
    """The corpus subset the architected output should cite."""

    acceptance_criteria_keywords: list[str]
    """The host LLM's output is scored on how many of these
    keywords it surfaces. A raw prompt without architect-pre-processing
    rarely names them; an architected prompt names them all."""


def load_fixture(fixture_dir: Path = FIXTURE_DIR) -> Fixture:
    """Parse ``corpus.jsonl`` + ``prompts.json`` + ``expected.json``."""
    corpus = [
        json.loads(line)
        for line in (fixture_dir / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    prompts = json.loads((fixture_dir / "prompts.json").read_text(encoding="utf-8"))["prompts"]
    expected = json.loads((fixture_dir / "expected.json").read_text(encoding="utf-8"))
    return Fixture(
        corpus=corpus,
        prompts=prompts,
        relevant_node_ids=set(expected["relevant_node_ids"]),
        acceptance_criteria_keywords=expected["acceptance_criteria_keywords"],
    )


def _make_tracking_memory(corpus: list[dict]) -> tuple[Memory, list[str]]:
    """Build a Memory that returns the full corpus on every query.

    Mirrors T1's tracking memory. The architect agent makes ONE
    query (to assemble the prompt); the vanilla agent makes zero
    (it just echoes the raw prompt to the host).
    """
    queries: list[str] = []

    class _TrackingMemory:
        def query(self, prompt: str, max_tokens: int = 800) -> Retrieval:
            queries.append(prompt)
            text = "\n".join(f"[{n['id']}] {n['body']}" for n in corpus)
            hit_ids = [n["id"] for n in corpus]
            return Retrieval(text=text[:max_tokens], hit_ids=hit_ids, tokens_used=min(len(text), max_tokens))

        def feedback(self, hit_id: str, direction) -> None:  # noqa: ARG002
            return None

    return _TrackingMemory(), queries


def score(*, fixture: Fixture, output: str) -> Metrics:
    """Per-task scorer for T9.

    The metric we lock is ``answer_correctness`` -- the fraction of
    acceptance-criteria keywords surfaced in the host LLM's output.
    Rederivation isn't meaningful for T9 (single-turn task);
    citation_precision is meaningful and is scored against the
    relevant_node_ids set. The architected prompt SHOULD cite all
    three MQTT-related nodes; the raw prompt cites none.
    """
    keywords = fixture.acceptance_criteria_keywords
    matched = sum(1 for kw in keywords if kw.lower() in output.lower())
    answer_correctness = matched / len(keywords) if keywords else 0.0

    cited = sum(1 for nid in fixture.relevant_node_ids if nid in output)
    citation_precision = cited / len(fixture.relevant_node_ids) if fixture.relevant_node_ids else 0.0

    return Metrics(
        rederivation_rate=0.0,
        tokens_in=sum(len(p) for p in fixture.prompts) // 4,
        tokens_out=max(len(output) // 4, 0),
        citation_precision=citation_precision,
        answer_correctness=answer_correctness,
    )


def run(
    agent_factory: Callable[[Memory], Callable[[str], str]],
    *,
    fixture: Fixture | None = None,
) -> TaskResult:
    """Drive a single agent through T9.

    ``agent_factory`` takes the tracking ``Memory`` and returns the
    actual agent callable; the agent closes over the memory handle
    so it can architect (or not) the raw prompt before answering.
    """
    fx = fixture or load_fixture()
    memory, _queries = _make_tracking_memory(fx.corpus)
    agent = agent_factory(memory)

    output = ""
    for prompt in fx.prompts:
        output = agent(prompt)

    metrics = score(fixture=fx, output=output)
    return TaskResult(task_id=TASK_ID, output=output, metrics=metrics)
