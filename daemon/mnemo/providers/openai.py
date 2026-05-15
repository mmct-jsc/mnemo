"""v3 phase 5: OpenAI implementation of ``BaseProvider``.

Translates Chat Completions streaming + function-calling into the
shared (text_delta | tool_call | stop) contract. Client injectable for
offline tests.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from mnemo.providers import (
    EV_STOP,
    EV_TEXT,
    EV_TOOL_CALL,
    BaseProvider,
    ProviderError,
    ProviderEvent,
)

_STOP_MAP = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}


def _to_openai_messages(messages: list[dict], system: str | None) -> list[dict]:
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role == "user":
            out.append({"role": "user", "content": content})
        elif role == "assistant":
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
            else:
                text = "".join(b["text"] for b in content if b.get("type") == "text")
                tcs = [
                    {
                        "id": b["id"],
                        "type": "function",
                        "function": {
                            "name": b["name"],
                            "arguments": json.dumps(b.get("input", {})),
                        },
                    }
                    for b in content
                    if b.get("type") == "tool_use"
                ]
                msg: dict = {"role": "assistant", "content": text or None}
                if tcs:
                    msg["tool_calls"] = tcs
                out.append(msg)
        elif role == "tool":
            for r in content:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": r["tool_use_id"],
                        "content": r["content"],
                    }
                )
    return out


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any | None = None,
    ):
        super().__init__(api_key=api_key, base_url=base_url)
        if client is not None:
            self._client = client
        else:
            import openai

            self._client = (
                openai.OpenAI(api_key=api_key, base_url=base_url)
                if base_url
                else openai.OpenAI(api_key=api_key)
            )

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
            "messages": _to_openai_messages(messages, system),
            "max_tokens": max_output_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        acc: dict[int, dict] = {}
        stop = "end_turn"
        try:
            for chunk in self._client.chat.completions.create(**kwargs):
                choice = chunk.choices[0]
                delta = choice.delta
                if getattr(delta, "content", None):
                    yield (EV_TEXT, delta.content)
                for tc in getattr(delta, "tool_calls", None) or []:
                    slot = acc.setdefault(tc.index, {"id": None, "name": None, "args": ""})
                    if getattr(tc, "id", None):
                        slot["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn and getattr(fn, "name", None):
                        slot["name"] = fn.name
                    if fn and getattr(fn, "arguments", None):
                        slot["args"] += fn.arguments
                if getattr(choice, "finish_reason", None):
                    stop = _STOP_MAP.get(choice.finish_reason, "end_turn")
        except Exception as exc:
            raise ProviderError(f"openai: {exc}") from exc

        for slot in acc.values():
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                args = {}
            yield (
                EV_TOOL_CALL,
                {"id": slot["id"] or "", "name": slot["name"] or "", "args": args},
            )
        yield (EV_STOP, stop)
