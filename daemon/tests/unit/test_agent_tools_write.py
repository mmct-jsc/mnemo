"""v3 phase 4: write/exec + danger tools and their risk tags (design S3).

Read tools stay ``safe``; mutating tools are ``confirm``; destructive
ones are ``danger``. Bodies have real store effects so the contract is
meaningful.
"""

from __future__ import annotations

from mnemo.agent_tools import TOOLS, ToolContext
from mnemo.store import Node, Store

CONFIRM = {
    "mnemo_create_node",
    "mnemo_update_node",
    "mnemo_thumbs_feedback",
    "mnemo_add_source",
    "mnemo_reindex_source",
}
DANGER = {
    "mnemo_delete_node",
    "mnemo_remove_source",
    "mnemo_purge_conversation",
    "mnemo_change_settings",
}


def _ctx(store: Store) -> ToolContext:
    return ToolContext(store=store, embedder=None)


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


def test_risk_tags_match_design() -> None:
    for n in CONFIRM:
        assert TOOLS[n].risk == "confirm", n
    for n in DANGER:
        assert TOOLS[n].risk == "danger", n
    # the 6 phase-1 read tools are still safe
    assert TOOLS["mnemo_query"].risk == "safe"


def test_create_node_writes_and_is_retrievable(store: Store) -> None:
    out = TOOLS["mnemo_create_node"].fn(
        _ctx(store),
        type="memory_feedback",
        name="new-lesson",
        body="learned a thing",
        project_key="P1",
    )
    assert "node_id" in out
    n = store.get_node(out["node_id"])
    assert n is not None
    assert n.name == "new-lesson"
    assert n.project_key == "P1"


def test_update_node_patches_fields(store: Store) -> None:
    _seed(store, "n1")
    out = TOOLS["mnemo_update_node"].fn(
        _ctx(store), node_id="n1", fields={"name": "renamed", "body": "new body"}
    )
    assert out["node_id"] == "n1"
    n = store.get_node("n1")
    assert n.name == "renamed"
    assert n.body == "new body"


def test_delete_node_removes_it(store: Store) -> None:
    _seed(store, "n1")
    out = TOOLS["mnemo_delete_node"].fn(_ctx(store), node_id="n1")
    assert out["deleted"] == "n1"
    assert store.get_node("n1") is None


def test_purge_conversation_tool(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="anthropic", model="m")
    store.append_message(conv.id, role="user", content={"text": "hi"})
    out = TOOLS["mnemo_purge_conversation"].fn(_ctx(store), conv_id=conv.id)
    assert out["purged"] == conv.id
    assert store.get_conversation(conv.id) is None


def test_thumbs_feedback_unknown_node_is_error_not_raise(store: Store) -> None:
    out = TOOLS["mnemo_thumbs_feedback"].fn(_ctx(store), node_id="ghost", direction="up")
    assert isinstance(out, dict)  # never raises -- error dict at worst


def test_change_settings_updates_config(store: Store, isolated_mnemo_home) -> None:
    out = TOOLS["mnemo_change_settings"].fn(_ctx(store), patch={"recency_half_life_days": 42.0})
    assert out.get("ok") is True
