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
    # v3.2: pid file is port-scoped (prod 7373 vs preview 7399 must not
    # share one file -- the shared file orphaned the live daemon).
    assert paths.pid_file() == tmp_path / "mnemo-7373.pid"
    assert paths.pid_file(7399) == tmp_path / "mnemo-7399.pid"


def test_ensure_runtime_dirs_creates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "fresh"
    monkeypatch.setenv("MNEMO_HOME", str(target))
    home = paths.ensure_runtime_dirs()
    assert home == target
    assert home.is_dir()
    assert (target / "cache").is_dir()
    assert (target / "logs").is_dir()


# --- v2.0 phase 3: grammars_dir for lazy-downloaded tree-sitter wheels ----


def test_grammars_dir_under_mnemo_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MNEMO_HOME", str(tmp_path))
    assert paths.grammars_dir() == tmp_path / "grammars"


def test_ensure_runtime_dirs_creates_grammars_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """v2.0 phase 3: ``grammars/`` must exist after ``ensure_runtime_dirs``
    so lazy-downloaded tree-sitter wheels have a home on first launch."""
    target = tmp_path / "fresh"
    monkeypatch.setenv("MNEMO_HOME", str(target))
    paths.ensure_runtime_dirs()
    assert (target / "grammars").is_dir()
