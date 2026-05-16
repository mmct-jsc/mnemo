"""v3 phase 6: expose mnemo's tool surface over MCP.

Same ``agent_tools.TOOLS`` registry, second consumer (design S6) -- an
external MCP client (Cursor / Claude Desktop / Codex / Windsurf) gets
mnemo retrieval + the write/danger tools for free, with the risk tag
surfaced in each tool's description so the host can gate them.

The dispatch core (:func:`tool_list`, :func:`call_tool`) has NO
dependency on the ``mcp`` package so it's unit-testable and robust to
SDK churn; :func:`build_server` / :func:`serve_stdio` are the thin,
lazily-imported wiring.
"""

from __future__ import annotations

import json
from typing import Any

from mnemo import paths
from mnemo.agent_tools import TOOLS, ToolContext

__all__ = ["ToolContext", "tool_list", "call_tool", "build_server", "serve_stdio"]


def tool_list() -> list[dict]:
    """Registry -> MCP tool descriptors (risk folded into description)."""
    out: list[dict] = []
    for spec in TOOLS.values():
        out.append(
            {
                "name": spec.name,
                "description": f"{spec.description} (risk: {spec.risk})",
                "inputSchema": spec.parameters,
                "risk": spec.risk,
            }
        )
    return out


def call_tool(name: str, arguments: dict, ctx: ToolContext) -> dict:
    """Dispatch a tool by name. Never raises -- unknown / failing tools
    come back as an ``{"error": ...}`` dict (same contract as the agent
    loop)."""
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": f"unknown tool: {name!r}"}
    return spec.fn(ctx, **(arguments or {}))


def make_context() -> ToolContext:
    """Production context: the daemon's own SQLite store + embedder."""
    from mnemo.embed import Embedder
    from mnemo.store import Store

    paths.ensure_runtime_dirs()
    return ToolContext(store=Store(paths.db_path()), embedder=Embedder())


def build_server(ctx: ToolContext | None = None) -> Any:
    """Build a low-level ``mcp`` Server bound to the tool surface.

    Lazily imports ``mcp`` so the dispatch core stays importable
    without the SDK. The context is built on first call if not given.
    """
    import mcp.types as types
    from mcp.server import Server

    server = Server("mnemo")
    _ctx_holder: dict[str, ToolContext] = {}

    def _ctx() -> ToolContext:
        if ctx is not None:
            return ctx
        if "c" not in _ctx_holder:
            _ctx_holder["c"] = make_context()
        return _ctx_holder["c"]

    @server.list_tools()
    async def _list() -> list:
        return [
            types.Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in tool_list()
        ]

    @server.call_tool()
    async def _call(name: str, arguments: dict | None) -> list:
        result = call_tool(name, arguments or {}, _ctx())
        return [types.TextContent(type="text", text=json.dumps(result))]

    return server


def serve_stdio() -> None:  # pragma: no cover -- exercised by phase-12 live smoke
    """Run the MCP server over stdio (the transport Cursor / Claude
    Desktop / Codex / Windsurf use)."""
    import anyio
    from mcp.server.stdio import stdio_server

    server = build_server()

    async def _main() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(_main)
