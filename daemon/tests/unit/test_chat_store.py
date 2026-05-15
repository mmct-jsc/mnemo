"""v3 phase 1: chat persistence contract.

Conversations / messages / permissions are first-class SQLite rows
(design doc 2026-05-14-mnemo-v3-design.md S5). The schema lands in the
always-run ``SCHEMA_SQL`` so a pre-v3 DB grows the tables on first open
(idempotent ``executescript``), exactly like every prior phase.

These tests pin the Store CRUD surface the agent loop (phase 2) and the
REST layer (phase 3) build on. No provider / network here -- pure
storage.
"""

from __future__ import annotations

import time

from mnemo.store import Store

# --- conversations ------------------------------------------------------


def test_create_then_get_conversation_roundtrips(store: Store) -> None:
    conv = store.create_conversation(
        name="MQTT auth dig",
        provider="anthropic",
        model="claude-sonnet-4-5-20250929",
        project_key="D--Repository-edge-device",
        page_context={"page": "nebula", "selected_node_id": "abc"},
    )
    assert conv.id
    assert len(conv.id) == 32  # uuid4 hex
    assert conv.archived_at is None
    assert conv.created_at == conv.updated_at

    got = store.get_conversation(conv.id)
    assert got is not None
    assert got.name == "MQTT auth dig"
    assert got.provider == "anthropic"
    assert got.model == "claude-sonnet-4-5-20250929"
    assert got.project_key == "D--Repository-edge-device"
    assert got.page_context == {"page": "nebula", "selected_node_id": "abc"}


def test_get_missing_conversation_returns_none(store: Store) -> None:
    assert store.get_conversation("does-not-exist") is None


def test_list_conversations_filters_project_and_sorts_recent_first(
    store: Store,
) -> None:
    a = store.create_conversation(name="a", provider="anthropic", model="m", project_key="P1")
    time.sleep(0.01)
    b = store.create_conversation(name="b", provider="anthropic", model="m", project_key="P1")
    store.create_conversation(name="c", provider="anthropic", model="m", project_key="P2")

    p1 = store.list_conversations(project_key="P1")
    assert [c.id for c in p1] == [b.id, a.id]  # updated_at DESC

    everything = store.list_conversations()
    assert {c.name for c in everything} == {"a", "b", "c"}


def test_archived_conversation_hidden_by_default(store: Store) -> None:
    conv = store.create_conversation(name="x", provider="anthropic", model="m")
    store.archive_conversation(conv.id)

    assert store.get_conversation(conv.id) is not None  # still fetchable by id
    assert store.get_conversation(conv.id).archived_at is not None
    assert conv.id not in {c.id for c in store.list_conversations()}
    assert conv.id in {c.id for c in store.list_conversations(include_archived=True)}


def test_rename_conversation_patches_and_bumps_updated_at(store: Store) -> None:
    conv = store.create_conversation(name="old", provider="anthropic", model="m1")
    time.sleep(0.01)
    patched = store.rename_conversation(conv.id, name="new", provider="openai", model="gpt-4o-mini")
    assert patched is not None
    assert patched.name == "new"
    assert patched.provider == "openai"
    assert patched.model == "gpt-4o-mini"
    assert patched.updated_at >= conv.updated_at
    assert store.rename_conversation("nope", name="x") is None


# --- messages -----------------------------------------------------------


def test_append_message_assigns_monotonic_seq(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="anthropic", model="m")
    m0 = store.append_message(conv.id, role="user", content={"text": "hi"})
    m1 = store.append_message(conv.id, role="assistant", content={"text": "hello"})
    m2 = store.append_message(
        conv.id, role="tool_call", content={"tool_call": {"name": "mnemo_query"}}
    )
    assert [m0.seq, m1.seq, m2.seq] == [0, 1, 2]
    assert m0.conversation_id == conv.id


