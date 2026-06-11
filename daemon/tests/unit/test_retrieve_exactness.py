"""v5.27.0 step 2: BM25 recall + lexical fusion + exact-name finisher.

The baseline misses share one shape: the answering code node never even
became a CANDIDATE (vector top-40 full of long prose). These tests pin
the three exactness behaviors with the vector channel disabled, so the
lexical path must do all the work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mnemo import retrieve
from mnemo.store import Node, Store
from tests.conftest import FakeEmbedder


@pytest.fixture(autouse=True)
def _sandbox(isolated_mnemo_home: Path) -> Path:
    return isolated_mnemo_home


def _node(name: str, body: str, path: str, type_: str = "code_function") -> Node:
    return Node.new(
        type=type_,
        name=name,
        description=f"{name}",
        body=body,
        source_path=path,
        source_kind="code_repo",
    )


def _quiet_vec(monkeypatch: pytest.MonkeyPatch, store: Store) -> None:
    """Disable the vector channel so only lexical recall can surface hits."""
    monkeypatch.setattr(store, "vec_search", lambda *a, **k: [])


def test_bm25_provides_candidates_when_vector_misses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "t.db")
    n = _node("format_statusline", "def format_statusline(health): ...", "/statusline.py")
    store.upsert_node(n)
    _quiet_vec(monkeypatch, store)
    res = retrieve.query(store, FakeEmbedder(), "where is format_statusline", k=5)
    assert any(h.node_id == n.id for h in res.hits), (
        "a lexically-exact node must surface even when the vector channel misses it"
    )
    store.close()


def test_exact_name_match_outranks_paraphrase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "t.db")
    exact = _node("format_statusline", "def format_statusline(): ...", "/statusline.py")
    prose = _node(
        "session_notes",
        "long discussion about statusline formatting, the format of the bar, "
        "statusline decisions and other prose " * 20,
        "/notes.md",
        type_="memory_project",
    )
    store.upsert_node(exact)
    store.upsert_node(prose)
    _quiet_vec(monkeypatch, store)
    res = retrieve.query(store, FakeEmbedder(), "how does format_statusline work", k=5)
    ids = [h.node_id for h in res.hits]
    assert ids, "lexical recall must produce candidates"
    assert ids[0] == exact.id, "asking for a thing BY NAME must beat a paraphrasing prose node"
    store.close()


def test_exact_name_boost_requires_min_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tiny names (< 4 chars) must not trigger the finisher -- 'app' or 'db'
    appearing in a prompt is noise, not intent."""
    store = Store(tmp_path / "t.db")
    tiny = _node("app", "def app(): ...", "/app.py")
    real = _node("application_bootstrap", "app bootstrap " * 50, "/boot.py")
    store.upsert_node(tiny)
    store.upsert_node(real)
    _quiet_vec(monkeypatch, store)
    res = retrieve.query(store, FakeEmbedder(), "how does the app bootstrap work", k=5)
    assert res.hits, "lexical recall must still produce candidates"
    # No assertion on order between them beyond: the run must not crash and
    # the tiny name must not get the *boost* -- proven structurally below.
    from mnemo.retrieve import _exact_name_match

    assert _exact_name_match("app", "how does the app bootstrap work") is False
    assert _exact_name_match("application_bootstrap", "x application_bootstrap y") is True
    assert _exact_name_match("format_statusline", "FORMAT_STATUSLINE?") is True
    store.close()
