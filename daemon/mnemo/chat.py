"""v3 phase 2: the server-side agent loop (design S2 / S4).

``AgentLoop.run(conv_id, user_text)`` is a generator of provider-
agnostic event dicts (the phase-3 SSE layer just serialises them). It:

  * persists the user message first (so a provider failure never loses
    it -- the user can retry),
  * iterates the provider <= ``MAX_ITERS`` times,
  * streams text deltas live, accumulates them into ONE assistant
    message, extracts ``[mnemo:ID]`` citations,
  * dispatches tool calls (phase 2: every registered tool is ``safe``
    and auto-runs; ``confirm``/``danger`` + the permission pause land
    in phase 4) and feeds results back,
  * stops on the provider's ``end_turn`` (-> ``done``) or surfaces a
    ``ProviderError`` as an ``error`` event with state preserved.

The system prompt is FROZEN (no timestamps / UUIDs) so the Anthropic
provider's tools+system cache prefix stays byte-stable across
iterations.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

from mnemo.agent_tools import TOOLS, ToolContext
from mnemo.compaction import (
    TRIGGER_TOKENS_DEFAULT,
    should_compact,
    summarize_prefix,
    supports_native_compaction,
)
from mnemo.providers import (
    EV_COMPACT,
    EV_STOP,
    EV_TEXT,
    EV_TOOL_CALL,
    EV_USAGE,
    BaseProvider,
    ProviderError,
)
from mnemo.store import Store

MAX_ITERS = 8

DEFAULT_SYSTEM = (
    "You are Mnem, the mnemo companion -- a knowledge assistant over the "
    "user's memory and code graph. Use the mnemo_* tools to research "
    "before answering. Cite every claim you draw from a node inline as "
    "[mnemo:<node_id>] so the UI can link it. Be concise and concrete."
)

_CITE_RE = re.compile(r"\[mnemo:([^\]\s]+)\]")


def _extract_citations(text: str) -> list[str]:
    seen: list[str] = []
    for m in _CITE_RE.finditer(text or ""):
        nid = m.group(1)
        if nid not in seen:
            seen.append(nid)
    return seen


class AgentLoop:
    def __init__(
        self,
        store: Store,
        provider: BaseProvider,
        *,
        embedder=None,
        model: str,
        system: str | None = None,
        project_key: str | None = None,
        permission_cb=None,
        compaction_trigger_tokens: int = TRIGGER_TOKENS_DEFAULT,
    ):
        self._store = store
        self._provider = provider
        self._embedder = embedder
        self._model = model
        self._system = system or DEFAULT_SYSTEM
        self._project_key = project_key
        self._compaction_trigger = compaction_trigger_tokens
        # permission_cb(req: dict) -> 'allow_once'|'allow_always'|'deny'.
        # None = no decision channel; non-safe tools then default-deny
        # (the model gets a recoverable error tool_result, design S4).
        self._permission_cb = permission_cb

    # --- provider context reconstruction ---------------------------------

    def _history_to_provider(self, conv_id: str) -> list[dict]:
        """Rebuild the normalised provider message list from persisted
        rows so a follow-up message has full prior context."""
        pmsgs: list[dict] = []
        for msg in self._store.list_messages(conv_id):
            c = msg.content
            if msg.role == "user":
                pmsgs.append({"role": "user", "content": c.get("text", "")})
            elif msg.role == "assistant":
                # Native-compaction turns persist the provider's FULL
                # content (compaction blocks included) under "raw" -- it
                # MUST be replayed verbatim (the claude-api rule).
                if c.get("raw"):
                    pmsgs.append({"role": "assistant", "content": c["raw"]})
                    continue
                blocks: list[dict] = []
                if c.get("text"):
                    blocks.append({"type": "text", "text": c["text"]})
                for tc in c.get("tool_calls", []):
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["name"],
                            "input": tc.get("args", {}),
                        }
                    )
                pmsgs.append({"role": "assistant", "content": blocks or c.get("text", "")})
            elif msg.role == "tool_result":
                r = c
                pmsgs.append(
                    {
                        "role": "tool",
                        "content": [
                            {
                                "tool_use_id": r["id"],
                                "content": json.dumps(r["result"]),
                                "is_error": isinstance(r["result"], dict)
                                and "error" in r["result"],
                            }
                        ],
                    }
                )
            # 'tool_call' rows are folded into the assistant block above;
            # 'system' rows (unused in phase 2) are skipped.
        return pmsgs

    # --- the loop --------------------------------------------------------

    def run(self, conv_id: str, user_text: str) -> Iterator[dict]:
        # Persist the user turn FIRST -- survives a provider failure.
        self._store.append_message(conv_id, role="user", content={"text": user_text})
        pmsgs = self._history_to_provider(conv_id)
        tools = list(TOOLS.values())
        native = supports_native_compaction(self._provider.name, self._model)
        native_announced = False

        for it in range(MAX_ITERS):
            yield {"type": "thinking", "iter": it}

            # Compaction (design S3.3): keep the MODEL's context bounded
            # (UI history pagination is a separate concern). Re-checked
            # each iteration because tool results grow pmsgs.
            compact_flag = False
            if should_compact(pmsgs, trigger_tokens=self._compaction_trigger):
                if native:
                    compact_flag = True
                    if not native_announced:
                        native_announced = True
                        yield {"type": "compaction", "mode": "native"}
                else:
                    pmsgs, summary = summarize_prefix(self._provider, self._model, pmsgs)
                    if summary:
                        self._store.set_conversation_summary(conv_id, {"summary": summary})
                        yield {"type": "compaction", "mode": "summarize"}

            stream_kwargs: dict = {"model": self._model, "system": self._system}
            if compact_flag:
                stream_kwargs["compact"] = True

            text_parts: list[str] = []
            tool_calls: list[dict] = []
            stop_reason = "end_turn"
            usage: dict | None = None
            raw_content: list | None = None
            try:
                for kind, payload in self._provider.stream(pmsgs, tools, **stream_kwargs):
                    if kind == EV_TEXT:
                        text_parts.append(payload)
                        yield {"type": "text_delta", "text": payload}
                    elif kind == EV_TOOL_CALL:
                        tool_calls.append(payload)
                    elif kind == EV_USAGE:
                        usage = payload
                    elif kind == EV_COMPACT:
                        raw_content = payload
                    elif kind == EV_STOP:
                        stop_reason = payload
            except ProviderError as exc:
                yield {"type": "error", "message": str(exc)}
                return

            assistant_text = "".join(text_parts)
            citations = _extract_citations(assistant_text)

            content: dict = {"text": assistant_text, "citations": citations}
            if tool_calls:
                content["tool_calls"] = [
                    {"id": t["id"], "name": t["name"], "args": t.get("args", {})}
                    for t in tool_calls
                ]
            if raw_content is not None:
                # Native compaction: persist the FULL provider content so
                # _history_to_provider replays it verbatim next turn.
                content["raw"] = raw_content
            tok_in = usage.get("input_tokens") if usage else None
            tok_out = usage.get("output_tokens") if usage else None
            tok_cache = usage.get("cache_read_input_tokens") if usage else None
            self._store.append_message(
                conv_id,
                role="assistant",
                content=content,
                token_in=tok_in,
                token_out=tok_out,
                cache_read=tok_cache,
            )
            if usage is not None:
                self._store.bump_tokens(conv_id, delta=(tok_in or 0) + (tok_out or 0))
                conv = self._store.get_conversation(conv_id)
                yield {
                    "type": "usage",
                    "input_tokens": tok_in or 0,
                    "output_tokens": tok_out or 0,
                    "cache_read": tok_cache or 0,
                    "tokens_total": conv.tokens_total if conv else 0,
                }

            blocks: list[dict] = []
            if assistant_text:
                blocks.append({"type": "text", "text": assistant_text})
            for t in tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": t["id"],
                        "name": t["name"],
                        "input": t.get("args", {}),
                    }
                )
            pmsgs.append({"role": "assistant", "content": blocks or assistant_text})

            for nid in citations:
                yield {"type": "citation", "node_id": nid, "label": f"[mnemo:{nid}]"}

            if not tool_calls:
                yield {"type": "done", "stop_reason": stop_reason}
                return

            # Dispatch tools (phase 2: all registered tools are safe).
            ctx = ToolContext(store=self._store, embedder=self._embedder)
            result_blocks: list[dict] = []
            for tc in tool_calls:
                name = tc["name"]
                args = tc.get("args", {}) or {}
                yield {"type": "tool_call", "id": tc["id"], "name": name, "args": args}
                spec = TOOLS.get(name)
                if spec is None:
                    result = {"error": f"unknown tool: {name!r}"}
                elif spec.risk == "safe" or self._store.is_permission_granted(
                    project_key=self._project_key, tool_name=name
                ):
                    result = spec.fn(ctx, **args)
                else:
                    allow_always_ok = spec.risk != "danger"
                    req = {
                        "type": "permission_request",
                        "id": tc["id"],
                        "tool_name": name,
                        "tool_args": args,
                        "risk": spec.risk,
                        "rationale": f"Mnem wants to run {name} ({spec.risk}).",
                        "auto_grant_options": (["always", "once"] if allow_always_ok else ["once"]),
                    }
                    yield req
                    decision = self._permission_cb(req) if self._permission_cb else "deny"
                    if decision == "deny":
                        result = {"error": f"user denied permission for {name}"}
                    else:
                        if decision == "allow_always" and allow_always_ok:
                            self._store.grant_permission(
                                project_key=self._project_key, tool_name=name
                            )
                        result = spec.fn(ctx, **args)
                # UI-directive tools (design S11): the daemon does not
                # execute them -- emit a ui_action for the chat UI to
                # dispatch and ack the model so it continues.
                if isinstance(result, dict) and "_ui_action" in result:
                    ua = result["_ui_action"]
                    yield {
                        "type": "ui_action",
                        "action": ua["action"],
                        "args": ua.get("args", {}),
                    }
                    result = {"ui_action_dispatched": ua["action"]}
                self._store.append_message(
                    conv_id,
                    role="tool_call",
                    content={"id": tc["id"], "name": name, "args": args},
                )
                self._store.append_message(
                    conv_id,
                    role="tool_result",
                    content={"id": tc["id"], "result": result},
                )
                is_err = isinstance(result, dict) and "error" in result
                yield {
                    "type": "tool_result",
                    "id": tc["id"],
                    "name": name,
                    "is_error": is_err,
                    "result": result,
                }
                result_blocks.append(
                    {
                        "tool_use_id": tc["id"],
                        "content": json.dumps(result),
                        "is_error": is_err,
                    }
                )
            pmsgs.append({"role": "tool", "content": result_blocks})

        yield {
            "type": "error",
            "message": f"max agent iterations ({MAX_ITERS}) reached without a final answer",
        }
