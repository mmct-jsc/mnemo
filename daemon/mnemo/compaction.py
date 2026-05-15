"""v3.1 phase 3: hybrid conversation compaction (design S3.3).

Two paths, picked by the agent loop:

  * **native** -- provider is Anthropic AND the model is
    compaction-capable: the request streams through
    ``client.beta.messages`` with the ``compact-2026-01-12`` beta and
    ``context_management``; the FULL final content (compaction blocks
    included) is preserved verbatim and replayed (the claude-api
    critical rule). Implemented inside ``AnthropicProvider`` +
    ``AgentLoop`` -- this module only owns the *decision* + the
    provider-agnostic fallback.
  * **fallback** -- everything else: summarize the oldest turns into a
    single pinned ``system`` message and keep the recent tail. Bounded,
    deterministic, model-agnostic. This is the workhorse (the project
    default model ``claude-sonnet-4-5`` is NOT compaction-capable).

UI history pagination and model-context compaction are SEPARATE
concerns (design decided fork 3): this only bounds what the model
sees, never what the user can scroll back to.
"""

from __future__ import annotations

from mnemo.providers import EV_TEXT, BaseProvider

# Settings knob default (design S3.3). The agent loop takes the live
# value; this is the floor when no settings override is wired.
TRIGGER_TOKENS_DEFAULT = 120_000

# How many of the most-recent turns the fallback keeps verbatim.
KEEP_RECENT_DEFAULT = 6

# Providers/models that do server-side compaction natively. Anthropic
# compaction beta is Opus 4.7 / Opus 4.6 / Sonnet 4.6 (NOT Sonnet 4.5,
# the mnemo default -> that path always uses the fallback).
NATIVE_COMPACTION: dict[str, frozenset[str]] = {
    "anthropic": frozenset(
        {
            "claude-opus-4-7",
            "claude-opus-4-6",
            "claude-sonnet-4-6",
        }
    ),
}

ANTHROPIC_COMPACT_BETA = "compact-2026-01-12"
ANTHROPIC_CONTEXT_MANAGEMENT = {"edits": [{"type": "compact_20260112"}]}

_SUMMARY_SYSTEM = (
    "You compress conversation history. Summarize the conversation "
    "prefix below in 400 tokens or fewer. Preserve every decision, "
    "identifier, file path, node id and [mnemo:<id>] citation verbatim. "
    "Output ONLY the summary prose -- no preamble."
)


def estimate_tokens(messages: list[dict]) -> int:
    """Cheap, dependency-free token estimate: ~4 chars/token over the
    stringified message contents. Good enough to decide *whether* to
    compact (the real budget is enforced server-side / by the model)."""
    chars = 0
    for m in messages:
        c = m.get("content")
        chars += len(c) if isinstance(c, str) else len(str(c))
    return chars // 4


def should_compact(messages: list[dict], *, trigger_tokens: int = TRIGGER_TOKENS_DEFAULT) -> bool:
    return estimate_tokens(messages) >= trigger_tokens


def supports_native_compaction(provider_name: str, model: str) -> bool:
    return model in NATIVE_COMPACTION.get(provider_name, frozenset())


def _collect_text(provider: BaseProvider, model: str, prompt: str) -> str:
    parts: list[str] = []
    for kind, payload in provider.stream(
        [{"role": "user", "content": prompt}],
        [],
        model=model,
        system=_SUMMARY_SYSTEM,
    ):
        if kind == EV_TEXT:
            parts.append(payload)
    return "".join(parts).strip()


def summarize_prefix(
    provider: BaseProvider,
    model: str,
    messages: list[dict],
    *,
    keep_recent: int = KEEP_RECENT_DEFAULT,
) -> tuple[list[dict], str]:
    """Replace everything older than the last ``keep_recent`` turns with
    one pinned ``system`` summary message. Returns
    ``(new_messages, summary_text)``. No-op (and no provider call) when
    there is nothing to compact."""
    if len(messages) <= keep_recent:
        return messages, ""
    prefix = messages[:-keep_recent] if keep_recent else messages
    tail = messages[-keep_recent:] if keep_recent else []

    serialized = "\n".join(
        f"{m.get('role', '?')}: "
        f"{m.get('content') if isinstance(m.get('content'), str) else str(m.get('content'))}"
        for m in prefix
    )
    summary = _collect_text(provider, model, serialized)
    pinned = {
        "role": "system",
        "content": f"[earlier conversation summary]\n{summary}",
    }
    return [pinned, *tail], summary
