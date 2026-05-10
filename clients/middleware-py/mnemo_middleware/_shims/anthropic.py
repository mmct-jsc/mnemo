"""Anthropic client shim.

Detects ``anthropic.Anthropic`` (and ``AsyncAnthropic``). Wraps
``client.messages.create``. Anthropic's API takes ``system`` as a
top-level kwarg (not a message in the array), so we merge there.

Prompt caching: when the mnemo block is >= 1024 tokens (rough char
heuristic), we attach ``cache_control: ephemeral`` so subsequent
calls within 5 minutes get the 90% cache discount.
"""

from __future__ import annotations

import logging
from typing import Any

from mnemo_middleware import _patcher as _patch_mod
from mnemo_middleware.retrieve import retrieve_context

log = logging.getLogger(__name__)

_NAME = "anthropic"
# Rough proxy: 1 token ~= 4 chars for English text.
_MIN_CACHE_CHARS = 1024 * 4


class _Shim:
    name = _NAME

    @staticmethod
    def matches(client: object) -> bool:
        mod = type(client).__module__ or ""
        if not mod.startswith("anthropic"):
            return False
        cls_name = type(client).__name__
        return cls_name in ("Anthropic", "AsyncAnthropic")

    @staticmethod
    def install(client: object, *, mode: str) -> _patch_mod.PatchState:
        messages_attr = getattr(client, "messages", None)
        if messages_attr is None or not hasattr(messages_attr, "create"):
            raise TypeError("Anthropic client missing messages.create")
        original = messages_attr.create
        state = _patch_mod.PatchState(mode=mode, original_create=original, shim_name=_NAME)

        def patched_create(*args: Any, **kwargs: Any) -> Any:
            messages = kwargs.get("messages")
            if not isinstance(messages, list):
                return original(*args, **kwargs)
            try:
                if _patch_mod.should_inject(state, messages):
                    query = _patch_mod.get_query_text(messages)
                    block = retrieve_context(query) if query.strip() else ""
                    if block:
                        kwargs = _inject_system_anthropic(kwargs, block)
                        _patch_mod.remember_call(state, messages, block)
                elif state.last_block:
                    kwargs = _inject_system_anthropic(kwargs, state.last_block)
            except Exception as exc:  # noqa: BLE001
                log.warning("mnemo anthropic shim error (%s); proceeding without injection", exc)
            return original(*args, **kwargs)

        messages_attr.create = patched_create  # type: ignore[assignment]
        return state

    @staticmethod
    def uninstall(client: object, state: _patch_mod.PatchState) -> None:
        messages_attr = getattr(client, "messages", None)
        if messages_attr is not None:
            messages_attr.create = state.original_create  # type: ignore[assignment]


def _inject_system_anthropic(kwargs: dict[str, Any], block: str) -> dict[str, Any]:
    """Anthropic accepts ``system`` as a string OR a list of blocks.

    Use the list form when the block is large enough that prompt
    caching is worth requesting, since cache_control is per-block.
    """
    full_block = _SYS_PREFIX + block
    user_system = kwargs.get("system")

    if len(full_block) >= _MIN_CACHE_CHARS:
        # Block form so we can attach cache_control.
        mnemo_block: dict[str, Any] = {
            "type": "text",
            "text": full_block,
            "cache_control": {"type": "ephemeral"},
        }
        if isinstance(user_system, list):
            new_system = [mnemo_block, *user_system]
        elif isinstance(user_system, str) and user_system:
            new_system = [mnemo_block, {"type": "text", "text": user_system}]
        else:
            new_system = [mnemo_block]
        return {**kwargs, "system": new_system}

    # Small enough that simple string concatenation is fine.
    if isinstance(user_system, str) and user_system:
        merged = full_block + "\n\n---\n\n" + user_system
    elif isinstance(user_system, list):
        # Don't downgrade the user's block-form system; insert as a block.
        return {**kwargs, "system": [{"type": "text", "text": full_block}, *user_system]}
    else:
        merged = full_block
    return {**kwargs, "system": merged}


_SYS_PREFIX = "[Project memory -- cite as shown]\n\n"


_patch_mod.register_shim(_NAME, _Shim)