def test_list_messages_ordered_by_seq_and_parses_content(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="anthropic", model="m")
    store.append_message(conv.id, role="user", content={"text": "q"})
    store.append_message(
        conv.id, role="assistant", content={"text": "a", "citations": ["n1", "n2"]}
    )
    msgs = store.list_messages(conv.id)
    assert [m.seq for m in msgs] == [0, 1]
    assert msgs[0].role == "user"
    assert msgs[1].content == {"text": "a", "citations": ["n1", "n2"]}


def test_append_message_bumps_conversation_updated_at(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="anthropic", model="m")
    time.sleep(0.01)
    store.append_message(conv.id, role="user", content={"text": "hi"})
    refreshed = store.get_conversation(conv.id)
    assert refreshed.updated_at >= conv.created_at


def test_purge_conversation_cascades_messages(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="anthropic", model="m")
    store.append_message(conv.id, role="user", content={"text": "hi"})
    store.purge_conversation(conv.id)
    assert store.get_conversation(conv.id) is None
    assert store.list_messages(conv.id) == []  # FK ON DELETE CASCADE


# --- permissions --------------------------------------------------------


def test_permission_grant_list_revoke_roundtrip(store: Store) -> None:
    store.grant_permission(project_key="P1", tool_name="mnemo_create_node")
    perms = store.list_permissions()
    assert len(perms) == 1
    assert perms[0].project_key == "P1"
    assert perms[0].tool_name == "mnemo_create_node"
    assert perms[0].granted_at > 0

    store.revoke_permission(project_key="P1", tool_name="mnemo_create_node")
    assert store.list_permissions() == []


def test_permission_grant_is_idempotent(store: Store) -> None:
    store.grant_permission(project_key="P1", tool_name="mnemo_create_node")
    store.grant_permission(project_key="P1", tool_name="mnemo_create_node")
    assert len(store.list_permissions()) == 1


def test_is_permission_granted_project_scoped(store: Store) -> None:
    assert store.is_permission_granted(project_key="P1", tool_name="mnemo_create_node") is False
    store.grant_permission(project_key="P1", tool_name="mnemo_create_node")
    assert store.is_permission_granted(project_key="P1", tool_name="mnemo_create_node") is True
    # a grant for P1 does NOT leak to P2
    assert store.is_permission_granted(project_key="P2", tool_name="mnemo_create_node") is False


def test_global_permission_grants_every_project(store: Store) -> None:
    """A NULL project_key row = global allow-always (design S4)."""
    store.grant_permission(project_key=None, tool_name="mnemo_reindex_source")
    assert store.is_permission_granted(project_key=None, tool_name="mnemo_reindex_source") is True
    assert store.is_permission_granted(project_key="anything", tool_name="mnemo_reindex_source")


def test_schema_idempotent_on_reopen(tmp_path) -> None:
    """Reopening an existing DB must not error (SCHEMA_SQL re-runs)."""
    p = tmp_path / "mnemo.db"
    s1 = Store(p)
    conv = s1.create_conversation(name="c", provider="anthropic", model="m")
    s1.close()
    s2 = Store(p)  # SCHEMA_SQL executescript runs again -> must be idempotent
    assert s2.get_conversation(conv.id) is not None
    s2.close()


# ======================================================================
# v3.1 phase 1: bookmarks + token/summary columns + paginated history.
# Design: docs/plans/2026-05-15-mnemo-v3.1-companion-design.md S3.1.
# Pagination follows reference_mnemo_pagination.md (SQL LIMIT/OFFSET +
# scalar COUNT -- never load-all-then-slice).
# ======================================================================


def test_bookmark_add_list_delete_roundtrip(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="anthropic", model="m")
    store.append_message(conv.id, role="user", content={"text": "q"})
    store.append_message(conv.id, role="assistant", content={"text": "a"})
    bm = store.add_bookmark(conv.id, message_seq=1, label="the answer")
    assert bm.id
    assert bm.conversation_id == conv.id
    assert bm.message_seq == 1
    assert bm.label == "the answer"
    assert bm.created_at > 0

    listed = store.list_bookmarks(conv.id)
    assert len(listed) == 1
    assert listed[0].id == bm.id

    store.delete_bookmark(bm.id)
    assert store.list_bookmarks(conv.id) == []


