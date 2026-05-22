"""v5.6.0 — port-listener becomes the authoritative source of truth.

Bug found this session (and twice before): ``mnemo daemon stop`` reports
"daemon not running" while ``netstat`` shows the daemon listening
on :7373. Root cause: ``is_alive(pid)`` uses ``os.kill(pid, 0)`` which
on Windows can return False for a live process in edge cases (signal-0
behavior differs from POSIX; process privileges + Python's signal
mapping interact). When ``is_alive`` lies, ``status()`` reports stale,
``stop()`` cleans up the pid file, and the listening process becomes
an orphan that ``mnemo`` can no longer manage.

Fix: query the OS for who's actually bound to the port (``psutil``'s
``net_connections``) and treat THAT as authoritative. The pid file
becomes a hint / fast-path; the listener pid is the truth.

Behavior contract this test file locks:

1. ``_listener_pid_for_port(port)`` returns None for an unused port.
2. ``_listener_pid_for_port(port)`` returns the bound process's pid
   when something is listening (covered by binding a real socket in
   the test).
3. ``status()`` returns ``orphaned=True`` when the pid file disagrees
   with the actual listener (the live-orphan scenario).
4. ``status()`` returns ``running=True`` with the listener pid when
   the pid file is missing but something IS bound to the port (the
   orphan-without-pid-file scenario).
5. ``DaemonStatus`` exposes the new ``orphaned`` field as an attribute
   default-False (back-compat).
"""

from __future__ import annotations

import socket
from contextlib import closing
from unittest.mock import patch


def _free_port() -> int:
    """Bind a socket to port 0 to let the OS assign a free one, then
    release it. The returned port is racy (someone else might grab it)
    but fine for unit-test scope."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_listener_pid_for_unused_port_is_none() -> None:
    """No bind = no listener = return None."""
    from mnemo.daemon import _listener_pid_for_port

    port = _free_port()
    # After _free_port returns, nothing's listening on `port`.
    assert _listener_pid_for_port(port) is None


def test_listener_pid_for_bound_port_returns_our_pid() -> None:
    """If THIS test process binds + listens on a port,
    ``_listener_pid_for_port`` must return ``os.getpid()``. This is
    the v5.6.0 source-of-truth contract — the OS knows who's listening
    even when our internal bookkeeping is stale."""
    import os

    from mnemo.daemon import _listener_pid_for_port

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        listener = _listener_pid_for_port(port)
        assert listener == os.getpid(), (
            f"Listener pid must be this test's pid {os.getpid()}; "
            f"got {listener!r}. _listener_pid_for_port is the only "
            f"reliable way to detect the v3.2 gotcha #32 orphan; if "
            f"this is wrong the whole orphan-recovery path is broken."
        )
    finally:
        sock.close()


def test_daemon_status_exposes_orphaned_field() -> None:
    """The ``orphaned`` flag is the new v5.6.0 surface bit; it must
    default to False so legacy callers (existing tests / docs / the
    CLI's status formatter) don't break."""
    from mnemo.daemon import DaemonStatus

    s = DaemonStatus(running=False, pid=None, pid_file_present=False, stale=False)
    assert hasattr(s, "orphaned"), "DaemonStatus must expose an 'orphaned' attribute"
    assert s.orphaned is False, "orphaned must default to False"


def test_status_detects_orphan_when_pid_file_disagrees_with_listener() -> None:
    """The v3.2 gotcha #32 / v5.5.0 lesson #93 scenario:

    1. pid file says pid X
    2. ``is_alive(X)`` returns False (Windows quirk)
    3. ...but the OS reports pid Y is actually listening on :port

    Pre-v5.6.0: status() returns running=False, stale=True. stop()
    cleans up the pid file. The live listener Y becomes an orphan
    invisible to mnemo CLI.

    v5.6.0: status() detects the disagreement, reports running=True
    with pid=Y (the truth) + orphaned=True (so the CLI can warn the
    user "another daemon is running outside our control")."""
    from mnemo.daemon import status

    # Mock the pid file reader + the alive check + the listener probe.
    # We're testing the orchestration, not any one underlying primitive.
    with (
        patch("mnemo.daemon.read_pid", return_value=12345),  # stale pid file
        patch("mnemo.daemon.is_alive", return_value=False),  # OS says dead
        patch("mnemo.daemon._listener_pid_for_port", return_value=67890),  # but port is bound
    ):
        s = status(port=17373)
        assert s.running is True, (
            "Port IS bound → daemon IS running, even if the pid file's "
            "pid looks dead. The pid file was stale; the listener is the truth."
        )
        assert s.pid == 67890, (
            "When pid file disagrees with listener, the LISTENER pid is reported "
            f"(that's the one mnemo needs to kill to recover). Got {s.pid}."
        )
        assert s.orphaned is True, (
            "Disagreement between pid file and listener IS the orphan condition; "
            "the CLI should warn the user that something outside mnemo's "
            "bookkeeping is bound to the port."
        )


def test_status_detects_orphan_when_no_pid_file_but_port_bound() -> None:
    """Second orphan path: pid file got deleted (by a duplicate exit,
    by manual rm, by OS cleanup), but a daemon is still listening on
    the port.

    Pre-v5.6.0: status() returns running=False (no pid file = nothing
    to report). The CLI says "daemon not running" and a subsequent
    start() races to bind the port (and fails silently). v5.6.0:
    status() consults the listener probe and reports the truth."""
    from mnemo.daemon import status

    with (
        patch("mnemo.daemon.read_pid", return_value=None),  # no pid file
        patch("mnemo.daemon._listener_pid_for_port", return_value=99999),  # but bound
    ):
        s = status(port=17373)
        assert s.running is True, (
            "Port bound → daemon running, even without a pid file. The pid file "
            "is bookkeeping; the listener is reality."
        )
        assert s.pid == 99999
        assert s.orphaned is True
