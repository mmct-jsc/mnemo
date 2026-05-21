"""v5 phase 3: ``use_skill`` field on ``POST /v1/chat/<id>/message``.

The dock surface (phase 4) POSTs with ``use_skill="mnemo-prompt-architect"``
so the agent is primed with the skill's guidance BEFORE the model
sees the user's text. Without this entry point, every dock turn
would either need a system-prompt round-trip or trust the model to
notice the architect intent on its own.

Tests:

- ``MessageCreateIn`` accepts the optional field and validates it.
- The agent loop pulls the skill via the existing ``mnemo_run_skill``
  pathway and emits ``skill_loaded`` BEFORE the first text_delta.
- The skill guidance is persisted as a message turn in the
  conversation history.
- Omitting the field is fully backward-compatible -- the run starts
  exactly as it did pre-v5.
- An unknown skill name is reported as an error event, never
  silently dropped.
"""

from __future__ import annotations

from collections.abc import Iterator

from mnemo import chat
from mnemo.api_schemas import MessageCreateIn
from mnemo.providers import EV_STOP, EV_TEXT, BaseProvider
from mnemo.store import Store

# --- Schema contract -------------------------------------------------------


def test_message_create_in_accepts_use_skill() -> None:
    m = MessageCreateIn(text="hi", use_skill="mnemo-prompt-architect")
    assert m.text == "hi"
    assert m.use_skill == "mnemo-prompt-architect"


def test_message_create_in_use_skill_optional() -> None:
    m = MessageCreateIn(text="hi")
    assert m.use_skill is None


# --- Agent-loop entry-point contract --------------------------------------


class _ScriptedProvider(BaseProvider):
    name = "fake"

    def __init__(self, script: list[list[tuple]]) -> None:
        self._script = script
        self._i = 0
        self.seen: list[list[dict]] = []

    def stream(
        self,
        messages,
        tools,
        *,
        model,
        system=None,
        max_output_tokens=4096,
        **kwargs,
    ) -> Iterator[tuple]:
        # Capture the messages so the test can assert the skill guidance
        # is in the model's view BEFORE its first text_delta.
        self.seen.append([dict(m) for m in messages])
        evs = self._script[self._i] if self._i < len(self._script) else [(EV_STOP, "end_turn")]
        self._i += 1
        yield from evs


def _seed_conv(store: Store) -> str:
    conv = store.create_conversation(
        name="dock",
        project_key=None,
        page_context=None,
        provider="fake",
        model="m",
    )
    return conv.id


def test_use_skill_injects_guidance_before_user_text(store: Store) -> None:
    """The skill body must be in the message history BEFORE the model
    sees the user's text -- otherwise the architected prompt won't
    use the skill's analysis pattern on the first turn."""
    conv_id = _seed_conv(store)
    provider = _ScriptedProvider([[(EV_TEXT, "ok"), (EV_STOP, "end_turn")]])
    loop = chat.AgentLoop(store, provider, embedder=None, model="m", project_key=None)
    events = list(loop.run(conv_id, "fix MQTT auth bug", use_skill="mnemo-prompt-architect"))

    # Must have emitted skill_loaded.
    event_types = [e["type"] for e in events]
    assert "skill_loaded" in event_types, f"expected skill_loaded before text; got {event_types}"
    # The model's view on its first call must include both the skill
    # guidance and the user text. The skill comes first.
    first_view = provider.seen[0]
    roles_and_text = [(m["role"], str(m.get("content", ""))[:60]) for m in first_view]
    contents = " ".join(text for _, text in roles_and_text)
    assert "active skill" in contents.lower() or "mnemo-prompt-architect" in contents
    assert "fix MQTT auth bug" in " ".join(str(m.get("content", "")) for m in first_view)


def test_use_skill_persists_guidance_in_history(store: Store) -> None:
    """A user navigating to the conversation later must still see
    the skill-loaded marker (otherwise re-opening the dock loses
    context)."""
    conv_id = _seed_conv(store)
    provider = _ScriptedProvider([[(EV_TEXT, "ok"), (EV_STOP, "end_turn")]])
    loop = chat.AgentLoop(store, provider, embedder=None, model="m", project_key=None)
    list(loop.run(conv_id, "fix the bug", use_skill="mnemo-prompt-architect"))

    msgs = store.list_messages(conv_id)
    # The skill guidance is appended as a user-role marker before the
    # user's actual text.
    user_msgs = [m for m in msgs if m.role == "user"]
    assert len(user_msgs) >= 2, "expected skill-pin + user-text user-role turns"
    skill_pin = user_msgs[0]
    assert "mnemo-prompt-architect" in str(skill_pin.content), (
        "first user-role turn should be the skill pin"
    )


def test_use_skill_omitted_is_backward_compatible(store: Store) -> None:
    """No use_skill -> behaves exactly like pre-v5: no skill_loaded
    event, history starts with the user turn alone."""
    conv_id = _seed_conv(store)
    provider = _ScriptedProvider([[(EV_TEXT, "ok"), (EV_STOP, "end_turn")]])
    loop = chat.AgentLoop(store, provider, embedder=None, model="m", project_key=None)
    events = list(loop.run(conv_id, "fix the bug"))
    event_types = [e["type"] for e in events]
    assert "skill_loaded" not in event_types
    msgs = store.list_messages(conv_id)
    user_msgs = [m for m in msgs if m.role == "user"]
    # Exactly one user message -- the raw text.
    assert len(user_msgs) == 1
    assert "fix the bug" in str(user_msgs[0].content)


def test_use_skill_unknown_name_emits_error(store: Store) -> None:
    """A typo in use_skill must surface; don't silently drop and run
    the loop without the requested guidance."""
    conv_id = _seed_conv(store)
    provider = _ScriptedProvider([[(EV_TEXT, "ok"), (EV_STOP, "end_turn")]])
    loop = chat.AgentLoop(store, provider, embedder=None, model="m", project_key=None)
    events = list(loop.run(conv_id, "fix the bug", use_skill="does-not-exist"))
    event_types = [e["type"] for e in events]
    # No skill_loaded, but an error event surfaces.
    assert "skill_loaded" not in event_types
    error_evs = [e for e in events if e["type"] == "error"]
    assert error_evs, f"expected error event for unknown skill; got {event_types}"
    assert "does-not-exist" in str(error_evs[0])
