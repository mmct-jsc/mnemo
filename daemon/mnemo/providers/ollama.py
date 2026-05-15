"""v3 phase 5: Ollama implementation of ``BaseProvider``.

Local, no SDK, no key -- stdlib HTTP against ``/api/chat``. Newer
models (llama3.1+, qwen2.5, mistral-nemo) use Ollama's native tool API;
older ones fall back to a ``<tool_call>{...}</tool_call>`` prompt
template we parse client-side (design S4). ``transport`` is injectable
for offline tests.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

from mnemo.providers import (
    EV_STOP,
    EV_TEXT,
    EV_TOOL_CALL,
    BaseProvider,
    ProviderError,
    ProviderEvent,
)

# Model name prefixes that support Ollama's native tool API.
_NATIVE_TOOL_PREFIXES = ("llama3.1", "llama3.2", "llama3.3", "qwen2.5", "mistral-nemo")

_FENCE_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

_FENCE_SYSTEM = (
    "\n\nWhen you need a tool, emit EXACTLY one line: "
    '<tool_call>{"name": "<tool>", "args": {...}}</tool_call> and stop.'
)


def _parse_tool_fences(text: str) -> list[dict]:
    """Extract ``<tool_call>{json}</tool_call>`` blocks (fallback path
    for models without native tool support)."""
    out: list[dict] = []
    for m in _FENCE_RE.finditer(text or ""):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "name" in obj:
            out.append({"name": obj["name"], "args": obj.get("args", {})})
    return out


def _to_ollama_messages(messages: list[dict], system: str | None) -> list[dict]:
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
                    {"function": {"name": b["name"], "arguments": b.get("input", {})}}
                    for b in content
                    if b.get("type") == "tool_use"
                ]
                msg: dict = {"role": "assistant", "content": text}
                if tcs:
                    msg["tool_calls"] = tcs
                out.append(msg)
        elif role == "tool":
            for r in content:
                out.append({"role": "tool", "content": r["content"]})
    return out


def _http_transport(base_url: str) -> Callable[[str, dict], Iterator[dict]]:
    def transport(url: str, payload: dict) -> Iterator[dict]:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:  # noqa: S310 -- localhost only
            for raw in resp:
                line = raw.decode().strip()
                if line:
                    yield json.loads(line)

    return transport


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: Callable[[str, dict], Iterator[dict]] | None = None,
    ):
        super().__init__(api_key=api_key, base_url=base_url)
        self._base = (base_url or "http://localhost:11434").rstrip("/")
        self._transport = transport or _http_transport(self._base)

    def stream(
        self,
        messages: list[dict],
        tools: list,
        *,
        model: str,
        system: str | None = None,
        max_output_tokens: int = 4096,
    ) -> Iterator[ProviderEvent]:
        native = any(model.startswith(p) for p in _NATIVE_TOOL_PREFIXES)
        sys_prompt = system
        payload: dict[str, Any] = {"model": model, "stream": True}
        if tools and native:
            payload["tools"] = [
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
        elif tools:
            sys_prompt = (system or "") + _FENCE_SYSTEM
        payload["messages"] = _to_ollama_messages(messages, sys_prompt)

        saw_tool = False
        text_buf: list[str] = []
        try:
            for line in self._transport(f"{self._base}/api/chat", payload):
                msg = line.get("message") or {}
                content = msg.get("content")
                if content:
                    text_buf.append(content)
                    yield (EV_TEXT, content)
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function", {})
                    saw_tool = True
                    yield (
                        EV_TOOL_CALL,
                        {
                            "id": fn.get("name", ""),
                            "name": fn.get("name", ""),
                            "args": fn.get("arguments", {}) or {},
                        },
                    )
                if line.get("done"):
                    break
        except Exception as exc:
            raise ProviderError(f"ollama: {exc}") from exc

        if not saw_tool and not native:
            for call in _parse_tool_fences("".join(text_buf)):
                saw_tool = True
                yield (
                    EV_TOOL_CALL,
                    {"id": call["name"], "name": call["name"], "args": call["args"]},
                )
        yield (EV_STOP, "tool_use" if saw_tool else "end_turn")
