"""Shared pytest fixtures for mnemo tests."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from mnemo.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    """Fresh per-test SQLite store under a tmp_path."""
    s = Store(tmp_path / "mnemo.db")
    try:
        yield s
    finally:
        s.close()


class FakeEmbedder:
    """Deterministic, instant embedder for tests that don't need real semantics.

    Maps each input string to a 384-d vector by spreading its MD5 digest across
    a fixed-shape array and zero-padding the rest. Output isn't unit-normalized
    but sqlite-vec only cares that the dimension matches.
    """

    dim = 384
    _model = None  # so server.health reports embedding_loaded=False

    def embed_text(self, text: str) -> list[float]:
        digest = hashlib.md5(text.encode("utf-8"), usedforsecurity=False).digest()
        head = [(b - 128) / 128.0 for b in digest]  # 16 floats in [-1, 1]
        return head + [0.0] * (self.dim - len(head))

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def isolated_mnemo_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point MNEMO_HOME at a tmp dir so CLI / daemon tests stay sandboxed.

    v5.6.0: also stubs out ``daemon._listener_pid_for_port`` to return
    None by default so pid-file-based lifecycle tests don't see real
    OS-level daemons listening on :7373 (e.g. a dev box's prod
    daemon). Tests that DO want to exercise listener-aware logic patch
    it explicitly (see ``test_daemon_orphan_detection.py``)."""
    monkeypatch.setenv("MNEMO_HOME", str(tmp_path))
    monkeypatch.setattr("mnemo.daemon._listener_pid_for_port", lambda _port: None)
    return tmp_path
