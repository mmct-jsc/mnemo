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
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mnemo import config, retrieve
from mnemo.parsers import md as _md_parser
from mnemo.store import NODE_TYPES, Node, Store, signal_for_reason

RISK_SAFE = "safe"
RISK_CONFIRM = "confirm"
RISK_DANGER = "danger"

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
    # phase 4 adds: project_key, conversation_id, ui_action sink.


@dataclass
class ToolSpec:
    name: str
    description: str
    risk: str
    parameters: dict  # JSON Schema (object) -- provider tool defs + MCP
    fn: Callable[..., dict]


TOOLS: dict[str, ToolSpec] = {}


def _register(spec: ToolSpec) -> ToolSpec:
    if spec.name in TOOLS:
        raise ValueError(f"duplicate tool registration: {spec.name}")
    if spec.risk not in (RISK_SAFE, RISK_CONFIRM, RISK_DANGER):
        raise ValueError(f"bad risk for {spec.name}: {spec.risk}")
    TOOLS[spec.name] = spec
    return spec


def _tool(
    *, name: str, risk: str, description: str, parameters: dict
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
        "each with a [mnemo:<id>] citation."
    ),
    parameters=_obj(
        {
            "prompt": {"type": "string", "description": "natural-language query"},
            "limit": {"type": "integer", "default": 8},
            "max_tokens": {"type": "integer", "default": 800},
            "project_key": {"type": ["string", "null"], "default": None},
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
) -> dict:
    res = retrieve.query(
        ctx.store,
        ctx.embedder,
        prompt,
        budget_tokens=max_tokens,
        k=limit,
        active_project=project_key,
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
