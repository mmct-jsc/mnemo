"""v3 phase 2: Anthropic implementation of ``BaseProvider``.

Streaming via ``client.messages.stream(...)`` -- text deltas are
forwarded live as ``('text_delta', str)``; ``get_final_message()`` then
yields any ``tool_use`` blocks as ``('tool_call', ...)`` and the
``stop_reason`` as ``('stop', ...)``.

Prompt caching (the claude-api skill mandate, design S2 "fixed system
prompt + tool definitions"): the request render order is tools ->
system -> messages, so a single ``cache_control: ephemeral`` breakpoint
on the system block caches the whole stable tools+system prefix across
every iteration of the multi-turn agent loop. The system prompt is
kept frozen by the loop (no timestamps / UUIDs) so the prefix is
byte-stable and the cache actually hits.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import anthropic

from mnemo.providers import (
    EV_STOP,
    EV_TEXT,
    EV_TOOL_CALL,
    BaseProvider,
    ProviderError,
    ProviderEvent,
    _usage_event,
)


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Normalised mnemo messages -> Anthropic Messages API shape."""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "user":
            out.append({"role": "user", "content": content})
        elif role == "assistant":
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
            else:
                blocks: list[dict] = []
                for b in content:
                    if b.get("type") == "text":
                        blocks.append({"type": "text", "text": b["text"]})
                    elif b.get("type") == "tool_use":
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": b["id"],
                                "name": b["name"],
                                "input": b.get("input", {}),
                            }
                        )
                out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            # tool results go back as a user turn of tool_result blocks
            results = []
            for r in content:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": r["tool_use_id"],
                        "content": r["content"],
                        "is_error": bool(r.get("is_error")),
                    }
                )
            out.append({"role": "user", "content": results})
    return out


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any | None = None,
    ):
        super().__init__(api_key=api_key, base_url=base_url)
        if client is not None:
            self._client = client  # injected (tests / custom transport)
        elif base_url:
            self._client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
        else:
            self._client = anthropic.Anthropic(api_key=api_key)

    def stream(
        self,
        messages: list[dict],
        tools: list,
        *,
        model: str,
        system: str | None = None,
        max_output_tokens: int = 4096,
    ) -> Iterator[ProviderEvent]:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_output_tokens,
            "messages": _to_anthropic_messages(messages),
        }
        if tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]
        if system:
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        try:
            with self._client.messages.stream(**kwargs) as stream:
                for event in stream:
                    if (
                        getattr(event, "type", None) == "content_block_delta"
                        and getattr(event.delta, "type", None) == "text_delta"
                    ):
                        yield (EV_TEXT, event.delta.text)
                final = stream.get_final_message()
        except anthropic.APIError as exc:  # typed SDK exception hierarchy
            msg = getattr(exc, "message", None) or str(exc)
            raise ProviderError(f"anthropic: {msg}") from exc
        except Exception as exc:  # network / unexpected -> recoverable
            raise ProviderError(f"anthropic: {exc}") from exc

        for block in final.content:
            if getattr(block, "type", None) == "tool_use":
                yield (
                    EV_TOOL_CALL,
                    {"id": block.id, "name": block.name, "args": block.input},
                )
        usage = getattr(final, "usage", None)
        if usage is not None:
            ev = _usage_event(
                getattr(usage, "input_tokens", None),
                getattr(usage, "output_tokens", None),
                getattr(usage, "cache_read_input_tokens", 0),
            )
            if ev is not None:
                yield ev
        yield (EV_STOP, final.stop_reason or "end_turn")
