"""v3.1 phase 4: the agent can list + load mnemo skills.

Design 2026-05-15-mnemo-v3.1 S3.5. Skills are markdown workflow guides
(``skills/<name>/SKILL.md``: YAML frontmatter ``name``/``description``
+ a markdown body), NOT executable functions -- "running" one means
the agent reads it and follows it (the same model the IDE uses).

  * ``mnemo_list_skills``  -- safe; name + description from frontmatter.
  * ``mnemo_run_skill``    -- confirm; returns a ``{"_skill": ...}``
    sentinel the loop turns into a pinned guidance turn for the rest
    of the run + a 'skill_loaded' event.

Offline: a temp skills dir via the MNEMO_SKILLS_DIR override.
"""

from __future__ import annotations

from mnemo.agent_tools import RISK_CONFIRM, RISK_SAFE, TOOLS, ToolContext
from mnemo.chat import AgentLoop
from mnemo.providers import EV_STOP, EV_TEXT, EV_TOOL_CALL, BaseProvider
from mnemo.store import Store

_SKILL_A = """---
name: mnemo-demo
description: A demo skill used by the tests.
---

# mnemo:demo

Step 1. Do the thing.
Step 2. Cite [mnemo:n1].
"""

_SKILL_B = """---
name: mnemo-other
description: Another skill.
---

Body of other.
"""


def _seed_skills(tmp_path, monkeypatch) -> None:
    root = tmp_path / "skills"
    (root / "mnemo-demo").mkdir(parents=True)
    (root / "mnemo-other").mkdir(parents=True)
    (root / "mnemo-demo" / "SKILL.md").write_text(_SKILL_A, encoding="utf-8")
    (root / "mnemo-other" / "SKILL.md").write_text(_SKILL_B, encoding="utf-8")
    monkeypatch.setenv("MNEMO_SKILLS_DIR", str(root))


def _ctx(store: Store) -> ToolContext:
    return ToolContext(store=store)


# --- registry contract --------------------------------------------------


def test_skill_tools_registered_with_expected_risk() -> None:
    assert TOOLS["mnemo_list_skills"].risk == RISK_SAFE
    assert TOOLS["mnemo_run_skill"].risk == RISK_CONFIRM


def test_list_skills_reads_frontmatter(store: Store, tmp_path, monkeypatch) -> None:
    _seed_skills(tmp_path, monkeypatch)
    out = TOOLS["mnemo_list_skills"].fn(_ctx(store))
    by_name = {s["name"]: s for s in out["skills"]}
    assert set(by_name) == {"mnemo-demo", "mnemo-other"}
    assert by_name["mnemo-demo"]["description"] == "A demo skill used by the tests."


def test_run_skill_returns_sentinel_with_body(store: Store, tmp_path, monkeypatch) -> None:
    _seed_skills(tmp_path, monkeypatch)
    out = TOOLS["mnemo_run_skill"].fn(_ctx(store), skill_name="mnemo-demo")
    assert "_skill" in out
    assert out["_skill"]["name"] == "mnemo-demo"
    # the frontmatter is stripped; the markdown body is the guidance
    assert "Step 1. Do the thing." in out["_skill"]["guidance"]
    assert "description:" not in out["_skill"]["guidance"]


def test_run_unknown_skill_is_recoverable_error(store: Store, tmp_path, monkeypatch) -> None:
    _seed_skills(tmp_path, monkeypatch)
    out = TOOLS["mnemo_run_skill"].fn(_ctx(store), skill_name="does-not-exist")
    assert "error" in out
    assert "_skill" not in out


def test_list_skills_finds_the_18_shipped_skills(store: Store) -> None:
    """No override -> the package-relative skills/ dir (works from
    daemon/ and as an installed plugin). 18 skills ship in v3.1."""
    out = TOOLS["mnemo_list_skills"].fn(_ctx(store))
    names = {s["name"] for s in out["skills"]}
    assert len(out["skills"]) >= 18
    assert "mnemo-doc" in names


# --- loop skill-injection ----------------------------------------------


class _ScriptedProvider(BaseProvider):
    name = "fake"

    def __init__(self, script):
        self._script = script
        self._i = 0
        self.seen: list[list[dict]] = []

    def stream(self, messages, tools, *, model, system=None, max_output_tokens=4096):
        self.seen.append([dict(m) for m in messages])
        evs = self._script[self._i] if self._i < len(self._script) else [(EV_STOP, "end_turn")]
        self._i += 1
        yield from evs


def test_loop_injects_skill_guidance_as_user_turn(store: Store, tmp_path, monkeypatch) -> None:
    """run_skill -> the loop emits 'skill_loaded', acks the model, and
    pins the guidance as a USER turn (a 'system' turn is dropped by
    every provider translator -- see test_compaction)."""
    _seed_skills(tmp_path, monkeypatch)
    conv = store.create_conversation(name="c", provider="fake", model="m")
    # auto-allow the confirm-risk skill tool so the loop runs it
    store.grant_permission(project_key=None, tool_name="mnemo_run_skill")

    prov = _ScriptedProvider(
        [
            [
                (
                    EV_TOOL_CALL,
                    {"id": "s1", "name": "mnemo_run_skill", "args": {"skill_name": "mnemo-demo"}},
                ),
                (EV_STOP, "tool_use"),
            ],
            [(EV_TEXT, "following the skill now"), (EV_STOP, "end_turn")],
        ]
    )
    loop = AgentLoop(store, prov, model="m", system="s")
    events = list(loop.run(conv.id, "use the demo skill"))

    sl = next(e for e in events if e["type"] == "skill_loaded")
    assert sl["name"] == "mnemo-demo"
    assert events[-1]["type"] == "done"

    # the SECOND provider call must carry the pinned guidance as a user
    # turn the model will actually receive
    second = prov.seen[1]
    pin = next(
        m for m in second if m["role"] == "user" and "active skill" in str(m["content"]).lower()
    )
    assert "Step 1. Do the thing." in pin["content"]
