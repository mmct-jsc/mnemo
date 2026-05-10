"""OpenAI client shim.

Detects ``openai.OpenAI`` (and ``AsyncOpenAI``) by module path. Wraps
``client.chat.completions.create`` so retrieval is prepended as a
system message before the upstream call.

Prompt-caching: when the mnemo block is large enough, we place it at
the very top of the messages array so OpenAI's automatic prefix
caching can reuse it across calls.
"""

from __future__ import annotations

import logging
from typing import Any

from mnemo_middleware import _patcher as _patch_mod
from mnemo_middleware.retrieve import retrieve_context

log = logging.getLogger(__name__)

_NAME = "openai"


class _Shim:
    name = _NAME

    @staticmethod
    def matches(client: object) -> bool:
        mod = type(client).__module__ or ""
        # openai-python v1.x: openai._client.OpenAI / AsyncOpenAI etc.
        if not mod.startswith("openai"):
            return False
        cls_name = type(client).__name__
        return cls_name in ("OpenAI", "AsyncOpenAI", "AzureOpenAI", "AsyncAzureOpenAI")

    @staticmethod
    def install(client: object, *, mode: str) -> _patch_mod.PatchState:
        chat = getattr(client, "chat", None)
        completions = getattr(chat, "completions", None) if chat is not None else None
        if completions is None or not hasattr(completions, "create"):
            raise TypeError("OpenAI client missing chat.completions.create")
        original = completions.create
        state = _patch_mod.PatchState(mode=mode, original_create=original, shim_name=_NAME)

        def patched_create(*args: Any, **kwargs: Any) -> Any:
            messages = kwargs.get("messages")
            if not isinstance(messages, list):
                # Unknown shape -- pass through.
                return original(*args, **kwargs)
            try:
                if _patch_mod.should_inject(state, messages):
                    query = _patch_mod.get_query_text(messages)
                    block = retrieve_context(query) if query.strip() else ""
                    if block:
                        new_messages = _inject_system(messages, block)
                        kwargs = {**kwargs, "messages": new_messages}
                        # Record the ORIGINAL user-side messages so the
                        # auto-mode hash compares user-vs-user, not
                        # patched-vs-user on the next call.
                        _patch_mod.remember_call(state, messages, block)
                elif state.last_block:
                    # Reuse the last block so the conversation stays
                    # consistent without paying the retrieval cost.
                    kwargs = {**kwargs, "messages": _inject_system(messages, state.last_block)}
            except Exception as exc:  # noqa: BLE001
                log.warning("mnemo openai shim error (%s); proceeding without injection", exc)
            return original(*args, **kwargs)

        completions.create = patched_create  # type: ignore[assignment]
        return state

    @staticmethod
    def uninstall(client: object, state: _patch_mod.PatchState) -> None:
        chat = getattr(client, "chat", None)
        completions = getattr(chat, "completions", None) if chat is not None else None
        if completions is not None:
            completions.create = state.original_create  # type: ignore[assignment]


def _inject_system(messages: list[dict[str, Any]], block: str) -> list[dict[str, Any]]:
    """Prepend or merge the mnemo block as the first system message.

    If the messages array already starts with a system message, we
    replace it with our block + original system content (so the user's
    own system instructions still apply).
    """
    sys_msg = {"role": "system", "content": _SYS_PREFIX + block}
    if messages and messages[0].get("role") == "system":
        existing = messages[0].get("content") or ""
        if isinstance(existing, str) and existing:
            sys_msg["content"] = _SYS_PREFIX + block + "\n\n---\n\n" + existing
        return [sys_msg, *messages[1:]]
    return [sys_msg, *messages]


_SYS_PREFIX = "[Project memory -- cite as shown]\n\n"


_patch_mod.register_shim(_NAME, _Shim)
