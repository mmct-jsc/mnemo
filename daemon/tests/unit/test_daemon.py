"""Unit tests for the daemon module's pure helpers.

These cover everything except the actual subprocess spawn (see the integration
test docstring for why spawning is manual-only on Windows).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mnemo import daemon, paths


def test_status_when_no_pid_file(isolated_mnemo_home: Path) -> None:
    s = daemon.status()
    assert s.running is False
    assert s.pid is None
    assert s.pid_file_present is False
    assert s.stale is False


def test_status_when_pid_file_points_at_self(isolated_mnemo_home: Path) -> None:
    paths.ensure_runtime_dirs()
    paths.pid_file().write_text(str(os.getpid()))
    s = daemon.status()
    assert s.pid_file_present is True
    assert s.pid == os.getpid()
    assert s.running is True
    assert s.stale is False


def test_status_when_pid_file_is_stale(isolated_mnemo_home: Path) -> None:
    paths.ensure_runtime_dirs()
    # PID 1 is the OS init on Unix and very unlikely to be Python on Windows;
    # but to be safe, use a clearly-impossible PID instead.
    paths.pid_file().write_text("99999999")
    s = daemon.status()
    assert s.pid_file_present is True
    assert s.running is False
    assert s.stale is True


def test_status_when_pid_file_is_garbage(isolated_mnemo_home: Path) -> None:
    paths.ensure_runtime_dirs()
    paths.pid_file().write_text("not a number")
    s = daemon.status()
    # read_pid returns None on malformed content; status reports as no daemon.
    assert s.running is False
    assert s.pid is None
    assert s.pid_file_present is False


def test_read_pid_missing(isolated_mnemo_home: Path) -> None:
    assert daemon.read_pid() is None


def test_read_pid_present(isolated_mnemo_home: Path) -> None:
    paths.ensure_runtime_dirs()
    paths.pid_file().write_text("4242")
    assert daemon.read_pid() == 4242


def test_is_alive_self() -> None:
    assert daemon.is_alive(os.getpid()) is True


def test_is_alive_zero_or_negative() -> None:
    assert daemon.is_alive(0) is False
    assert daemon.is_alive(-1) is False


def test_is_alive_clearly_dead_pid() -> None:
    # 99999999 is far above any realistic Linux/Windows PID range.
    assert daemon.is_alive(99999999) is False


def test_stop_when_not_running_returns_false(isolated_mnemo_home: Path) -> None:
    assert daemon.stop() is False


def test_stop_cleans_up_stale_pid_file(isolated_mnemo_home: Path) -> None:
    paths.ensure_runtime_dirs()
    paths.pid_file().write_text("99999999")
    daemon.stop()
    assert not paths.pid_file().exists()


def test_write_and_remove_pid_file(isolated_mnemo_home: Path) -> None:
    f = daemon.write_pid_file(1234)
    assert f.exists()
    assert f.read_text().strip() == "1234"
    daemon.remove_pid_file()
    assert not f.exists()


def test_start_refuses_when_already_running(
    isolated_mnemo_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths.ensure_runtime_dirs()
    paths.pid_file().write_text(str(os.getpid()))  # pretend daemon is us
    with pytest.raises(RuntimeError, match="already running"):
        daemon.start()
