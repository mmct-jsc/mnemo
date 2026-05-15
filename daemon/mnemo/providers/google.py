"""v3 phase 5: Google (Gemini) implementation of ``BaseProvider``.

Translates ``generate_content_stream`` chunks + function calls into the
shared contract. Extraction is defensive (getattr) because the
google-genai surface is version-sensitive; the offline test injects a
fake client, and real-SDK wire shape is a phase-12 live smoke.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from mnemo.providers import (
    EV_STOP,
    EV_TEXT,
    EV_TOOL_CALL,
    BaseProvider,
    ProviderError,
    ProviderEvent,
    _usage_event,
)


def _to_google_contents(messages: list[dict]) -> list[dict]:
    contents: list[dict] = []
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role == "user":
            contents.append({"role": "user", "parts": [{"text": content}]})
        elif role == "assistant":
            parts: list[dict] = []
            if isinstance(content, str):
                parts.append({"text": content})
            else:
                for b in content:
                    if b.get("type") == "text":
                        parts.append({"text": b["text"]})
                    elif b.get("type") == "tool_use":
                        parts.append(
                            {
                                "function_call": {
                                    "name": b["name"],
                                    "args": b.get("input", {}),
                                }
                            }
                        )
            contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "name": r.get("tool_use_id", "tool"),
                                "response": {"content": r["content"]},
                            }
                        }
                        for r in content
                    ],
                }
            )
    return contents


class GoogleProvider(BaseProvider):
    name = "google"

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
            from google import genai

            self._client = genai.Client(api_key=api_key)

    def stream(
        self,
        messages: list[dict],
        tools: list,
        *,
        model: str,
        system: str | None = None,
        max_output_tokens: int = 4096,
    ) -> Iterator[ProviderEvent]:
        config: dict[str, Any] = {"max_output_tokens": max_output_tokens}
        if system:
            config["system_instruction"] = system
        if tools:
            config["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        }
                        for t in tools
                    ]
                }
            ]
        saw_tool = False
        usage_meta = None
        try:
            for chunk in self._client.models.generate_content_stream(
                model=model,
                contents=_to_google_contents(messages),
                config=config,
            ):
                um = getattr(chunk, "usage_metadata", None)
                if um is not None:
                    usage_meta = um
                txt = getattr(chunk, "text", None)
                if txt:
                    yield (EV_TEXT, txt)
                for cand in getattr(chunk, "candidates", None) or []:
                    content = getattr(cand, "content", None)
                    for part in getattr(content, "parts", None) or []:
                        fc = getattr(part, "function_call", None)
                        if fc is not None:
                            saw_tool = True
                            yield (
                                EV_TOOL_CALL,
                                {
                                    "id": getattr(fc, "name", "") or "",
                                    "name": getattr(fc, "name", "") or "",
                                    "args": dict(getattr(fc, "args", {}) or {}),
                                },
                            )
        except Exception as exc:
            raise ProviderError(f"google: {exc}") from exc
        if usage_meta is not None:
            ev = _usage_event(
                getattr(usage_meta, "prompt_token_count", None),
                getattr(usage_meta, "candidates_token_count", None),
                getattr(usage_meta, "cached_content_token_count", 0),
            )
            if ev is not None:
                yield ev
        yield (EV_STOP, "tool_use" if saw_tool else "end_turn")
