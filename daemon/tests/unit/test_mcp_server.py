"""v3 phase 6: MCP server wraps the SAME tool surface (design S6).

The dispatch core (``tool_list`` / ``call_tool``) is SDK-independent
so it's tested deterministically here; the thin mcp-package wiring
(``build_server``) is smoke-checked and live-verified in phase 12.
"""

from __future__ import annotations

from mnemo import mcp_server
from mnemo.store import Node, Store


def _seed(store: Store, nid: str) -> None:
    store.upsert_node(
        Node(
            id=nid,
            type="memory_feedback",
            name=nid,
            description=None,
            body="b",
            source_path=f"/m/{nid}.md",
            source_kind="memory_dir",
            project_key=None,
            frontmatter_json=None,
            hash="h",
            created_at=1,
            updated_at=1,
        )
    )


def test_tool_list_mirrors_the_registry() -> None:
    tl = mcp_server.tool_list()
    names = {t["name"] for t in tl}
    assert {"mnemo_query", "mnemo_get_node", "mnemo_delete_node"} <= names
    by_name = {t["name"]: t for t in tl}
    assert by_name["mnemo_query"]["risk"] == "safe"
    assert by_name["mnemo_delete_node"]["risk"] == "danger"
    for t in tl:
        assert t["description"].strip()
        assert t["inputSchema"]["type"] == "object"


def test_call_tool_dispatches(store: Store) -> None:
    _seed(store, "n1")
    ctx = mcp_server.ToolContext(store=store, embedder=None)
    out = mcp_server.call_tool("mnemo_get_node", {"node_id": "n1"}, ctx)
    assert out["node_id"] == "n1"


def test_call_unknown_tool_is_error_not_raise(store: Store) -> None:
    ctx = mcp_server.ToolContext(store=store, embedder=None)
    out = mcp_server.call_tool("nope", {}, ctx)
    assert "error" in out


def test_build_server_smoke() -> None:
    srv = mcp_server.build_server()
    assert srv is not None
