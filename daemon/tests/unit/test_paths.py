"""Tests for path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from mnemo import paths


def test_mnemo_home_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MNEMO_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_HOME", raising=False)
    assert paths.mnemo_home() == Path.home() / ".claude" / "mnemo"


def test_mnemo_home_overridden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MNEMO_HOME", str(tmp_path))
    assert paths.mnemo_home() == tmp_path


def test_claude_home_overridden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path))
    monkeypatch.delenv("MNEMO_HOME", raising=False)
    assert paths.claude_home() == tmp_path
    assert paths.mnemo_home() == tmp_path / "mnemo"


def test_runtime_subpaths_under_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MNEMO_HOME", str(tmp_path))
    assert paths.db_path() == tmp_path / "mnemo.db"
    assert paths.vec_path() == tmp_path / "mnemo.vec"
    assert paths.cache_dir() == tmp_path / "cache"
    assert paths.logs_dir() == tmp_path / "logs"
    assert paths.pid_file() == tmp_path / "pid"


def test_ensure_runtime_dirs_creates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "fresh"
    monkeypatch.setenv("MNEMO_HOME", str(target))
    home = paths.ensure_runtime_dirs()
    assert home == target
    assert home.is_dir()
    assert (target / "cache").is_dir()
    assert (target / "logs").is_dir()
