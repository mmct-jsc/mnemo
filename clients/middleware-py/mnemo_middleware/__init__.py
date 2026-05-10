"""mnemo-middleware: drop mnemo retrieval into any LLM SDK call.

Two surfaces:

1. ``retrieve_context(prompt, ...)`` — explicit helper. Returns a
   markdown string with budget-capped citations from the local mnemo
   daemon. Caller decides where to inject it.

2. ``patch(client, mode='auto')`` — opt-in monkey-patcher. Wraps the
   chat-completion method on a known SDK client (OpenAI, Anthropic,
   Google, Ollama) so every call auto-injects retrieval as a system
   message. Three modes:
     - ``auto`` (default): inject when conversation looks new (turn 1,
       new prefix, or topic shift). Reuses last block otherwise so
       multi-turn chats don't re-pay the retrieval token cost.
     - ``once``: inject on the first call only. Persistent agents.
     - ``every``: inject every call. One-shot evaluators.

Failure mode is **always additive**: if the daemon is down, slow, or
unreachable, the LLM call proceeds without injection (logged at
WARNING). The middleware never blocks the model call indefinitely.
"""

from __future__ import annotations

from mnemo_middleware.errors import UnsupportedClient
from mnemo_middleware.retrieve import retrieve_context

__all__ = [
    "UnsupportedClient",
    "patch",
    "retrieve_context",
    "unpatch",
]
__version__ = "1.1.0"


def patch(client: object, *, mode: str = "auto") -> object:
    """Monkey-patch a known SDK client so chat completions auto-inject
    mnemo retrieval. Returns the same client for chaining.

    Lazy-imports the patcher so the package's import-time cost stays
    tiny (just retrieve_context); shims load only when you patch.
    """
    from mnemo_middleware.patch import patch as _patch

    return _patch(client, mode=mode)


def unpatch(client: object) -> object:
    """Reverse a previous ``patch(client)``. Idempotent."""
    from mnemo_middleware.patch import unpatch as _unpatch

    return _unpatch(client)
