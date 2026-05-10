"""Explicit retrieve_context helper.

This is the manual entry point. The auto-patcher in patch.py composes
retrieve_context with provider-specific shims; users who want fine
control call retrieve_context themselves and inject the result
wherever they want.

Failure is additive: any error returns the empty string with a
WARNING log, never propagates an exception. The patcher relies on
this contract.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mnemo_middleware.config import daemon_url, default_budget, default_k, timeout_seconds

log = logging.getLogger(__name__)


def retrieve_context(
    prompt: str,
    *,
    project_key: str | None = None,
    k: int | None = None,
    budget_tokens: int | None = None,
    timeout: float | None = None,
) -> str:
    """Call the local mnemo daemon's /v1/query and format the hits as
    a single markdown system-message block.

    Returns ``""`` if the daemon is unreachable, slow, or returns no
    hits. The caller can drop the result into a system message
    unconditionally.

    Args:
        prompt: The query text. Usually the user's last message.
        project_key: Optional project to scope retrieval. None falls
            back to the daemon's active project (set via
            ``mnemo project use`` or the topbar widget).
        k: Number of hits to request. Defaults to MNEMO_DEFAULT_K env
            var or 5.
        budget_tokens: Token cap for the response. Defaults to
            MNEMO_DEFAULT_BUDGET env var or 800.
        timeout: Per-call timeout in seconds. Defaults to
            MNEMO_TIMEOUT env var or 2.0.
    """
    body: dict[str, Any] = {
        "prompt": prompt,
        "k": k if k is not None else default_k(),
        "budget_tokens": budget_tokens if budget_tokens is not None else default_budget(),
    }
    if project_key is not None:
        body["project_key"] = project_key

    url = daemon_url().rstrip("/") + "/v1/query"
    t = timeout if timeout is not None else timeout_seconds()
    try:
        with httpx.Client(timeout=t) as client:
            r = client.post(url, json=body)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        log.warning("mnemo daemon unreachable (%s); proceeding without injection", exc)
        return ""
    except ValueError as exc:
        log.warning("mnemo daemon response was not JSON (%s); proceeding without injection", exc)
        return ""

    return _format_hits(data)


def _format_hits(payload: dict) -> str:
    """Render the daemon's QueryOut JSON as a markdown block.

    Format mirrors the Claude Code plugin hook so model behavior is
    consistent across clients.
    """
    hits = payload.get("hits") or []
    if not hits:
        return ""

    lines: list[str] = ["## Relevant memory (mnemo)", ""]
    for h in hits:
        cite = h.get("citation") or f"[mnemo:{h.get('id', '')}]"
        node_type = h.get("type") or ""
        name = h.get("name") or ""
        desc = (h.get("description") or "").replace("\n", " ").strip()
        prefix = f"- {cite}"
        if node_type:
            prefix += f" [{node_type}]"
        if name:
            prefix += f" {name}"
        if desc:
            prefix += f": {desc}"
        lines.append(prefix)
        body = h.get("body")
        if body:
            snippet = body if len(body) <= 400 else body[:400].rstrip() + "..."
            for line in snippet.splitlines():
                lines.append(f"  {line}")
    intent = ", ".join(payload.get("intent_tags") or [])
    tokens = payload.get("tokens_used", 0)
    lines.append("")
    lines.append(f"intent: {intent or 'none'} | tokens used: {tokens} | k: {len(hits)}")
    return "\n".join(lines)
