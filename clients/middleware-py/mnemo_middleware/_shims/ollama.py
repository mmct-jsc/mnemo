"""Ollama shim.

The ``ollama`` Python client exposes ``client.chat(model, messages)``
for chat completions. Same prepend-system-message pattern as OpenAI.
"""

from __future__ import annotations

import logging
from typing import Any

from mnemo_middleware import _patcher as _patch_mod
from mnemo_middleware.retrieve import retrieve_context

log = logging.getLogger(__name__)

_NAME = "ollama"


class _Shim:
    name = _NAME

    @staticmethod
    def matches(client: object) -> bool:
        mod = type(client).__module__ or ""
        if not mod.startswith("ollama"):
            return False
        cls_name = type(client).__name__
        return cls_name in ("Client", "AsyncClient")

    @staticmethod
    def install(client: object, *, mode: str) -> _patch_mod.PatchState:
        if not hasattr(client, "chat"):
            raise TypeError("Ollama client missing chat()")
        original = client.chat
        state = _patch_mod.PatchState(mode=mode, original_create=original, shim_name=_NAME)

        def patched_chat(*args: Any, **kwargs: Any) -> Any:
            messages = kwargs.get("messages")
            if not isinstance(messages, list):
                return original(*args, **kwargs)
            try:
                if _patch_mod.should_inject(state, messages):
                    query = _patch_mod.get_query_text(messages)
                    block = retrieve_context(query) if query.strip() else ""
                    if block:
                        new_messages = _inject_system(messages, block)
                        kwargs = {**kwargs, "messages": new_messages}
                        # Record the ORIGINAL messages so auto-mode
                        # hash compares user-vs-user on next call.
                        _patch_mod.remember_call(state, messages, block)
                elif state.last_block:
                    kwargs = {**kwargs, "messages": _inject_system(messages, state.last_block)}
            except Exception as exc:  # noqa: BLE001
                log.warning("mnemo ollama shim error (%s); proceeding without injection", exc)
            return original(*args, **kwargs)

        client.chat = patched_chat  # type: ignore[assignment]
        return state

    @staticmethod
    def uninstall(client: object, state: _patch_mod.PatchState) -> None:
        client.chat = state.original_create  # type: ignore[assignment]


def _inject_system(messages: list[dict[str, Any]], block: str) -> list[dict[str, Any]]:
    sys_msg = {"role": "system", "content": _SYS_PREFIX + block}
    if messages and messages[0].get("role") == "system":
        existing = messages[0].get("content") or ""
        if isinstance(existing, str) and existing:
            sys_msg["content"] = _SYS_PREFIX + block + "\n\n---\n\n" + existing
        return [sys_msg, *messages[1:]]
    return [sys_msg, *messages]


_SYS_PREFIX = "[Project memory -- cite as shown]\n\n"


_patch_mod.register_shim(_NAME, _Shim)
