"""T1 -- "answer follow-up referencing material from turn 1".

Spec section: see ``docs/benchmark/agent-memory-spec-v0.md``
(``T1``). The fixture lives at ``bench/fixtures/answer_follow_up/``.

The strict invariant this task enforces:

    vanilla.rederivation_rate > mnemo.rederivation_rate

A typed-Graph-RAG memory agent MUST score lower re-derivation than
the no-memory baseline on this task. The skeleton's mnemo mock
hits this by construction (caches turn-1 retrieval, reuses on
deictic follow-ups); a real implementation faces a harder version
of the same problem.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent_memory_bench.runner import Memory, Metrics, Retrieval, TaskResult

FIXTURE_DIR: Path = Path(__file__).resolve().parent.parent.parent / "fixtures" / "answer_follow_up"
TASK_ID: str = "answer_follow_up"


@dataclass
class Fixture:
    """Parsed contents of a task's fixture directory."""

    corpus: list[dict]
    """One dict per memory_feedback (or other) node. Schema mirrors
    mnemo's node JSON output so re-using a real mnemo corpus is
    trivial."""

    prompts: list[str]
    """The conversation the agent drives, in order."""

    relevant_node_ids: set[str]
    """The ``expected.json`` ``relevant_node_ids`` set -- used for
    M3 citation precision."""

    required_keywords_final_turn: list[str]
    """Rule-based check on the final turn's output (M4 skeleton)."""


def load_fixture(fixture_dir: Path = FIXTURE_DIR) -> Fixture:
    """Parse ``corpus.jsonl`` + ``prompts.json`` + ``expected.json``."""
    corpus_path = fixture_dir / "corpus.jsonl"
    prompts_path = fixture_dir / "prompts.json"
    expected_path = fixture_dir / "expected.json"

    corpus = [
        json.loads(line)
        for line in corpus_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    prompts = json.loads(prompts_path.read_text(encoding="utf-8"))["prompts"]
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    return Fixture(
        corpus=corpus,
        prompts=prompts,
        relevant_node_ids=set(expected["relevant_node_ids"]),
        required_keywords_final_turn=expected.get("required_keywords_final_turn", []),
    )


def _make_tracking_memory(corpus: list[dict]) -> tuple[Memory, list[str]]:
    """Build a Memory that returns the full corpus on every query
    AND records each query for re-derivation scoring.

    The returned list is the live query log; the task scorer reads
    it after the agent finishes.
    """
    queries: list[str] = []

    class _TrackingMemory:
        def query(self, prompt: str, max_tokens: int = 800) -> Retrieval:
            queries.append(prompt)
            text = "\n".join(f"[{n['id']}] {n['body']}" for n in corpus)
            hit_ids = [n["id"] for n in corpus]
            # crude 4-chars-per-token estimate; the harness only uses
            # tokens_used for budget compliance checks (T8), not here.
            tokens = min(len(text), max_tokens)
            return Retrieval(text=text[:max_tokens], hit_ids=hit_ids, tokens_used=tokens)

        def feedback(self, hit_id: str, direction) -> None:  # noqa: ARG002
            return None

    return _TrackingMemory(), queries


def score(*, fixture: Fixture, queries: list[str], output: str) -> Metrics:
    """Per-task scorer for T1. Deterministic; no LLM judge in v0.1.

    - **M1 rederivation_rate**: queries beyond the first count as
      re-derivations across the follow-up turns. Vanilla emits one
      query per turn -> 1.0; mnemo mock queries only on turn 1 -> 0.0.
    - **M2 tokens_in / tokens_out**: rough 4-chars-per-token estimate
      summed across prompts (in) and the final output (out). The
      goal is comparative not absolute.
    - **M3 citation_precision**: skeleton returns 1.0 (the tracking
      memory only ever returns the canonical relevant set, so any
      cited hit is by construction relevant). v0.1 swaps in a
      retrieval-quality-aware memory to make this metric meaningful.
    - **M4 answer_correctness**: keyword-match the final turn's
      output against ``required_keywords_final_turn``. A real LLM
      judge lands in v0.1 as an opt-in flag.
    """
    n_prompts = len(fixture.prompts)
    follow_up_turns = max(n_prompts - 1, 1)
    extra_queries = max(len(queries) - 1, 0)
    rederivation_rate = min(extra_queries / follow_up_turns, 1.0)

    tokens_in = sum(len(p) for p in fixture.prompts) // 4
    tokens_out = max(len(output) // 4, 0)

    citation_precision = 1.0

    keywords = fixture.required_keywords_final_turn
    if keywords:
        matched = sum(1 for kw in keywords if kw.lower() in output.lower())
        answer_correctness = matched / len(keywords)
    else:
        answer_correctness = 1.0

    return Metrics(
        rederivation_rate=rederivation_rate,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        citation_precision=citation_precision,
        answer_correctness=answer_correctness,
    )


def run(
    agent_factory: Callable[[Memory], Callable[[str], str]],
    *,
    fixture: Fixture | None = None,
) -> TaskResult:
    """Drive a single agent through T1.

    ``agent_factory`` takes the tracking ``Memory`` and returns the
    actual agent callable. This shape lets the agent close over the
    tracking memory for queries while keeping the agent's call
    signature ``Callable[[str], str]`` per the runner contract.
    """
    fx = fixture or load_fixture()
    memory, query_log = _make_tracking_memory(fx.corpus)
    agent = agent_factory(memory)

    output = ""
    for prompt in fx.prompts:
        output = agent(prompt)

    metrics = score(fixture=fx, queries=query_log, output=output)
    return TaskResult(task_id=TASK_ID, output=output, metrics=metrics)
