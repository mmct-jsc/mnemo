"""v3: provider abstraction (design S2).

One ``BaseProvider.stream()`` shape, four implementations. The agent
loop (``mnemo.chat``) is provider-agnostic: it only sees the tagged
events below, never an SDK type.

Event protocol (design S2) -- ``stream()`` yields tuples:

  * ``('text_delta', str)``                -- assistant text chunk
  * ``('tool_call', {id, name, args})``    -- the model wants a tool
  * ``('usage', {input_tokens, output_tokens,
        cache_read_input_tokens})``        -- v3.1: per-turn token
        usage, yielded AT MOST ONCE directly before ('stop', ...).
        Omitted when the provider/transport didn't surface counts (a
        mocked client without usage stays a pure text/tool/stop stream).
  * ``('stop', reason)``                   -- 'end_turn' | 'tool_use'
                                              | 'max_tokens' | ...

Normalised message shape the loop builds + persists, and each provider
translates to its own API:

  * ``{'role': 'user', 'content': '<text>'}``
  * ``{'role': 'assistant', 'content': [ {'type':'text','text':..},
        {'type':'tool_use','id':..,'name':..,'input':{..}} ]}``
  * ``{'role': 'tool', 'content': [ {'tool_use_id':..,
        'content':'<json str>', 'is_error': bool} ]}``

Phase 2 ships Anthropic only; OpenAI / Google / Ollama land in phase 5
behind the same contract (``get_provider`` raises until then).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

EV_TEXT = "text_delta"
EV_TOOL_CALL = "tool_call"
EV_USAGE = "usage"
# v3.1 native compaction: the FULL final content (compaction blocks
# included) as a list of plain dicts, so the loop can persist + replay
# it verbatim (the claude-api "preserve response.content" rule). Only
# the Anthropic provider in compact mode yields this.
EV_COMPACT = "compaction"
EV_STOP = "stop"


def _usage_event(
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_input_tokens: int | None = 0,
) -> tuple[str, dict] | None:
    """Build the ``('usage', {...})`` event, or None when the provider
    surfaced no usable counts (so the loop stays a text/tool/stop
    stream -- design S3.2). Counts coerce to non-negative ints."""
    if input_tokens is None and output_tokens is None:
        return None

    def _n(v: object) -> int:
        try:
            return max(0, int(v))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    return (
        EV_USAGE,
        {
            "input_tokens": _n(input_tokens),
            "output_tokens": _n(output_tokens),
            "cache_read_input_tokens": _n(cache_read_input_tokens),
        },
    )


ProviderEvent = tuple[str, Any]

# Default model per provider (design S4). C2 (v4.1): DERIVED from
# PROVIDERS, defined at the bottom of this module AFTER the concrete
# providers self-register (no longer a hand-maintained literal).
DEFAULT_MODELS: dict[str, str]


class ProviderError(RuntimeError):
    """A provider call failed. The agent loop turns this into an
    ``error`` SSE event with conversation state preserved (design S4)."""


class BaseProvider:
    """Subclasses set ``name`` and implement ``stream``."""

    name: str = "base"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None):
        self._api_key = api_key
        self._base_url = base_url

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
        raise NotImplementedError("provider must implement stream()")


@dataclass(frozen=True)
class ProviderDescriptor:
    """C2 (v4.1): one declarative registry entry per provider. Mirrors
    agent_tools.ToolSpec/TOOLS/_register exactly -- adding a provider =
    one register_provider(...) call + one stream() impl class, instead
    of editing get_provider + DEFAULT_MODELS + keys.ENV_VAR +
    Config.providers + compaction.NATIVE_COMPACTION (5 files)."""

    name: str
    display_name: str
    impl_class: type
    env_var: str | None
    requires_key: bool
    default_model: str
    known_models: tuple[str, ...]
    base_url: str | None
    native_compaction_models: frozenset[str]


PROVIDERS: dict[str, ProviderDescriptor] = {}


def register_provider(desc: ProviderDescriptor) -> ProviderDescriptor:
    """Validating registrar (mirrors agent_tools._register: dict +
    raise-on-dupe). get_provider / DEFAULT_MODELS / keys / config /
    compaction all DERIVE from PROVIDERS."""
    if desc.name in PROVIDERS:
        raise ValueError(f"duplicate provider registration: {desc.name}")
    PROVIDERS[desc.name] = desc
    return desc


def get_provider(
    name: str, *, api_key: str | None = None, base_url: str | None = None
) -> BaseProvider:
    """Construct a provider by name. C2 (v4.1): DERIVES from the
    PROVIDERS registry (was a hand-edited if/elif chain)."""
    desc = PROVIDERS.get(name)
    if desc is None:
        raise ValueError(f"unknown provider: {name!r}")
    return desc.impl_class(
        api_key=api_key,
        base_url=base_url if base_url is not None else desc.base_url,
    )


# C2 (v4.1): import the concrete modules LAST so their
# register_provider(...) calls run on `import mnemo.providers`.
# Registry/registrar/descriptor/BaseProvider are all defined ABOVE, so
# each module's `from mnemo.providers import ...` resolves against the
# partially-initialized module (standard register-at-bottom pattern;
# avoids a circular import).
from mnemo.providers import anthropic as _anthropic  # noqa: E402,F401
from mnemo.providers import google as _google  # noqa: E402,F401
from mnemo.providers import ollama as _ollama  # noqa: E402,F401
from mnemo.providers import openai as _openai  # noqa: E402,F401

# C2 (v4.1): single-sourced from the now-populated registry.
DEFAULT_MODELS = {n: d.default_model for n, d in PROVIDERS.items()}
