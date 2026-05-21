"""Vanilla no-memory baseline.

The agent that re-derives on EVERY turn -- never uses prior turn's
retrieval, never caches. Calls ``memory.query`` once per turn so the
tracking memory in the task module records the re-derivations.

This is the worst-case baseline every typed-Graph-RAG agent must
beat. If your agent doesn't beat ``agent_vanilla`` on
``rederivation_rate``, your memory layer isn't doing anything.
"""

from __future__ import annotations

from collections.abc import Callable

from agent_memory_bench.runner import Memory


def make_vanilla_agent(memory: Memory) -> Callable[[str], str]:
    """Factory: returns an agent that queries ``memory`` on every
    prompt, then emits a placeholder answer.

    The placeholder answer is intentionally NOT a real model call --
    the v0 skeleton measures memory behaviour, not generation
    quality. ``Metrics.answer_correctness`` is rule-based-scored
    via keyword match in :func:`tasks.answer_follow_up.score`.
    """

    def agent(prompt: str) -> str:
        retrieval = memory.query(prompt)
        snippet = retrieval.text[:120].replace("\n", " ")
        return f"[vanilla] re-derived answer (no cache) for {prompt!r}: {snippet}"

    return agent
