"""v5.25.0 step 4: the mnemo_help discovery tool + grep-nudges.

For MCP-only hosts (Cursor / Continue / Windsurf / ...) mnemo has no
commands/hooks -- only passive tools. mnemo_help is a no-arg safe read that
tells the model what mnemo is and to prefer mnemo_query over grep; the
mnemo_query / mnemo_search_by_type descriptions carry the same nudge, and the
first mnemo_query result per process carries a one-time discovery notice.
"""

from __future__ import annotations

import mnemo.agent_tools as at
from mnemo.agent_tools import TOOLS


class _FakeStore:
    def count_nodes(self) -> dict:
        return {"memory": 3, "code_function": 2}

    def list_sources(self) -> list:
        return [object(), object()]


def test_mnemo_help_registered_safe_no_args() -> None:
    assert "mnemo_help" in TOOLS
    spec = TOOLS["mnemo_help"]
    assert spec.risk == "safe"
    assert spec.parameters == {"type": "object", "properties": {}, "required": []}


def test_mnemo_help_returns_capabilities() -> None:
    ctx = at.ToolContext(store=_FakeStore())
    out = TOOLS["mnemo_help"].fn(ctx)
    blob = (out.get("what", "") + " " + out.get("prefer_over_grep", "")).lower()
    assert "grep" in blob
    names = [t["name"] for t in out["key_tools"]]
    assert "mnemo_query" in names
    assert out["counts"]["nodes"] == 5
    assert out["counts"]["sources"] == 2


def test_mnemo_query_description_nudges_over_grep() -> None:
    assert "grep" in TOOLS["mnemo_query"].description.lower()


def test_mnemo_search_by_type_description_nudges_over_grep() -> None:
    assert "grep" in TOOLS["mnemo_search_by_type"].description.lower()


def test_mnemo_query_first_call_notice(monkeypatch) -> None:
    monkeypatch.setattr(at, "_FIRST_QUERY_DONE", False)

    class _Res:
        hits: list = []
        intent_tags: list = []
        tokens_used = 0
        query_id = "q"
        local_only_excluded = 0

    monkeypatch.setattr(at.retrieve, "query", lambda *a, **k: _Res())
    ctx = at.ToolContext(store=None, embedder=None)
    first = TOOLS["mnemo_query"].fn(ctx, prompt="x")
    assert "notice" in first
    assert "mnemo" in first["notice"].lower()
    second = TOOLS["mnemo_query"].fn(ctx, prompt="x")
    assert "notice" not in second, "the discovery notice fires once per process"
