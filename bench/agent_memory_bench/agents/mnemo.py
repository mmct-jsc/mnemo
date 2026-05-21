"""Reference memory-equipped baselines for mnemo.

Two variants:

- :func:`make_mnemo_mock_agent` -- deterministic mock for CI. Uses
  the tracking memory the task module provides; queries on turn 1,
  reuses cached retrieval for follow-up turns whose prompt contains
  a deictic ("that", "this", "it", "those", "these"). This is the
  simplest "smart" agent that beats the vanilla baseline on M1
  re-derivation; it deliberately is NOT a perfect agent so the
  harness's per-metric scoring stays exercisable as the skeleton
  evolves.

- :func:`make_mnemo_http_agent` -- HTTP adapter against a live mnemo
  daemon's ``POST /v1/query``. Gated on ``MNEMO_DAEMON_URL`` env
  var. Tests that depend on this variant skip when the env var is
  unset so CI stays portable.

External implementers can copy this module as a starting point for
their own ``Memory``-protocol agents.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable

from agent_memory_bench.runner import Memory, Retrieval

DEICTICS: frozenset[str] = frozenset({"that", "this", "it", "those", "these"})
_WORD_RE = re.compile(r"[a-z]+")


def _contains_deictic(prompt: str) -> bool:
    """Word-boundary deictic check. Punctuation-safe: 'that?' matches
    'that'. Skeleton-quality; v0.1 may swap in dependency-parse if
    false positives matter."""
    return any(w in DEICTICS for w in _WORD_RE.findall(prompt.lower()))


def make_mnemo_mock_agent(memory: Memory) -> Callable[[str], str]:
    """Deterministic CI agent: query on turn 1, cache; reuse on
    follow-up turns whose prompt contains a deictic.

    Beats vanilla on M1 by construction (only re-derives when the
    prompt is a topic shift, not a follow-up). The cache is closure-
    local so each ``run_task`` call gets a fresh agent.
    """

    cached: dict[str, Retrieval] = {}

    def agent(prompt: str) -> str:
        if cached and _contains_deictic(prompt):
            ret = cached["first"]
            snippet = ret.text[:120].replace("\n", " ")
            return f"[mnemo] follow-up using cached retrieval: {snippet}"
        ret = memory.query(prompt)
        if "first" not in cached:
            cached["first"] = ret
        snippet = ret.text[:120].replace("\n", " ")
        return f"[mnemo] fresh retrieval: {snippet}"

    return agent


def get_daemon_url() -> str | None:
    """Read ``MNEMO_DAEMON_URL`` for the HTTP agent / tests. Returns
    None if unset. Tests use this to ``pytest.skip`` portably."""
    return os.environ.get("MNEMO_DAEMON_URL")


def make_mnemo_http_agent(daemon_url: str | None = None) -> Callable[[str], str]:
    """HTTP adapter against ``POST /v1/query`` on a live mnemo
    daemon. Same caching heuristic as the mock; the differentiator
    is that the retrieval text actually comes from the daemon's
    Graph-RAG store rather than the in-test tracking memory.

    Lazy-imports ``httpx`` so the skeleton package stays
    dependency-free for external implementers. Install the
    ``mnemo`` extra to use this:

        uv sync --extra mnemo
    """
    url = daemon_url or get_daemon_url()
    if not url:
        raise RuntimeError(
            "MNEMO_DAEMON_URL not set; pass daemon_url= or set the env var. "
            "Tests using this agent should pytest.skip when the env var is unset."
        )

    import httpx  # lazy: optional dep

    cache: dict[str, str] = {}
    client = httpx.Client(base_url=url, timeout=10.0)

    def agent(prompt: str) -> str:
        if cache and _contains_deictic(prompt):
            return f"[mnemo-http] follow-up using cached retrieval: {cache['first'][:120]}"
        resp = client.post("/v1/query", json={"prompt": prompt, "max_tokens": 800})
        resp.raise_for_status()
        data = resp.json()
        # Daemon's v1/query response shape: {budget: "...", hits: [...], ...}.
        # We only need the budget block for the cache.
        text = data.get("budget") if isinstance(data.get("budget"), str) else str(data)
        if "first" not in cache:
            cache["first"] = text
        return f"[mnemo-http] fresh retrieval: {text[:120]}"

    return agent
