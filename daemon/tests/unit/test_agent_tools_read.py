"""v3 phase 1: the 6 safe READ tools + the TOOLS registry.

``agent_tools.TOOLS`` is the ONE source of truth consumed by both the
internal agent loop (phase 2) and the MCP server (phase 6). Phase 1
ships only the six ``safe`` (no-side-effect, auto-run, never-prompted)
read tools. Write / exec / danger tools + the permission system land in
phase 4.

Contracts pinned here: registry shape (names, risk tag, JSON-schema
params), and each tool's input -> JSON-serialisable output on a seeded
tmp store. ``mnemo_query`` is shape-only (deep retrieval correctness is
retrieve.py's own test surface; here we only lock the wrapper envelope
+ that it never raises).
"""

from __future__ import annotations

import json

from mnemo.agent_tools import TOOLS, ToolContext
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder

SAFE_READ_TOOLS = {
    "mnemo_query",
    "mnemo_get_node",
    "mnemo_get_edges",
    "mnemo_traverse",
    "mnemo_search_by_type",
    "mnemo_get_code_lines",
}


def _node(store: Store, nid: str, *, ntype: str = "memory_feedback", **kw) -> Node:
    n = Node(
        id=nid,
        type=ntype,
        name=kw.get("name", nid),
        description=kw.get("description"),
        body=kw.get("body", f"body of {nid}"),
        source_path=kw.get("source_path", f"/mem/{nid}.md"),
        source_kind=kw.get("source_kind", "memory_dir"),
        project_key=kw.get("project_key"),
        frontmatter_json=kw.get("frontmatter_json"),
        hash="h",
        created_at=1,
        updated_at=1,
        base=kw.get("base", False),
    )
    store.upsert_node(n)
    return n


def _ctx(store: Store) -> ToolContext:
    return ToolContext(store=store, embedder=FakeEmbedder())


# --- registry -----------------------------------------------------------


def test_registry_exposes_exactly_the_six_safe_read_tools() -> None:
    assert set(TOOLS) >= SAFE_READ_TOOLS, "all 6 read tools must be registered"
    for name in SAFE_READ_TOOLS:
        spec = TOOLS[name]
        assert spec.risk == "safe", f"{name} must be a safe tool"
        assert spec.name == name
        assert spec.description.strip()
        assert spec.parameters.get("type") == "object"
        assert isinstance(spec.parameters.get("properties"), dict)
        assert callable(spec.fn)


def test_only_safe_tools_in_phase1() -> None:
    """Phase 1 ships read-only. No confirm/danger tools yet (phase 4)."""
    assert {s.risk for s in TOOLS.values()} == {"safe"}


def test_every_tool_output_is_json_serialisable(store: Store) -> None:
    _node(store, "n1")
    ctx = _ctx(store)
    kwargs_by_tool = {
        "mnemo_query": {"prompt": "anything"},
        "mnemo_get_node": {"node_id": "n1"},
        "mnemo_get_edges": {"node_id": "n1"},
        "mnemo_traverse": {"start_id": "n1"},
        "mnemo_search_by_type": {"type": "memory_feedback"},
        "mnemo_get_code_lines": {"source_path": "/mem/n1.md", "start": 1, "end": 1},
    }
    for name in SAFE_READ_TOOLS:
        out = TOOLS[name].fn(ctx, **kwargs_by_tool[name])
        json.dumps(out)  # must not raise
        assert isinstance(out, dict)


# --- mnemo_get_node -----------------------------------------------------


def test_get_node_returns_full_record(store: Store) -> None:
    _node(
        store,
        "n1",
        name="MQTT auth",
        description="how broker auth flakes",
        body="full body text",
        project_key="P1",
        frontmatter_json=json.dumps({"type": "feedback", "base": False}),
    )
    out = TOOLS["mnemo_get_node"].fn(_ctx(store), node_id="n1")
    assert out["node_id"] == "n1"
    assert out["name"] == "MQTT auth"
    assert out["body"] == "full body text"
    assert out["project_key"] == "P1"
    assert out["frontmatter"] == {"type": "feedback", "base": False}


def test_get_node_missing_returns_error_not_exception(store: Store) -> None:
    out = TOOLS["mnemo_get_node"].fn(_ctx(store), node_id="ghost")
    assert "error" in out
    assert out["node_id"] == "ghost"


# --- mnemo_get_edges ----------------------------------------------------


