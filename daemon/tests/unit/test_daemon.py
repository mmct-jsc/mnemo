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


def test_status_when_pid_file_points_at_self(
    isolated_mnemo_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v5.6.0: 'running' requires BOTH a pid file pointing at a live
    process AND something actually listening on the port. Mock the
    listener to return os.getpid() to simulate a real running daemon
    owned by this test process."""
    paths.ensure_runtime_dirs()
    paths.pid_file().write_text(str(os.getpid()))
    monkeypatch.setattr("mnemo.daemon._listener_pid_for_port", lambda _port: os.getpid())
    s = daemon.status()
    assert s.pid_file_present is True
    assert s.pid == os.getpid()
    assert s.running is True
    assert s.stale is False
    assert s.orphaned is False  # pid file and listener agree -> healthy


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
    # New ownership-safe contract: remove_pid_file only deletes a file
    # this process owns (so a duplicate / other-port daemon exiting can
    # never orphan the real one). Write OUR pid so the remove applies.
    f = daemon.write_pid_file(os.getpid())
    assert f.exists()
    assert f.read_text().strip() == str(os.getpid())
    daemon.remove_pid_file()
    assert not f.exists()


# --- v3.2 fix: the daemon "stuck" root cause -------------------------
# Two defects orphaned the real daemon so `mnemo daemon stop/status`
# went blind and a stale process kept serving old code:
#   (1) ONE pid file shared across ports -- a preview daemon (7399)
#       and the prod daemon (7373) clobbered each other's pid file;
#   (2) remove_pid_file() unlinked unconditionally -- a duplicate /
#       other-port process exiting wiped the live one's pid file.


def test_pid_file_is_port_scoped(isolated_mnemo_home: Path) -> None:
    assert paths.pid_file(7373) != paths.pid_file(7399)
    # the default must remain the canonical 7373 file (back-compat)
    assert paths.pid_file() == paths.pid_file(7373)
    assert "7399" in paths.pid_file(7399).name


def test_status_does_not_collide_across_ports(
    isolated_mnemo_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v3.2 invariant carried forward into v5.6.0: a preview daemon on
    7399 must NOT make the 7373 prod daemon look running (the exact
    bug: prod served stale code as an orphan).

    Listener probe: 7399 has a daemon (mocked to os.getpid()), 7373
    does not (returns None — the fixture default)."""
    paths.ensure_runtime_dirs()
    paths.pid_file(7399).write_text(str(os.getpid()))
    monkeypatch.setattr(
        "mnemo.daemon._listener_pid_for_port",
        lambda port: os.getpid() if port == 7399 else None,
    )
    assert daemon.status(port=7399).running is True
    assert daemon.status(port=7373).running is False


def test_remove_pid_file_only_removes_when_owned(isolated_mnemo_home: Path) -> None:
    paths.ensure_runtime_dirs()
    # a DIFFERENT pid owns the file -> remove must be a no-op (this is
    # what stops preview-stop from orphaning the prod daemon).
    paths.pid_file().write_text("99999999")
    daemon.remove_pid_file()
    assert paths.pid_file().exists()
    assert paths.pid_file().read_text().strip() == "99999999"
    # we own it -> remove works
    paths.pid_file().write_text(str(os.getpid()))
    daemon.remove_pid_file()
    assert not paths.pid_file().exists()


def test_remove_pid_file_is_port_scoped(isolated_mnemo_home: Path) -> None:
    paths.ensure_runtime_dirs()
    paths.pid_file(7399).write_text(str(os.getpid()))
    daemon.remove_pid_file(port=7373)  # different port -> must not touch 7399
    assert paths.pid_file(7399).exists()
    daemon.remove_pid_file(port=7399)
    assert not paths.pid_file(7399).exists()


def test_start_refuses_when_already_running(
    isolated_mnemo_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v5.6.0: ``start()`` refuses when the port is genuinely bound
    (listener probe returns a pid). Pretend OUR pid is the bound
    daemon by mocking the listener; pid file is consistent."""
    paths.ensure_runtime_dirs()
    paths.pid_file().write_text(str(os.getpid()))
    monkeypatch.setattr("mnemo.daemon._listener_pid_for_port", lambda _port: os.getpid())
    with pytest.raises(RuntimeError, match="already running"):
        daemon.start()
