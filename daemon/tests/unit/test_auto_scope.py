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
