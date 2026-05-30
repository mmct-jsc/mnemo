"""v3: the agent tool surface -- ONE source of truth, two consumers.

The internal agent loop (``mnemo.chat``, phase 2) and the MCP server
(``mnemo.mcp_server``, phase 6) both read :data:`TOOLS`. Each tool is a
:class:`ToolSpec` carrying a JSON-Schema for its params and a ``risk``
tag that drives the permission system (design doc S3):

  * ``safe``    -- no side effects; auto-run; never prompted.
  * ``confirm`` -- mutates recoverably; prompts unless allow-always.
  * ``danger``  -- destructive; always prompts, no allow-always.

Phase 1 ships only the six ``safe`` read tools. Write / exec / danger
tools + the permission protocol land in phase 4; every later mnemo
feature adds its tool here and both consumers pick it up for free.

Tool functions are ``fn(ctx, **kwargs) -> dict`` where the return is
always JSON-serialisable. Tools never raise to the caller: an
unexpected failure (or a not-found / bad-arg) comes back as
``{"error": "..."}`` so the agent loop can feed it to the model as a
recoverable ``tool_result`` (design S4 error handling).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mnemo import config, retrieve
from mnemo.parsers import md as _md_parser
from mnemo.store import NODE_TYPES, Node, Store, signal_for_reason

RISK_SAFE = "safe"
RISK_CONFIRM = "confirm"
RISK_DANGER = "danger"

# v3 phase 1.5: structured risk taxonomy. ``Risk`` gives static
# type-checkers (ruff / mypy / pyright) edit-time safety against
# bogus values; ``ALL_RISKS`` is the single source of truth for
# tests + host-side validators. Order is least-to-most dangerous so
# hosts iterating the tuple display risks in a sensible default
# order. Treated as part of the MCP wire contract: external hosts
# can read ``descriptor["risk"]`` (returned by ``mcp_server.tool_list``)
# and trust the value is one of these three literals.
Risk = Literal["safe", "confirm", "danger"]
ALL_RISKS: tuple[Risk, ...] = (RISK_SAFE, RISK_CONFIRM, RISK_DANGER)

# Hard cap so a single mnemo_get_code_lines call can't dump a whole
# huge file into the model context.
MAX_CODE_LINES = 400


@dataclass
class ToolContext:
    """Everything a tool needs to run. The agent loop builds one per
    request; MCP builds one per process. ``embedder`` is only needed by
    ``mnemo_query``."""

    store: Store
    embedder: Any | None = None
    # v3.2: the running conversation id (when invoked by the agent
    # loop). Lets safe tools resolve the live, client-PATCHed
    # ``page_context``. None when invoked by MCP / outside a chat.
    conversation_id: str | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    risk: Risk
    parameters: dict  # JSON Schema (object) -- provider tool defs + MCP
    fn: Callable[..., dict]


TOOLS: dict[str, ToolSpec] = {}


def _register(spec: ToolSpec) -> ToolSpec:
    if spec.name in TOOLS:
        raise ValueError(f"duplicate tool registration: {spec.name}")
    if spec.risk not in ALL_RISKS:
        raise ValueError(f"bad risk for {spec.name}: {spec.risk!r} not in {ALL_RISKS}")
    TOOLS[spec.name] = spec
    return spec


def _tool(
    *, name: str, risk: Risk, description: str, parameters: dict
) -> Callable[[Callable[..., dict]], Callable[..., dict]]:
    def deco(fn: Callable[..., dict]) -> Callable[..., dict]:
        def safe_fn(ctx: ToolContext, **kwargs: Any) -> dict:
            try:
                return fn(ctx, **kwargs)
            except Exception as exc:  # never raise to the agent loop
                return {"error": f"{type(exc).__name__}: {exc}"}

        _register(
            ToolSpec(
                name=name,
                description=description,
                risk=risk,
                parameters=parameters,
                fn=safe_fn,
            )
        )
        return safe_fn

    return deco


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


# --- 1. mnemo_query -----------------------------------------------------


@_tool(
    name="mnemo_query",
    risk=RISK_SAFE,
    description=(
        "Hybrid Graph-RAG retrieval over memory + code, ranked and "
        "token-budgeted. Use for broad research. Returns ranked hits "
        "each with a [mnemo:<id>] citation. v5: pass "
        "``exclude_local_only=true`` when the output will be pasted "
        "into a foreign LLM (the prompt-architect skill does this); "
        "the result's ``local_only_excluded`` count tells the UI how "
        "many confidential nodes were dropped pre-paste."
    ),
    parameters=_obj(
        {
            "prompt": {"type": "string", "description": "natural-language query"},
            "limit": {"type": "integer", "default": 8},
            "max_tokens": {"type": "integer", "default": 800},
            "project_key": {"type": ["string", "null"], "default": None},
            "exclude_local_only": {
                "type": "boolean",
                "default": False,
                "description": (
                    "When true, nodes flagged ``local_only`` (see v5 design) "
                    "are filtered from the result set. The count of dropped "
                    "nodes is exposed as ``local_only_excluded`` on the "
                    "response so the dock can warn before paste."
                ),
            },
        },
        ["prompt"],
    ),
)
def _mnemo_query(
    ctx: ToolContext,
    *,
    prompt: str,
    limit: int = 8,
    max_tokens: int = 800,
    project_key: str | None = None,
    exclude_local_only: bool = False,
) -> dict:
    res = retrieve.query(
        ctx.store,
        ctx.embedder,
        prompt,
        budget_tokens=max_tokens,
        k=limit,
        active_project=project_key,
        exclude_local_only=exclude_local_only,
    )
    return {
        "hits": [
            {
                "node_id": h.node_id,
                "type": h.type,
                "name": h.name,
                "description": h.description,
                "score": round(h.score, 4),
                "citation": h.citation,
                "source_path": h.source_path,
            }
            for h in res.hits
        ],
        "intent_tags": res.intent_tags,
        "tokens_used": res.tokens_used,
        "query_id": res.query_id,
        # v5.1.0: always present (0 when the filter is off). The
        # dock's SSE handler accumulates this into the factory's
        # ``localOnlyExcluded`` state so the pre-emit warning
        # banner can fire on real retrieval data.
        "local_only_excluded": res.local_only_excluded,
    }


# --- 2. mnemo_get_node --------------------------------------------------


@_tool(
    name="mnemo_get_node",
    risk=RISK_SAFE,
    description="Full body + frontmatter for one node by id.",
    parameters=_obj(
        {"node_id": {"type": "string"}},
        ["node_id"],
    ),
)
def _mnemo_get_node(ctx: ToolContext, *, node_id: str) -> dict:
    n = ctx.store.get_node(node_id)
    if n is None:
        return {"error": "node not found", "node_id": node_id}
    fm = json.loads(n.frontmatter_json) if n.frontmatter_json else None
    return {
        "node_id": n.id,
        "type": n.type,
        "name": n.name,
        "description": n.description,
        "body": n.body,
        "source_path": n.source_path,
        "source_kind": n.source_kind,
        "project_key": n.project_key,
        "base": n.base,
        "frontmatter": fm,
        "created_at": n.created_at,
        "updated_at": n.updated_at,
    }


# --- 3. mnemo_get_edges -------------------------------------------------


@_tool(
    name="mnemo_get_edges",
    risk=RISK_SAFE,
    description=(
        "Edges connected to a node. direction in {out,in,both}; optional relation filter."
    ),
    parameters=_obj(
        {
            "node_id": {"type": "string"},
            "direction": {
                "type": "string",
                "enum": ["out", "in", "both"],
                "default": "both",
            },
            "relation": {"type": ["string", "null"], "default": None},
        },
        ["node_id"],
    ),
)
def _mnemo_get_edges(
    ctx: ToolContext,
    *,
    node_id: str,
    direction: str = "both",
    relation: str | None = None,
) -> dict:
    rels = (relation,) if relation else None
    edges = ctx.store.get_edges_for_nodes([node_id], relations=rels)
    out = []
    for e in edges:
        d = "out" if e.src_id == node_id else "in"
        if direction != "both" and direction != d:
            continue
        out.append(
            {
                "src": e.src_id,
                "dst": e.dst_id,
                "relation": e.relation,
                "weight": e.weight,
                "confidence": e.confidence,
                "source": e.source,
                "direction": d,
            }
        )
    return {"node_id": node_id, "direction": direction, "edges": out}


# --- 4. mnemo_traverse --------------------------------------------------


@_tool(
    name="mnemo_traverse",
    risk=RISK_SAFE,
    description=(
        "BFS from a node up to max_hops (optional relation filter). The "
        "'why-is-this-here' provenance walk. Returns nodes tagged with "
        "their hop distance + the edges traversed."
    ),
    parameters=_obj(
        {
            "start_id": {"type": "string"},
            "max_hops": {"type": "integer", "default": 2},
            "relations": {"type": ["array", "null"], "items": {"type": "string"}},
        },
        ["start_id"],
    ),
)
def _mnemo_traverse(
    ctx: ToolContext,
    *,
    start_id: str,
    max_hops: int = 2,
    relations: list[str] | None = None,
) -> dict:
    if ctx.store.get_node(start_id) is None:
        return {"error": "start node not found", "start_id": start_id}
    rels = tuple(relations) if relations else None
    visited: dict[str, int] = {start_id: 0}
    frontier = [start_id]
    seen_edges: set[tuple[str, str, str]] = set()
    edges_out: list[dict] = []
    for hop in range(1, max(1, int(max_hops)) + 1):
        if not frontier:
            break
        es = ctx.store.get_edges_for_nodes(frontier, relations=rels)
        fset = set(frontier)
        nxt: list[str] = []
        for e in es:
            key = (e.src_id, e.dst_id, e.relation)
            if key not in seen_edges and (e.src_id in fset or e.dst_id in fset):
                seen_edges.add(key)
                edges_out.append(
                    {
                        "src": e.src_id,
                        "dst": e.dst_id,
                        "relation": e.relation,
                        "confidence": e.confidence,
                    }
                )
            for endpoint in (e.src_id, e.dst_id):
                other = e.dst_id if endpoint == e.src_id else e.src_id
                if endpoint in fset and other not in visited:
                    visited[other] = hop
                    nxt.append(other)
        frontier = nxt
    nodes_by_id = ctx.store.get_nodes_by_ids(list(visited))
    nodes = [
        {
            "node_id": nid,
            "type": (nodes_by_id[nid].type if nid in nodes_by_id else None),
            "name": (nodes_by_id[nid].name if nid in nodes_by_id else None),
            "hop": hop,
        }
        for nid, hop in sorted(visited.items(), key=lambda kv: (kv[1], kv[0]))
    ]
    return {
        "start_id": start_id,
        "max_hops": max_hops,
        "nodes": nodes,
        "edges": edges_out,
    }


# --- 5. mnemo_search_by_type --------------------------------------------


@_tool(
    name="mnemo_search_by_type",
    risk=RISK_SAFE,
    description="List nodes by type, optional name glob + project filter.",
    parameters=_obj(
        {
            "type": {"type": "string"},
            "name_glob": {"type": ["string", "null"], "default": None},
            "project_key": {"type": ["string", "null"], "default": None},
            "limit": {"type": "integer", "default": 20},
        },
        ["type"],
    ),
)
def _mnemo_search_by_type(
    ctx: ToolContext,
    *,
    type: str,
    name_glob: str | None = None,
    project_key: str | None = None,
    limit: int = 20,
) -> dict:
    if type not in NODE_TYPES:
        return {"error": f"unknown node type: {type!r}", "type": type}
    nodes = ctx.store.list_nodes(type=type, project_key=project_key, limit=limit)
    if name_glob:
        nodes = [n for n in nodes if fnmatch.fnmatch(n.name, name_glob)]
    return {
        "type": type,
        "count": len(nodes),
        "nodes": [
            {
                "node_id": n.id,
                "name": n.name,
                "description": n.description,
                "project_key": n.project_key,
                "type": n.type,
            }
            for n in nodes
        ],
    }


# --- 6. mnemo_get_code_lines --------------------------------------------


@_tool(
    name="mnemo_get_code_lines",
    risk=RISK_SAFE,
    description=(
        f"Read lines [start, end] (1-based, inclusive) from a source "
        f"file. Capped at {MAX_CODE_LINES} lines per call."
    ),
    parameters=_obj(
        {
            "source_path": {"type": "string"},
            "start": {"type": "integer"},
            "end": {"type": "integer"},
        },
        ["source_path", "start", "end"],
    ),
)
def _mnemo_get_code_lines(ctx: ToolContext, *, source_path: str, start: int, end: int) -> dict:
    p = Path(source_path)
    if not p.is_file():
        return {"error": "file not found", "source_path": source_path}
    start = max(1, int(start))
    end = int(end)
    if end < start:
        return {"error": "end < start", "source_path": source_path}
    if end - start + 1 > MAX_CODE_LINES:
        end = start + MAX_CODE_LINES - 1
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    slice_ = lines[start - 1 : end]
    return {
        "source_path": source_path,
        "start": start,
        "end": end,
        "lines": "\n".join(slice_),
    }


# --- 7. mnemo_page_context (v3.2) ---------------------------------------
#
# The companion must ground on the CURRENT screen, not guess. The chat
# client calls ``window.mnemoPageContext()`` and PATCHes the result onto
# the conversation before every run (design v3.2 S3.1); this tool hands
# the model that live state plus the server-known view so it can act in
# the page ("what's selected here / related in this session") instead of
# blindly redirecting.


@_tool(
    name="mnemo_page_context",
    risk=RISK_SAFE,
    description=(
        "The user's CURRENT screen: the live page state the UI attached "
        "to this conversation (page, path, selected node, visible nodes, "
        "query, weights, ...). Call this FIRST when the user says 'this "
        "page' / 'here' / 'what's on screen' so you act in-context."
    ),
    parameters=_obj({}, []),
)
def _mnemo_page_context(ctx: ToolContext) -> dict:
    cid = ctx.conversation_id
    conv = ctx.store.get_conversation(cid) if cid else None
    if conv is None:
        return {
            "available": False,
            "page_context": None,
            "conversation_id": cid,
            "project_key": None,
        }
    return {
        "available": conv.page_context is not None,
        "page_context": conv.page_context,
        "conversation_id": conv.id,
        "project_key": conv.project_key,
    }


# --- 8. mnemo_session_nodes (v3.2) --------------------------------------
#
# "What's related in THIS session?" -- the cited / tool-used node ids of
# the running conversation + their 1-hop neighbours. The companion calls
# this then mnemo_highlight_nodes to highlight the subgraph ON THE LIVE
# NEBULA GRAPH instead of guessing. v4.6 replaced the v4.5 third-party
# renderer with the custom nebula-gl.js WebGL engine; the renderer
# handle's setHighlight() still makes a real graph-view highlight a
# pure data change -- this closes the old gotcha-31 cosmos ceiling
# and the v3.2 "side panel, not the graph" honesty caveat.

_SESSION_CITE_RE = re.compile(r"\[mnemo:([^\]\s]+)\]")
# the tool args that unambiguously name a node (so a tool the user
# acted on counts as "used" even without an inline citation).
_NODE_ID_ARGS = ("node_id", "start_id")


@_tool(
    name="mnemo_session_nodes",
    risk=RISK_SAFE,
    description=(
        "The nodes this conversation has cited or acted on, plus their "
        "1-hop neighbours. Use to ground 'what's related in this "
        "session / highlight it on the Nebula graph' before "
        "mnemo_highlight_nodes."
    ),
    parameters=_obj({}, []),
)
def _mnemo_session_nodes(ctx: ToolContext) -> dict:
    cid = ctx.conversation_id
    empty = {
        "conversation_id": cid,
        "node_ids": [],
        "neighbor_ids": [],
        "edges": [],
        "count": 0,
    }
    if not cid or ctx.store.get_conversation(cid) is None:
        return empty

    seeds: list[str] = []

    def _add(nid: object) -> None:
        if isinstance(nid, str) and nid and nid not in seeds:
            seeds.append(nid)

    for msg in ctx.store.list_messages(cid):
        c = msg.content or {}
        if msg.role == "assistant":
            for nid in c.get("citations") or []:
                _add(nid)
            for m in _SESSION_CITE_RE.finditer(c.get("text") or ""):
                _add(m.group(1))
        elif msg.role == "tool_call":
            args = c.get("args") or {}
            for k in _NODE_ID_ARGS:
                _add(args.get(k))
        elif msg.role == "tool_result":
            res = c.get("result")
            if isinstance(res, dict):
                _add(res.get("node_id"))

    # Keep only seeds that still exist (a node may have been deleted).
    existing = ctx.store.get_nodes_by_ids(seeds)
    node_ids = [s for s in seeds if s in existing]

    edges = ctx.store.get_edges_for_nodes(node_ids) if node_ids else []
    node_set = set(node_ids)
    neighbors: list[str] = []
    edges_out: list[dict] = []
    for e in edges:
        edges_out.append(
            {
                "src": e.src_id,
                "dst": e.dst_id,
                "relation": e.relation,
                "confidence": e.confidence,
            }
        )
        for endpoint in (e.src_id, e.dst_id):
            if endpoint not in node_set and endpoint not in neighbors:
                neighbors.append(endpoint)
    # only surface neighbours that are real nodes
    if neighbors:
        nb_existing = ctx.store.get_nodes_by_ids(neighbors)
        neighbors = [n for n in neighbors if n in nb_existing]

    return {
        "conversation_id": cid,
        "node_ids": node_ids,
        "neighbor_ids": neighbors,
        "edges": edges_out,
        "count": len(node_ids),
    }


# --- write / exec tools (confirm) ---------------------------------------


@_tool(
    name="mnemo_create_node",
    risk=RISK_CONFIRM,
    description="Create a new memory node. Reindex picks it up later.",
    parameters=_obj(
        {
            "type": {"type": "string"},
            "name": {"type": "string"},
            "body": {"type": "string"},
            "frontmatter": {"type": ["object", "null"]},
            "project_key": {"type": ["string", "null"]},
        },
        ["type", "name", "body"],
    ),
)
def _mnemo_create_node(
    ctx: ToolContext,
    *,
    type: str,
    name: str,
    body: str,
    frontmatter: dict | None = None,
    project_key: str | None = None,
) -> dict:
    node = Node.new(
        type=type,
        name=name,
        body=body,
        source_path=f"mnemo-chat://{uuid.uuid4().hex}.md",
        source_kind="memory_dir",
        description=(frontmatter or {}).get("description"),
        project_key=project_key,
        frontmatter_json=json.dumps(frontmatter) if frontmatter else None,
    )
    ctx.store.upsert_node(node)
    return {"node_id": node.id, "type": node.type, "name": node.name}


@_tool(
    name="mnemo_update_node",
    risk=RISK_CONFIRM,
    description="Patch a node's name / description / body / frontmatter.",
    parameters=_obj(
        {"node_id": {"type": "string"}, "fields": {"type": "object"}},
        ["node_id", "fields"],
    ),
)
def _mnemo_update_node(ctx: ToolContext, *, node_id: str, fields: dict) -> dict:
    n = ctx.store.get_node(node_id)
    if n is None:
        return {"error": "node not found", "node_id": node_id}
    applied: list[str] = []
    for key in ("name", "description", "body"):
        if key in fields:
            setattr(n, key, fields[key])
            applied.append(key)
    if "frontmatter" in fields:
        n.frontmatter_json = json.dumps(fields["frontmatter"])
        applied.append("frontmatter")
    n.updated_at = int(time.time())
    ctx.store.upsert_node(n)
    return {"node_id": node_id, "updated": applied}


@_tool(
    name="mnemo_thumbs_feedback",
    risk=RISK_CONFIRM,
    description="Register a thumbs up/down on a retrieval hit.",
    parameters=_obj(
        {
            "node_id": {"type": "string"},
            "direction": {"type": "string", "enum": ["up", "down"]},
            "query_id": {"type": ["string", "null"]},
        },
        ["node_id", "direction"],
    ),
)
def _mnemo_thumbs_feedback(
    ctx: ToolContext,
    *,
    node_id: str,
    direction: str,
    query_id: str | None = None,
) -> dict:
    if ctx.store.get_node(node_id) is None:
        return {"error": "node not found", "node_id": node_id}
    reason = "thumbs_up" if direction == "up" else "thumbs_down"
    try:
        ctx.store.log_feedback_event(
            query_id=query_id or "chat-companion",
            node_id=node_id,
            signal=signal_for_reason(reason),
            reason=reason,
        )
        logged = True
    except Exception:
        # No backing query row (chat-originated) -> best-effort only.
        logged = False
    return {"ok": True, "node_id": node_id, "direction": direction, "logged": logged}


@_tool(
    name="mnemo_add_source",
    risk=RISK_CONFIRM,
    description="Register a new indexing source (memory_dir / code_repo / ...).",
    parameters=_obj(
        {
            "path": {"type": "string"},
            "kind": {"type": "string"},
            "project_key": {"type": ["string", "null"]},
        },
        ["path", "kind"],
    ),
)
def _mnemo_add_source(
    ctx: ToolContext, *, path: str, kind: str, project_key: str | None = None
) -> dict:
    ctx.store.register_source(path, kind, project_key=project_key)
    return {"ok": True, "path": path, "kind": kind}


@_tool(
    name="mnemo_reindex_source",
    risk=RISK_CONFIRM,
    description=(
        "Acknowledge a reindex request. The actual reindex runs via the "
        "daemon's /v1/reindex SSE channel (a tool must not block on it)."
    ),
    parameters=_obj(
        {"source_path": {"type": ["string", "null"]}},
        [],
    ),
)
def _mnemo_reindex_source(ctx: ToolContext, *, source_path: str | None = None) -> dict:
    return {
        "status": "queued",
        "source_path": source_path,
        "note": "trigger /v1/reindex (SSE) to run it; this tool only acknowledges",
    }


# --- 9. mnemo_apply_retune (v3.2) ---------------------------------------
#
# The Settings retune assistant's apply step. Scoped to ONLY the 6
# scoring weights -> a bounded, recoverable CONFIRM surface (config can
# be reset / re-applied), deliberately NOT the danger
# mnemo_change_settings catch-all. Returns before/after so the page can
# validate (design v3.2 S3.5).

_RETUNE_KEYS = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")


@_tool(
    name="mnemo_apply_retune",
    risk=RISK_CONFIRM,
    description=(
        "Apply retrieval scoring-weight deltas (alpha/beta/gamma/delta/"
        "epsilon/zeta) to the live config -- the Settings retune "
        "assistant's apply step. Bounded + recoverable. Returns the "
        "before/after so the page can validate."
    ),
    parameters=_obj(
        {
            "weights": {
                "type": "object",
                "description": "subset of alpha/beta/gamma/delta/epsilon/zeta -> float",
            }
        },
        ["weights"],
    ),
)
def _mnemo_apply_retune(ctx: ToolContext, *, weights: dict) -> dict:
    cur = config.load().scoring
    before = {k: getattr(cur, k) for k in _RETUNE_KEYS}
    valid: dict[str, float] = {}
    ignored: list[str] = []
    for k, v in (weights or {}).items():
        if k in _RETUNE_KEYS and isinstance(v, int | float) and not isinstance(v, bool):
            valid[k] = float(v)
        else:
            ignored.append(k)
    if valid:
        config.update({"scoring": valid})
    after_sc = config.load().scoring
    after = {k: getattr(after_sc, k) for k in _RETUNE_KEYS}
    return {
        "ok": True,
        "before": before,
        "after": after,
        "applied": sorted(valid.keys()),
        "ignored": ignored,
    }


# --- danger tools (always prompt, no allow-always) ----------------------


@_tool(
    name="mnemo_delete_node",
    risk=RISK_DANGER,
    description="Permanently delete a node (cascades its edges).",
    parameters=_obj({"node_id": {"type": "string"}}, ["node_id"]),
)
def _mnemo_delete_node(ctx: ToolContext, *, node_id: str) -> dict:
    ctx.store.delete_node(node_id)
    return {"deleted": node_id}


@_tool(
    name="mnemo_remove_source",
    risk=RISK_DANGER,
    description="Unregister a source + cascade-clean its nodes.",
    parameters=_obj({"path": {"type": "string"}}, ["path"]),
)
def _mnemo_remove_source(ctx: ToolContext, *, path: str) -> dict:
    rows = ctx.store.remove_source(path)
    return {"removed": path, "rows": rows}


@_tool(
    name="mnemo_purge_conversation",
    risk=RISK_DANGER,
    description="Wipe a chat conversation + all its messages.",
    parameters=_obj({"conv_id": {"type": "string"}}, ["conv_id"]),
)
def _mnemo_purge_conversation(ctx: ToolContext, *, conv_id: str) -> dict:
    ctx.store.purge_conversation(conv_id)
    return {"purged": conv_id}


@_tool(
    name="mnemo_change_settings",
    risk=RISK_DANGER,
    description="Mutate settings.json (scoring / retention / ...).",
    parameters=_obj({"patch": {"type": "object"}}, ["patch"]),
)
def _mnemo_change_settings(ctx: ToolContext, *, patch: dict) -> dict:
    config.update(patch)
    return {"ok": True, "applied": sorted(patch.keys())}


# --- UI-directive tools (confirm, client-side) --------------------------
#
# The daemon does NOT execute these (design S3/S11). The fn returns a
# ``_ui_action`` sentinel; the agent loop turns it into a ``ui_action``
# SSE event the chat UI dispatches, and feeds the model a 'dispatched'
# ack so it continues. They're still ``confirm`` risk -> permission-
# gated like any other mutating tool.


def _ui(action: str, args: dict) -> dict:
    return {"_ui_action": {"action": action, "args": args}}


@_tool(
    name="mnemo_navigate",
    risk=RISK_CONFIRM,
    description="Navigate the browser to a mnemo path (e.g. /graph).",
    parameters=_obj({"path": {"type": "string"}}, ["path"]),
)
def _mnemo_navigate(ctx: ToolContext, *, path: str) -> dict:
    return _ui("navigate", {"path": path})


@_tool(
    name="mnemo_select_node",
    risk=RISK_CONFIRM,
    description="Select/focus a node on the current Nebula or /code view.",
    parameters=_obj({"node_id": {"type": "string"}}, ["node_id"]),
)
def _mnemo_select_node(ctx: ToolContext, *, node_id: str) -> dict:
    return _ui("select_node", {"node_id": node_id})


@_tool(
    name="mnemo_set_filter",
    risk=RISK_CONFIRM,
    description="Apply a type / confidence / layout filter on the view.",
    parameters=_obj(
        {"filter_kind": {"type": "string"}, "value": {"type": "string"}},
        ["filter_kind", "value"],
    ),
)
def _mnemo_set_filter(ctx: ToolContext, *, filter_kind: str, value: str) -> dict:
    return _ui("set_filter", {"filter_kind": filter_kind, "value": value})


@_tool(
    name="mnemo_scroll_to",
    risk=RISK_CONFIRM,
    description="Scroll a DOM element (by CSS selector) into view.",
    parameters=_obj({"selector": {"type": "string"}}, ["selector"]),
)
def _mnemo_scroll_to(ctx: ToolContext, *, selector: str) -> dict:
    return _ui("scroll_to", {"selector": selector})


@_tool(
    name="mnemo_open_panel",
    risk=RISK_CONFIRM,
    description="Open a UI panel (detail side panel, search popover, ...).",
    parameters=_obj({"panel_id": {"type": "string"}}, ["panel_id"]),
)
def _mnemo_open_panel(ctx: ToolContext, *, panel_id: str) -> dict:
    return _ui("open_panel", {"panel_id": panel_id})


@_tool(
    name="mnemo_highlight_nodes",
    risk=RISK_CONFIRM,
    description=(
        "Highlight a SET of related nodes ON THE LIVE NEBULA GRAPH: "
        "the custom WebGL renderer (nebula-gl.js) spotlights them "
        "(full vivid color, labelled) and dims the rest to a cool "
        "field (never hidden), and the camera frames the set. Pair "
        "with mnemo_session_nodes to show 'what's related in this "
        "session'. This DOES light them up on the graph -- tell the "
        "user so (the v4.6 custom renderer's setHighlight() delivers "
        "it; the old cosmos highlight ceiling stays closed). The "
        "/graph page must be open for the user to see it."
    ),
    parameters=_obj(
        {"node_ids": {"type": "array", "items": {"type": "string"}}},
        ["node_ids"],
    ),
)
def _mnemo_highlight_nodes(ctx: ToolContext, *, node_ids: list[str]) -> dict:
    return _ui("highlight_nodes", {"node_ids": node_ids})


# --- Skill tools (v3.1 phase 4) -----------------------------------------
#
# Skills are markdown workflow guides (skills/<name>/SKILL.md), NOT
# executable functions. ``mnemo_list_skills`` is a plain safe read;
# ``mnemo_run_skill`` returns a ``{"_skill": ...}`` sentinel the agent
# loop turns into a pinned guidance turn for the rest of the run (the
# same "read it and follow it" model the IDE uses). Deeper end-to-end
# autonomous skill *templates* are design S8 -> deferred to v3.2.


def _skills_root() -> Path:
    """The shipped skills dir. ``MNEMO_SKILLS_DIR`` overrides (tests).
    Otherwise package-relative -- mnemo/agent_tools.py -> mnemo ->
    daemon -> repo root, which holds ``skills/`` both in-repo and as an
    installed Claude Code plugin."""
    env = os.environ.get("MNEMO_SKILLS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "skills"


def _read_skill(skill_dir: Path) -> tuple[dict, str]:
    fm, body = _md_parser.parse((skill_dir / "SKILL.md").read_bytes(), skill_dir)
    return fm, body


@_tool(
    name="mnemo_list_skills",
    risk=RISK_SAFE,
    description=(
        "List the mnemo workflow skills available to load. Returns each "
        "skill's name + description. Call this first, then "
        "mnemo_run_skill to load one's guidance."
    ),
    parameters=_obj({}, []),
)
def _mnemo_list_skills(ctx: ToolContext) -> dict:
    root = _skills_root()
    skills: list[dict] = []
    if root.is_dir():
        for skill_md in sorted(root.glob("*/SKILL.md")):
            try:
                fm, _ = _read_skill(skill_md.parent)
            except Exception:  # a malformed skill must not break listing
                fm = {}
            name = str(fm.get("name") or skill_md.parent.name)
            skills.append({"name": name, "description": str(fm.get("description") or "")})
    return {"skills": skills}


@_tool(
    name="mnemo_run_skill",
    risk=RISK_CONFIRM,
    description=(
        "Load a mnemo skill's guidance into the conversation and follow "
        "it for the rest of this turn. ``skill_name`` is the skill's "
        "directory name (or its frontmatter name)."
    ),
    parameters=_obj({"skill_name": {"type": "string"}}, ["skill_name"]),
)
def _mnemo_run_skill(ctx: ToolContext, *, skill_name: str) -> dict:
    root = _skills_root()
    candidate = root / skill_name
    if not (candidate / "SKILL.md").is_file():
        # tolerate a frontmatter-name match (dir name may differ)
        candidate = None  # type: ignore[assignment]
        if root.is_dir():
            for skill_md in sorted(root.glob("*/SKILL.md")):
                try:
                    fm, _ = _read_skill(skill_md.parent)
                except Exception:
                    continue
                if str(fm.get("name") or "") == skill_name:
                    candidate = skill_md.parent
                    break
        if candidate is None:
            return {"error": f"unknown skill: {skill_name!r}"}
    fm, body = _read_skill(candidate)
    name = str(fm.get("name") or candidate.name)
    return {"_skill": {"name": name, "guidance": body.strip()}}


# --- Knowledge auditor (v5.12.0) ---------------------------------------


@_tool(
    name="mnemo_analyze",
    risk=RISK_SAFE,
    description=(
        "Audit the mnemo knowledge graph for structural issues. "
        "Deterministic detectors (no LLM): stale (nodes marked "
        "SUPERSEDED), duplicates (within-type pairs above cosine 0.95), "
        "orphan_references (citations to deleted nodes). Opt-in "
        "LLM-augmented detectors: contradictions (v5.13.0; within-type "
        "pairs in cosine 0.5-0.85 band with a negation differential) "
        "and semantic_orphans (v5.14.0; per-node concept extraction -- "
        "CamelCase/snake_case/ALL_CAPS -- whose terms aren't defined in "
        "any other node's name or description). Both default to "
        "severity 'candidate', elevated to 'high' when "
        "``MNEMO_ANALYZE_LLM_JUDGE=1`` + ``ANTHROPIC_API_KEY`` are set "
        "and Claude confirms. Set ``propose_actions=true`` (v5.15.0) to "
        "attach an LLM-proposed concrete refactor action (merge / "
        "supersede / delete / create_definition / fix_citation) to "
        "each high/medium finding -- requires "
        "``MNEMO_ANALYZE_PROPOSE_ACTIONS=1`` + key; actions are "
        "proposals the user reviews, never auto-applied. Pass "
        '``lens="code"`` (v5.16.0) to run a DOMAIN lens instead of '
        "the agnostic detectors: the code lens surfaces ``dead_code`` "
        "(private, uncalled functions/methods via the call graph; "
        "LLM-judged when the opt-in flag is set) and ``god_object`` "
        "(v5.17.0; oversized classes/modules by exact method/definition "
        "count -- deterministic, and v5.18.0 LLM-judged for cohesion "
        "when ``MNEMO_ANALYZE_LLM_JUDGE`` is set: a grab-bag becomes "
        "'high', a cohesive facade is dropped) and ``cyclic_imports`` "
        "(v5.19.0; module import cycles via Tarjan SCC over the imports "
        "graph -- deterministic, severity medium). Returns "
        "``{ran_at, node_count_scanned, findings, summary}``. Optional "
        "``types`` filter restricts detectors. Phase 1 + 2 + 3 of "
        "mnemo's Understanding arc."
    ),
    parameters=_obj(
        {
            "types": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional filter: subset of the active suite. "
                    'Agnostic (no lens): ["stale", "duplicates", '
                    '"orphan_references", "contradictions", '
                    '"semantic_orphans"]. With lens="code": '
                    '["dead_code", "god_object", "cyclic_imports"].'
                ),
            },
            "project_key": {
                "type": ["string", "null"],
                "default": None,
                "description": "Reserved for future scoping (currently no-op).",
            },
            "propose_actions": {
                "type": ["boolean", "null"],
                "default": None,
                "description": (
                    "v5.15.0: opt-in refactor_actions enrichment. true "
                    "attaches an LLM-proposed action to each high/medium "
                    "finding (requires MNEMO_ANALYZE_PROPOSE_ACTIONS=1 + "
                    "ANTHROPIC_API_KEY); null/omit = off."
                ),
            },
            "lens": {
                "type": ["string", "null"],
                "default": None,
                "description": (
                    "v5.16.0: optional domain lens. null = agnostic "
                    'detectors. "code" = the code lens (dead_code + '
                    "god_object + cyclic_imports). A lens REPLACES the "
                    "agnostic suite; unknown lens runs nothing."
                ),
            },
        },
        [],
    ),
)
def _mnemo_analyze(
    ctx: ToolContext,
    *,
    types: list[str] | None = None,
    project_key: str | None = None,
    propose_actions: bool | None = None,
    lens: str | None = None,
) -> dict:
    from mnemo import analyzer

    return analyzer.analyze(
        ctx.store,
        embedder=ctx.embedder,
        types=types,
        project_key=project_key,
        propose_actions=propose_actions,
        lens=lens,
    )
