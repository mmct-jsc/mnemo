"""v5.26.0 step 2: cwd-derived auto-scoping (the cross-project leak fix).

Strict isolation has existed since v1.2 -- the leak was that no production
caller passed ``active_project``. ``resolve_auto_scope`` derives the key
from the caller's cwd and applies it ONLY when that project actually has
nodes (scoping a fresh project would strict-filter everything to zero);
unindexed cwd -> (None, False) so callers can surface the index-me offer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnemo import paths, retrieve
from mnemo.cli import app
from mnemo.store import Node, Store


@pytest.fixture(autouse=True)
def _sandbox(isolated_mnemo_home: Path) -> Path:
    return isolated_mnemo_home


def _store_with_project_node(tmp_path: Path, cwd: str) -> Store:
    store = Store(tmp_path / "t.db")
    n = Node.new(
        type="code_function",
        name="fn",
        body="def fn(): ...",
        source_path=f"{cwd}/src/fn.py",
        source_kind="code_repo",
    )
    n.project_key = paths.resolve_project_key(cwd)
    store.upsert_node(n)
    return store


# --- resolve_auto_scope -----------------------------------------------------


def test_resolve_auto_scope_indexed_cwd(tmp_path: Path) -> None:
    cwd = "D:/Repos/myproj"
    store = _store_with_project_node(tmp_path, cwd)
    key, indexed = retrieve.resolve_auto_scope(store, cwd)
    assert indexed is True
    assert key == paths.resolve_project_key(cwd)
    store.close()


def test_resolve_auto_scope_unindexed_cwd(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    key, indexed = retrieve.resolve_auto_scope(store, "D:/Repos/fresh-project")
    assert key is None, "must NOT scope to a project with zero nodes"
    assert indexed is False
    store.close()


def test_resolve_auto_scope_no_cwd(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    assert retrieve.resolve_auto_scope(store, None) == (None, False)
    assert retrieve.resolve_auto_scope(store, "") == (None, False)
    store.close()


# --- server precedence: request cwd beats daemon-global workspace -----------


def test_resolve_query_project_uses_cwd_when_indexed(tmp_path: Path) -> None:
    from mnemo import server

    cwd = "D:/Repos/myproj"
    store = _store_with_project_node(tmp_path, cwd)

    class _Body:
        project_key = None
        active_project = None

    body = _Body()
    body.cwd = cwd
    assert server._resolve_query_project(store, body) == paths.resolve_project_key(cwd)
    store.close()


def test_resolve_query_project_explicit_key_beats_cwd(tmp_path: Path) -> None:
    from mnemo import server

    cwd = "D:/Repos/myproj"
    store = _store_with_project_node(tmp_path, cwd)

    class _Body:
        project_key = "EXPLICIT"
        active_project = None

    body = _Body()
    body.cwd = cwd
    assert server._resolve_query_project(store, body) == "EXPLICIT"
    store.close()


# --- hook plumbing -----------------------------------------------------------


def test_hook_sends_cwd_to_daemon_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_daemon_query(prompt: str, **kw: object) -> dict:
        captured.update(kw)
        return {"hits": [], "intent_tags": []}

    monkeypatch.setattr("mnemo.cli._daemon_query", fake_daemon_query)
    runner = CliRunner()
    payload = json.dumps({"prompt": "where is x", "cwd": "D:/Repos/myproj", "session_id": "s"})
    result = runner.invoke(app, ["hook", "user-prompt-submit"], input=payload)
    assert result.exit_code == 0
    assert captured.get("cwd") == "D:/Repos/myproj", "the hook must forward cwd for auto-scoping"


# --- MCP auto-scope (v5.26.0 step 3) -----------------------------------------


class _CountStore:
    """Minimal store stand-in for ToolContext-level tests."""

    def count_nodes(self, **kw: object) -> dict:
        return {}

    def list_sources(self) -> list:
        return []


def test_mcp_make_context_resolves_auto_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    import mnemo.mcp_server as mcp

    monkeypatch.setattr("mnemo.retrieve.resolve_auto_scope", lambda store, cwd: ("KEY1", True))
    ctx = mcp.make_context()
    try:
        assert ctx.auto_scope_key == "KEY1"
        assert ctx.auto_scope_indexed is True
    finally:
        ctx.store.close()


def test_mnemo_query_uses_ctx_auto_scope_when_no_explicit_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mnemo.agent_tools as at

    seen: dict = {}

    class _Res:
        hits: list = []
        intent_tags: list = []
        tokens_used = 0
        query_id = "q"
        local_only_excluded = 0

    def spy(store, embedder, prompt, **kw):  # type: ignore[no-untyped-def]
        seen.update(kw)
        return _Res()

    monkeypatch.setattr(at.retrieve, "query", spy)
    monkeypatch.setattr(at, "_FIRST_QUERY_DONE", True)
    ctx = at.ToolContext(store=_CountStore(), embedder=None, auto_scope_key="PAUTO")
    out = at.TOOLS["mnemo_query"].fn(ctx, prompt="x")
    assert seen.get("active_project") == "PAUTO"
    assert out["scope"] == {"project_key": "PAUTO", "auto": True}


def test_mnemo_query_explicit_key_beats_ctx_auto_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mnemo.agent_tools as at

    seen: dict = {}

    class _Res:
        hits: list = []
        intent_tags: list = []
        tokens_used = 0
        query_id = "q"
        local_only_excluded = 0

    def spy(store, embedder, prompt, **kw):  # type: ignore[no-untyped-def]
        seen.update(kw)
        return _Res()

    monkeypatch.setattr(at.retrieve, "query", spy)
    monkeypatch.setattr(at, "_FIRST_QUERY_DONE", True)
    ctx = at.ToolContext(store=_CountStore(), embedder=None, auto_scope_key="PAUTO")
    out = at.TOOLS["mnemo_query"].fn(ctx, prompt="x", project_key="EXPL")
    assert seen.get("active_project") == "EXPL"
    assert out["scope"] == {"project_key": "EXPL", "auto": False}


def test_mnemo_query_first_call_notice_mentions_unindexed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mnemo.agent_tools as at

    class _Res:
        hits: list = []
        intent_tags: list = []
        tokens_used = 0
        query_id = "q"
        local_only_excluded = 0

    monkeypatch.setattr(at.retrieve, "query", lambda *a, **k: _Res())
    monkeypatch.setattr(at, "_FIRST_QUERY_DONE", False)
    ctx = at.ToolContext(
        store=_CountStore(), embedder=None, auto_scope_key=None, auto_scope_indexed=False
    )
    out = at.TOOLS["mnemo_query"].fn(ctx, prompt="x")
    assert "not indexed" in out.get("notice", ""), "unindexed cwd must surface the index-me offer"


def test_hook_fallback_scopes_in_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Daemon down -> the in-process fallback applies resolve_auto_scope."""
    from tests.conftest import FakeEmbedder

    monkeypatch.setattr("mnemo.cli._daemon_query", lambda *a, **k: None)
    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    monkeypatch.setattr("mnemo.retrieve.resolve_auto_scope", lambda store, cwd: ("PX", True))

    seen: dict = {}
    real_query = retrieve.query

    def spy_query(store, embedder, prompt, **kw):  # type: ignore[no-untyped-def]
        seen.update(kw)
        return real_query(store, embedder, prompt, **kw)

    monkeypatch.setattr("mnemo.retrieve.query", spy_query)
    runner = CliRunner()
    payload = json.dumps({"prompt": "where is x", "cwd": "D:/Repos/myproj"})
    result = runner.invoke(app, ["hook", "user-prompt-submit"], input=payload)
    assert result.exit_code == 0
    assert seen.get("active_project") == "PX"