def test_get_edges_direction_and_relation_filter(store: Store) -> None:
    _node(store, "a")
    _node(store, "b")
    _node(store, "c")
    store.add_edge("a", "b", "mentions")
    store.add_edge("c", "a", "supersedes")
    ctx = _ctx(store)

    both = TOOLS["mnemo_get_edges"].fn(ctx, node_id="a")
    assert {(e["src"], e["dst"], e["relation"]) for e in both["edges"]} == {
        ("a", "b", "mentions"),
        ("c", "a", "supersedes"),
    }

    out_only = TOOLS["mnemo_get_edges"].fn(ctx, node_id="a", direction="out")
    assert {e["dst"] for e in out_only["edges"]} == {"b"}

    in_only = TOOLS["mnemo_get_edges"].fn(ctx, node_id="a", direction="in")
    assert {e["src"] for e in in_only["edges"]} == {"c"}

    rel = TOOLS["mnemo_get_edges"].fn(ctx, node_id="a", relation="mentions")
    assert all(e["relation"] == "mentions" for e in rel["edges"])
    assert len(rel["edges"]) == 1


# --- mnemo_traverse -----------------------------------------------------


def test_traverse_bfs_respects_max_hops(store: Store) -> None:
    for nid in ("a", "b", "c", "d"):
        _node(store, nid)
    store.add_edge("a", "b", "mentions")
    store.add_edge("b", "c", "mentions")
    store.add_edge("c", "d", "mentions")
    ctx = _ctx(store)

    one = TOOLS["mnemo_traverse"].fn(ctx, start_id="a", max_hops=1)
    assert {n["node_id"] for n in one["nodes"]} == {"a", "b"}

    two = TOOLS["mnemo_traverse"].fn(ctx, start_id="a", max_hops=2)
    assert {n["node_id"] for n in two["nodes"]} == {"a", "b", "c"}

    hop_of = {n["node_id"]: n["hop"] for n in two["nodes"]}
    assert hop_of["a"] == 0
    assert hop_of["b"] == 1
    assert hop_of["c"] == 2


def test_traverse_missing_start_errors(store: Store) -> None:
    out = TOOLS["mnemo_traverse"].fn(_ctx(store), start_id="ghost")
    assert "error" in out


# --- mnemo_search_by_type -----------------------------------------------


def test_search_by_type_with_name_glob(store: Store) -> None:
    _node(store, "f1", ntype="memory_feedback", name="mqtt-auth-flake")
    _node(store, "f2", ntype="memory_feedback", name="mqtt-tls-handshake")
    _node(store, "p1", ntype="memory_project", name="project-overview")
    ctx = _ctx(store)

    allf = TOOLS["mnemo_search_by_type"].fn(ctx, type="memory_feedback")
    assert allf["count"] == 2

    globbed = TOOLS["mnemo_search_by_type"].fn(ctx, type="memory_feedback", name_glob="mqtt-auth*")
    assert {n["node_id"] for n in globbed["nodes"]} == {"f1"}


def test_search_by_unknown_type_errors(store: Store) -> None:
    out = TOOLS["mnemo_search_by_type"].fn(_ctx(store), type="not_a_type")
    assert "error" in out


# --- mnemo_get_code_lines -----------------------------------------------


def test_get_code_lines_reads_slice(tmp_path, store: Store) -> None:
    f = tmp_path / "mod.py"
    f.write_text("\n".join(f"line {i}" for i in range(1, 21)), encoding="utf-8")
    out = TOOLS["mnemo_get_code_lines"].fn(_ctx(store), source_path=str(f), start=3, end=5)
    assert out["start"] == 3
    assert out["end"] == 5
    assert out["lines"] == "line 3\nline 4\nline 5"


def test_get_code_lines_missing_file_errors(store: Store) -> None:
    out = TOOLS["mnemo_get_code_lines"].fn(
        _ctx(store), source_path="/no/such/file.py", start=1, end=10
    )
    assert "error" in out


# --- mnemo_query (shape only) -------------------------------------------


def test_query_returns_documented_envelope(store: Store) -> None:
    out = TOOLS["mnemo_query"].fn(_ctx(store), prompt="how do we handle MQTT auth")
    assert set(out) >= {"hits", "intent_tags", "tokens_used", "query_id"}
    assert isinstance(out["hits"], list)
    assert out["hits"] == []  # empty store -> no hits, no exception
