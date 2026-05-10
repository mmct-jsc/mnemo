"""Google Gemini shim.

Detects ``google.generativeai`` GenerativeModel. Wraps
``model.generate_content`` to inject mnemo as a leading system /
contents prefix.

google-generativeai exposes content-injection through
``system_instruction`` on the model OR by prepending a
``role='user'`` content with a special marker. We use
``system_instruction`` when the model exposes it; otherwise
fall back to prepending text content.
"""

from __future__ import annotations

import logging
from typing import Any

from mnemo_middleware import _patcher as _patch_mod
from mnemo_middleware.retrieve import retrieve_context

log = logging.getLogger(__name__)

_NAME = "google"


class _Shim:
    name = _NAME

    @staticmethod
    def matches(client: object) -> bool:
        mod = type(client).__module__ or ""
        if "google.generativeai" not in mod and "google.genai" not in mod:
            return False
        cls_name = type(client).__name__
        return "GenerativeModel" in cls_name or cls_name == "Client"

    @staticmethod
    def install(client: object, *, mode: str) -> _patch_mod.PatchState:
        if not hasattr(client, "generate_content"):
            raise TypeError("Google generative model missing generate_content")
        original = client.generate_content
        state = _patch_mod.PatchState(mode=mode, original_create=original, shim_name=_NAME)

        def patched_generate(contents: Any = None, *args: Any, **kwargs: Any) -> Any:
            messages = _to_messages(contents)
            try:
                if messages and _patch_mod.should_inject(state, messages):
                    query = _patch_mod.get_query_text(messages)
                    block = retrieve_context(query) if query.strip() else ""
                    if block:
                        new_contents = _prepend(contents, block)
                        _patch_mod.remember_call(state, messages, block)
                        return original(new_contents, *args, **kwargs)
                elif state.last_block:
                    return original(_prepend(contents, state.last_block), *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                log.warning("mnemo google shim error (%s); proceeding without injection", exc)
            return original(contents, *args, **kwargs)

        client.generate_content = patched_generate  # type: ignore[assignment]
        return state

    @staticmethod
    def uninstall(client: object, state: _patch_mod.PatchState) -> None:
        client.generate_content = state.original_create  # type: ignore[assignment]


def _to_messages(contents: Any) -> list[dict[str, Any]]:
    """Map google's flexible ``contents`` arg shape to a uniform
    list-of-message dicts so the patcher's helpers work."""
    if isinstance(contents, str):
        return [{"role": "user", "content": contents}]
    if isinstance(contents, list):
        out = []
        for c in contents:
            if isinstance(c, str):
                out.append({"role": "user", "content": c})
            elif isinstance(c, dict):
                role = c.get("role") or "user"
                # google content blocks: [{role, parts: [{text}, ...]}]
                parts = c.get("parts") or []
                texts = []
                for p in parts:
                    if isinstance(p, dict) and "text" in p:
                        texts.append(p["text"])
                    elif isinstance(p, str):
                        texts.append(p)
                out.append({"role": role, "content": "\n".join(texts)})
        return out
    return []


def _prepend(contents: Any, block: str) -> Any:
    """Return a new contents value with the mnemo block prepended."""
    full = _SYS_PREFIX + block
    if isinstance(contents, str):
        return full + "\n\n---\n\n" + contents
    if isinstance(contents, list):
        return [{"role": "user", "parts": [{"text": full}]}] + list(contents)
    # Unknown shape: prepend a string equivalent.
    return [{"role": "user", "parts": [{"text": full}]}, contents]


_SYS_PREFIX = "[Project memory -- cite as shown]\n\n"


_patch_mod.register_shim(_NAME, _Shim)
