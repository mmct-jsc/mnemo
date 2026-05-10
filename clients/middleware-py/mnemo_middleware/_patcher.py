"""Auto-patcher for SDK clients.

Detects the client's provider by ``type(client).__module__`` and
delegates to the matching shim. Each shim wraps the relevant chat /
messages create method so retrieval flows through transparently.

State per patched client lives in a ``__mnemo_patch_state__`` attr on
the client; this is what enables ``mode='auto'`` (re-inject only on
new conversations / topic shifts) and ``mode='once'`` (one-shot per
patch).

Failure mode is additive: if a shim raises during injection, the
original method is invoked unchanged and a WARNING is logged.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from mnemo_middleware.errors import UnsupportedClient

log = logging.getLogger(__name__)

VALID_MODES = ("auto", "once", "every")


@dataclass
class PatchState:
    """Per-client state. Keyed off the patched client object so users
    can patch multiple clients simultaneously."""

    mode: str
    original_create: Any
    shim_name: str
    # 'auto' bookkeeping
    last_messages_hash: str | None = None
    last_first_message_hash: str | None = None
    last_block: str | None = None
    last_block_query: str | None = None
    has_injected_once: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


def patch(client: object, *, mode: str = "auto") -> object:
    """Install retrieval injection on a known SDK client.

    Returns the same client (so callers can chain). Raises
    ``UnsupportedClient`` if the client's provider isn't recognized.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES!r}, got {mode!r}")

    state = getattr(client, "__mnemo_patch_state__", None)
    if state is not None:
        # Already patched -- update mode and return.
        state.mode = mode
        return client

    shim = _detect_shim(client)
    if shim is None:
        raise UnsupportedClient(
            f"mnemo_middleware doesn't know how to patch {type(client)!r}. "
            f"Supported: openai.OpenAI, anthropic.Anthropic, "
            f"google.generativeai client, ollama.Client. "
            f"Use mnemo_middleware.retrieve_context() directly instead."
        )

    state = shim.install(client, mode=mode)
    client.__mnemo_patch_state__ = state
    return client


def unpatch(client: object) -> object:
    """Remove a previous patch. Idempotent."""
    state = getattr(client, "__mnemo_patch_state__", None)
    if state is None:
        return client
    shim = _SHIMS.get(state.shim_name)
    if shim is not None:
        shim.uninstall(client, state)
    import contextlib

    with contextlib.suppress(AttributeError):
        delattr(client, "__mnemo_patch_state__")
    return client


# --- shim registry --------------------------------------------------------


_SHIMS: dict[str, Any] = {}


def register_shim(name: str, shim: Any) -> None:
    """Called by each provider shim module at import time."""
    _SHIMS[name] = shim


def _detect_shim(client: object) -> Any:
    """Try each registered shim in turn; return the first that claims
    the client."""
    # Lazy import: only load shims when patch() is called, so the base
    # package's import-time cost stays tiny.
    from mnemo_middleware._shims import anthropic, google, ollama, openai  # noqa: F401

    for shim in _SHIMS.values():
        if shim.matches(client):
            return shim
    return None


# --- shared injection logic ----------------------------------------------


def should_inject(state: PatchState, messages: list[dict[str, Any]]) -> bool:
    """Decide whether the current call should re-inject mnemo retrieval.

    ``auto``  -- re-inject when:
        - this is the first call ever (state.has_injected_once is False)
        - messages length is <= 2 (first or near-first turn)
        - messages[0] differs from last seen (new conversation)

    ``once``  -- inject only on the first call.
    ``every`` -- always inject.
    """
    if state.mode == "every":
        return True
    if state.mode == "once":
        return not state.has_injected_once
    # 'auto'
    if not state.has_injected_once:
        return True
    if len(messages) <= 2:
        return True
    first_hash = _hash_dict(messages[0]) if messages else None
    # Same conversation continuing -- skip re-inject and reuse last block.
    return first_hash != state.last_first_message_hash


def remember_call(state: PatchState, messages: list[dict[str, Any]], block: str) -> None:
    """Update PatchState after an injection."""
    state.has_injected_once = True
    state.last_block = block
    if messages:
        state.last_first_message_hash = _hash_dict(messages[0])
    state.last_messages_hash = _hash_messages(messages)
    state.last_block_query = _last_user_text(messages)


def get_query_text(messages: list[dict[str, Any]]) -> str:
    """Pull the user's query text from a messages list. Concatenate
    user messages so multi-turn context informs retrieval."""
    parts: list[str] = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            content = m.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                # OpenAI v1 / Anthropic content blocks
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        text = blk.get("text") or ""
                        if text:
                            parts.append(text)
                    elif isinstance(blk, str):
                        parts.append(blk)
    return "\n\n".join(parts)


def _last_user_text(messages: list[dict[str, Any]]) -> str | None:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        return blk.get("text") or ""
    return None


def _hash_dict(d: dict[str, Any]) -> str:
    """Stable-ish hash for a message dict. We don't need cryptographic
    strength, just collision-resistance over likely message content."""
    import hashlib
    import json

    try:
        s = json.dumps(d, sort_keys=True, default=str)
    except (TypeError, ValueError):
        s = str(d)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _hash_messages(messages: list[dict[str, Any]]) -> str:
    return _hash_dict({"messages": messages})


# Used by shims to time injections (for debug logs / telemetry).
def now_ms() -> int:
    return int(time.time() * 1000)
