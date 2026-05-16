"""v3 phase 4: the permission protocol in the agent loop (design S4).

A ``safe`` tool auto-runs. A ``confirm``/``danger`` tool emits a
``permission_request`` event and consults the injected ``permission_cb``
unless an always-allow grant already exists in ``chat_permissions``.
deny -> synthetic error tool_result; allow_once -> run;
allow_always -> persist the grant + run. A persisted grant
short-circuits the prompt next time.
"""

from __future__ import annotations

from mnemo.chat import AgentLoop
from mnemo.providers import EV_STOP, EV_TEXT, EV_TOOL_CALL, BaseProvider
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder


class FakeProvider(BaseProvider):
    name = "fake"

    def __init__(self, script):
        self._s = script
        self._i = 0

    def stream(self, messages, tools, *, model, system=None, max_output_tokens=4096):
        i = self._i
        self._i += 1
        yield from (self._s[i] if i < len(self._s) else [(EV_STOP, "end_turn")])


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


def _provider_calls_delete():
    return FakeProvider(
        [
            [
                (
                    EV_TOOL_CALL,
                    {"id": "t1", "name": "mnemo_delete_node", "args": {"node_id": "n1"}},
                ),
                (EV_STOP, "tool_use"),
            ],
            [(EV_TEXT, "ok done"), (EV_STOP, "end_turn")],
        ]
    )


def _loop(store, provider, cb=None, project_key=None):
    return AgentLoop(
        store,
        provider,
        embedder=FakeEmbedder(),
        model="m",
        system="S",
        project_key=project_key,
        permission_cb=cb,
    )


def test_deny_blocks_the_tool(store: Store) -> None:
    _seed(store, "n1")
    conv = store.create_conversation(name="c", provider="fake", model="m")
    events = list(
        _loop(store, _provider_calls_delete(), cb=lambda req: "deny").run(conv.id, "delete n1")
    )
    pr = next(e for e in events if e["type"] == "permission_request")
    assert pr["tool_name"] == "mnemo_delete_node"
    assert pr["risk"] == "danger"
    assert pr["auto_grant_options"] == ["once"]  # danger: no "always"
    assert store.get_node("n1") is not None  # NOT deleted


def test_allow_once_runs_but_does_not_persist(store: Store) -> None:
    _seed(store, "n1")
    conv = store.create_conversation(name="c", provider="fake", model="m")
    list(
        _loop(store, _provider_calls_delete(), cb=lambda req: "allow_once").run(
            conv.id, "delete n1"
        )
    )
    assert store.get_node("n1") is None
    assert store.list_permissions() == []  # not persisted


def test_allow_always_persists_grant(store: Store) -> None:
    _seed(store, "n1")
    conv = store.create_conversation(name="c", provider="fake", model="m", project_key="P1")
    calls = []
    list(
        _loop(
            store,
            FakeProvider(
                [
                    [
                        (
                            EV_TOOL_CALL,
                            {
                                "id": "t1",
                                "name": "mnemo_create_node",
                                "args": {"type": "memory_feedback", "name": "x", "body": "y"},
                            },
                        ),
                        (EV_STOP, "tool_use"),
                    ],
                    [(EV_STOP, "end_turn")],
                ]
            ),
            cb=lambda req: calls.append(req) or "allow_always",
            project_key="P1",
        ).run(conv.id, "make a note")
    )
    perms = store.list_permissions()
    assert any(p.tool_name == "mnemo_create_node" and p.project_key == "P1" for p in perms)


def test_existing_grant_short_circuits_prompt(store: Store) -> None:
    _seed(store, "n1")
    store.grant_permission(project_key=None, tool_name="mnemo_delete_node")
    conv = store.create_conversation(name="c", provider="fake", model="m")

    def cb(req):
        raise AssertionError("permission_cb must not be called when granted")

    events = list(_loop(store, _provider_calls_delete(), cb=cb).run(conv.id, "delete n1"))
    assert store.get_node("n1") is None  # ran without prompting
    assert not any(e["type"] == "permission_request" for e in events)


def test_safe_tool_never_prompts(store: Store) -> None:
    _seed(store, "n1")
    conv = store.create_conversation(name="c", provider="fake", model="m")
    prov = FakeProvider(
        [
            [
                (EV_TOOL_CALL, {"id": "t1", "name": "mnemo_get_node", "args": {"node_id": "n1"}}),
                (EV_STOP, "tool_use"),
            ],
            [(EV_STOP, "end_turn")],
        ]
    )
    events = list(_loop(store, prov, cb=lambda req: "deny").run(conv.id, "read n1"))
    assert not any(e["type"] == "permission_request" for e in events)
