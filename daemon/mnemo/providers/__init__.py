"""v3: provider abstraction (design S2).

One ``BaseProvider.stream()`` shape, four implementations. The agent
loop (``mnemo.chat``) is provider-agnostic: it only sees the tagged
events below, never an SDK type.

Event protocol (design S2) -- ``stream()`` yields tuples:

  * ``('text_delta', str)``                -- assistant text chunk
  * ``('tool_call', {id, name, args})``    -- the model wants a tool
  * ``('usage', {input_tokens, output_tokens,
        cache_read_input_tokens})``        -- v3.1: per-turn token
        usage, yielded AT MOST ONCE directly before ('stop', ...).
        Omitted when the provider/transport didn't surface counts (a
        mocked client without usage stays a pure text/tool/stop stream).
  * ``('stop', reason)``                   -- 'end_turn' | 'tool_use'
                                              | 'max_tokens' | ...

Normalised message shape the loop builds + persists, and each provider
translates to its own API:

  * ``{'role': 'user', 'content': '<text>'}``
  * ``{'role': 'assistant', 'content': [ {'type':'text','text':..},
        {'type':'tool_use','id':..,'name':..,'input':{..}} ]}``
  * ``{'role': 'tool', 'content': [ {'tool_use_id':..,
        'content':'<json str>', 'is_error': bool} ]}``

Phase 2 ships Anthropic only; OpenAI / Google / Ollama land in phase 5
behind the same contract (``get_provider`` raises until then).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

EV_TEXT = "text_delta"
EV_TOOL_CALL = "tool_call"
EV_USAGE = "usage"
# v3.1 native compaction: the FULL final content (compaction blocks
# included) as a list of plain dicts, so the loop can persist + replay
# it verbatim (the claude-api "preserve response.content" rule). Only
# the Anthropic provider in compact mode yields this.
EV_COMPACT = "compaction"
EV_STOP = "stop"


def _usage_event(
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_input_tokens: int | None = 0,
) -> tuple[str, dict] | None:
    """Build the ``('usage', {...})`` event, or None when the provider
    surfaced no usable counts (so the loop stays a text/tool/stop
    stream -- design S3.2). Counts coerce to non-negative ints."""
    if input_tokens is None and output_tokens is None:
        return None

    def _n(v: object) -> int:
        try:
            return max(0, int(v))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    return (
        EV_USAGE,
        {
            "input_tokens": _n(input_tokens),
            "output_tokens": _n(output_tokens),
            "cache_read_input_tokens": _n(cache_read_input_tokens),
        },
    )


ProviderEvent = tuple[str, Any]

# Default model per provider (design S4). User-overridable per
# conversation + in Settings (phase 7).
DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-5-20250929",
    "openai": "gpt-4o-mini",
    "google": "gemini-2.5-flash",
    "ollama": "llama3.1:8b",
}


class ProviderError(RuntimeError):
    """A provider call failed. The agent loop turns this into an
    ``error`` SSE event with conversation state preserved (design S4)."""


class BaseProvider:
    """Subclasses set ``name`` and implement ``stream``."""

    name: str = "base"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None):
        self._api_key = api_key
        self._base_url = base_url

    def stream(
        self,
        messages: list[dict],
        tools: list,
        *,
        model: str,
        system: str | None = None,
        max_output_tokens: int = 4096,
        compact: bool = False,
    ) -> Iterator[ProviderEvent]:
        raise NotImplementedError("provider must implement stream()")


def get_provider(
    name: str, *, api_key: str | None = None, base_url: str | None = None
) -> BaseProvider:
    """Construct a provider by name. Phase 2: anthropic only."""
    if name == "anthropic":
        from mnemo.providers.anthropic import AnthropicProvider

        return AnthropicProvider(api_key=api_key, base_url=base_url)
    if name == "openai":
        from mnemo.providers.openai import OpenAIProvider

        return OpenAIProvider(api_key=api_key, base_url=base_url)
    if name == "google":
        from mnemo.providers.google import GoogleProvider

        return GoogleProvider(api_key=api_key, base_url=base_url)
    if name == "ollama":
        from mnemo.providers.ollama import OllamaProvider

        return OllamaProvider(api_key=api_key, base_url=base_url)
    raise ValueError(f"unknown provider: {name!r}")