def test_bookmarks_scoped_per_conversation_and_sorted_by_seq(store: Store) -> None:
    c1 = store.create_conversation(name="c1", provider="anthropic", model="m")
    c2 = store.create_conversation(name="c2", provider="anthropic", model="m")
    for i in range(4):
        store.append_message(c1.id, role="user", content={"text": str(i)})
    store.add_bookmark(c1.id, message_seq=3, label="late")
    store.add_bookmark(c1.id, message_seq=1, label="early")
    store.add_bookmark(c2.id, message_seq=0, label="other")
    rows = store.list_bookmarks(c1.id)
    assert [r.message_seq for r in rows] == [1, 3]  # ascending by seq
    assert len(store.list_bookmarks(c2.id)) == 1


def test_bookmarks_cascade_on_purge(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="anthropic", model="m")
    store.append_message(conv.id, role="user", content={"text": "q"})
    store.add_bookmark(conv.id, message_seq=0, label="x")
    store.purge_conversation(conv.id)
    assert store.list_bookmarks(conv.id) == []  # FK ON DELETE CASCADE


def test_append_message_records_token_usage(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="anthropic", model="m")
    store.append_message(conv.id, role="user", content={"text": "q"})
    store.append_message(
        conv.id,
        role="assistant",
        content={"text": "a"},
        token_in=1200,
        token_out=340,
        cache_read=900,
    )
    msgs = store.list_messages(conv.id)
    assert msgs[0].token_in is None  # legacy / unmeasured rows stay NULL
    assert msgs[1].token_in == 1200
    assert msgs[1].token_out == 340
    assert msgs[1].cache_read == 900


def test_bump_tokens_accumulates_on_conversation(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="anthropic", model="m")
    assert store.get_conversation(conv.id).tokens_total == 0  # default
    store.bump_tokens(conv.id, delta=1500)
    store.bump_tokens(conv.id, delta=420)
    assert store.get_conversation(conv.id).tokens_total == 1920


def test_list_messages_latest_window_limit(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="anthropic", model="m")
    for i in range(10):
        store.append_message(conv.id, role="user", content={"text": str(i)})
    window = store.list_messages(conv.id, limit=3)
    # last 3, returned oldest-first for prepend-free rendering
    assert [m.seq for m in window] == [7, 8, 9]
    # unbounded default is unchanged (all, ascending)
    assert [m.seq for m in store.list_messages(conv.id)] == list(range(10))


def test_list_messages_before_seq_paginates_older(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="anthropic", model="m")
    for i in range(10):
        store.append_message(conv.id, role="user", content={"text": str(i)})
    older = store.list_messages(conv.id, before_seq=7, limit=3)
    assert [m.seq for m in older] == [4, 5, 6]  # the 3 just before seq 7, ASC


def test_count_messages_is_scalar_total(store: Store) -> None:
    conv = store.create_conversation(name="c", provider="anthropic", model="m")
    for i in range(42):
        store.append_message(conv.id, role="user", content={"text": str(i)})
    assert store.count_messages(conv.id) == 42  # COUNT(*), not a capped scan


def test_v31_columns_and_bookmarks_idempotent_on_reopen(tmp_path) -> None:
    """A pre-v3.1 DB must grow the new columns + table on reopen."""
    p = tmp_path / "mnemo.db"
    s1 = Store(p)
    conv = s1.create_conversation(name="c", provider="anthropic", model="m")
    s1.append_message(conv.id, role="user", content={"text": "q"}, token_in=5)
    s1.add_bookmark(conv.id, message_seq=0, label="x")
    s1.bump_tokens(conv.id, delta=99)
    s1.close()
    s2 = Store(p)  # SCHEMA_SQL + _ensure_columns re-run -> idempotent
    assert s2.get_conversation(conv.id).tokens_total == 99
    assert s2.list_messages(conv.id)[0].token_in == 5
    assert len(s2.list_bookmarks(conv.id)) == 1
    s2.close()
