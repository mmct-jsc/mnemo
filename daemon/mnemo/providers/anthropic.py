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
    EV_COMPACT,
    EV_STOP,
    EV_TEXT,
    EV_TOOL_CALL,
    BaseProvider,
    ProviderDescriptor,
    ProviderError,
    ProviderEvent,
    _usage_event,
    register_provider,
)

# Native server-side compaction (v3.1, claude-api skill). Capable
# models only (Opus 4.7/4.6, Sonnet 4.6) -- the loop gates on
# compaction.supports_native_compaction before passing compact=True.
_COMPACT_BETA = "compact-2026-01-12"
_CONTEXT_MANAGEMENT = {"edits": [{"type": "compact_20260112"}]}


def _block_to_dict(block: Any) -> dict:
    """Serialize one final-message content block to a plain dict so the
    loop can persist + replay it verbatim (compaction blocks MUST be
    preserved -- the claude-api critical rule)."""
    bt = getattr(block, "type", None)
    if bt == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if bt == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", {}) or {},
        }
    # compaction / unknown future blocks: keep type + id opaquely.
    out: dict = {"type": bt}
    if getattr(block, "id", None) is not None:
        out["id"] = block.id
    return out


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
        compact: bool = False,
    ) -> Iterator[ProviderEvent]:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_output_tokens,
            "messages": _to_anthropic_messages(messages),
        }
        if compact:
            kwargs["betas"] = [_COMPACT_BETA]
            kwargs["context_management"] = _CONTEXT_MANAGEMENT
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
        streamer = self._client.beta.messages.stream if compact else self._client.messages.stream
        try:
            with streamer(**kwargs) as stream:
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
        if compact:
            # Preserve the FULL content (compaction blocks included) so
            # the loop can replay it verbatim next turn.
            yield (EV_COMPACT, [_block_to_dict(b) for b in final.content])
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


# C2 (v4.1): self-register. Adding a provider = this one call + the
# stream() class above. get_provider / DEFAULT_MODELS / keys.ENV_VAR /
# Config.providers / compaction.NATIVE_COMPACTION all DERIVE from this.
register_provider(
    ProviderDescriptor(
        name="anthropic",
        display_name="Anthropic (Claude)",
        impl_class=AnthropicProvider,
        env_var="ANTHROPIC_API_KEY",
        requires_key=True,
        default_model="claude-sonnet-4-5-20250929",  # UNCHANGED (DEFAULT_MODELS)
        known_models=(
            "claude-sonnet-4-5-20250929",
            "claude-opus-4-7",
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
        ),
        base_url=None,
        native_compaction_models=frozenset(
            {"claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"}
        ),
    )
)
