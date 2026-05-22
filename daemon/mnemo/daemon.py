"""Daemon process lifecycle: PID-file based start/stop/status.

The daemon is a foreground ``uvicorn`` process. ``start`` spawns it as a
detached child (Windows: ``CREATE_NEW_PROCESS_GROUP``; Unix: ``setsid``);
``stop`` reads the PID file and sends SIGTERM (or the platform equivalent).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from mnemo import paths

log = logging.getLogger(__name__)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7373
START_TIMEOUT_SECONDS = 8.0
STOP_TIMEOUT_SECONDS = 5.0


@dataclass
class DaemonStatus:
    running: bool
    pid: int | None
    pid_file_present: bool
    stale: bool  # PID file present but process not alive
    # v5.6.0: orphan = pid file disagrees with the actual port listener
    # (or pid file is missing but something IS listening). The pid file
    # is bookkeeping; the OS port owner is the truth. When orphaned is
    # True, ``pid`` is the LISTENER pid (the one ``stop()`` must
    # terminate to recover), regardless of what the pid file said.
    orphaned: bool = False


def read_pid(port: int = DEFAULT_PORT) -> int | None:
    f = paths.pid_file(port)
    if not f.exists():
        return None
    try:
        return int(f.read_text().strip())
    except (ValueError, OSError):
        return None


def is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by someone else; treat as alive.
        return True
    except OSError:
        return False
    return True


def _listener_pid_for_port(port: int) -> int | None:
    """Return the pid of whichever process is LISTENING on ``port`` on
    localhost, or None if nothing is bound.

    v5.6.0 source-of-truth for daemon lifecycle: the OS-level port
    owner is authoritative. ``is_alive(pid)`` lies in some Windows
    scenarios (``os.kill(pid, 0)`` can return False for live
    processes when signal-0 hits privilege boundaries), so the pid
    file alone isn't enough — we cross-check with what's actually
    bound. See v3.2 gotcha #32 / v5.5.0 lesson #93 for the recurrent
    orphan bug this fixes.

    Implementation: iterate ``psutil.process_iter()`` and check each
    process's own ``net_connections()``. This is slower than the
    system-wide ``psutil.net_connections()`` (~50ms with ~500
    processes) but works without elevated privileges on every
    supported platform — macOS in particular restricts system-wide
    socket enumeration to root since 10.14. Mnemo's daemon CLI runs
    as a regular user, so the portable path is the right default.

    Returns None on any error so callers can fall back to the pid
    file gracefully — never raises into the daemon lifecycle path.
    """
    try:
        import psutil  # imported here so test collection doesn't depend on it
    except ImportError:  # pragma: no cover -- listed as a dep, but be defensive
        return None
    try:
        for proc in psutil.process_iter(["pid"]):
            try:
                for conn in proc.net_connections(kind="inet"):
                    if conn.status != psutil.CONN_LISTEN:
                        continue
                    laddr = conn.laddr
                    if not laddr or laddr.port != port:
                        continue
                    return proc.info["pid"]
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                # Skip processes we can't introspect (other users,
                # gone-since-iter-started); they're definitively not
                # our daemon either way.
                continue
    except (psutil.AccessDenied, OSError):  # pragma: no cover -- top-level safety net
        return None
    return None


def status(port: int = DEFAULT_PORT) -> DaemonStatus:
    """Authoritative daemon-status view. Port listener wins over pid file.

    Three cases the v5.6.0 logic handles that the v5.5.x logic missed:

    1. pid file says X, ``is_alive(X)`` says dead, but Y IS listening
       on the port — the v3.2/v5.5.0 orphan scenario. Return
       running=True, pid=Y, orphaned=True so the CLI can warn and
       ``stop()`` can target Y.
    2. pid file missing but Y IS listening — pid file was deleted out
       of band; daemon is still alive. Return running=True, pid=Y,
       orphaned=True.
    3. pid file says X, ``is_alive(X)`` says alive, listener pid is
       also X — healthy. Return running=True, pid=X, orphaned=False.
    """
    pid_in_file = read_pid(port)
    listener_pid = _listener_pid_for_port(port)

    if listener_pid is not None:
        # Something IS bound to the port — that's the running daemon
        # by definition. Pid file may or may not agree.
        if pid_in_file is not None and pid_in_file == listener_pid:
            return DaemonStatus(
                running=True,
                pid=listener_pid,
                pid_file_present=True,
                stale=False,
                orphaned=False,
            )
        # Disagreement: listener exists but pid file points elsewhere
        # (or pid file is missing). Either way, the listener is the
        # truth and the bookkeeping is stale.
        return DaemonStatus(
            running=True,
            pid=listener_pid,
            pid_file_present=pid_in_file is not None,
            stale=False,
            orphaned=True,
        )

    # No listener: daemon truly not running.
    if pid_in_file is None:
        return DaemonStatus(
            running=False, pid=None, pid_file_present=False, stale=False, orphaned=False
        )
    # Pid file present but nothing bound — definitively stale.
    return DaemonStatus(
        running=False,
        pid=pid_in_file,
        pid_file_present=True,
        stale=True,
        orphaned=False,
    )


def start(*, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> int:
    """Spawn a detached daemon. Returns the daemon's PID.

    Raises ``RuntimeError`` if the daemon is already running, or if it fails
    to publish its PID file within ``START_TIMEOUT_SECONDS``.
    """
    s = status(port)
    if s.running:
        raise RuntimeError(f"daemon already running (pid {s.pid})")
    if s.pid_file_present:
        # Stale PID; clean up before respawn.
        paths.pid_file(port).unlink(missing_ok=True)

    paths.ensure_runtime_dirs()
    cmd = [
        sys.executable,
        "-m",
        "mnemo.cli",
        "daemon",
        "start",
        "--foreground",
        "--host",
        host,
        "--port",
        str(port),
    ]

    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP detaches from the parent's Ctrl-C handling.
        # We deliberately don't use DETACHED_PROCESS: combining it with
        # subprocess.DEVNULL stdio can leave the child with no usable handles
        # on some Windows configurations.
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        start_new_session = True

    subprocess.Popen(  # noqa: S603 - cmd is fully controlled
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )

    deadline = time.time() + START_TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(0.1)
        pid = read_pid(port)
        if pid is not None and is_alive(pid):
            return pid
    raise RuntimeError(f"daemon did not write its pid file within {START_TIMEOUT_SECONDS}s")


def stop(port: int = DEFAULT_PORT) -> bool:
    """Stop the daemon if running. Returns True if a daemon was stopped.

    v5.6.0: ``status()`` now consults the port-listener as authoritative,
    so ``s.pid`` is the LISTENER pid when there's an orphan (pid file
    disagrees with reality). We terminate that pid — recovers the
    v3.2/v5.5.0 orphan scenarios without manual ``Stop-Process``.

    Wait-for-exit uses the listener probe too: a process pid can be
    reused by an unrelated process between our SIGTERM and the next
    poll, so the right "did it exit" check is "is the port still
    bound" — not "does pid X still exist".
    """
    s = status(port)
    if not s.running:
        if s.stale:
            paths.pid_file(port).unlink(missing_ok=True)
        return False
    pid = s.pid
    assert pid is not None
    if s.orphaned:
        log.warning(
            "daemon at pid %d is orphaned (pid file said %s); terminating the "
            "actual port :%d listener to recover",
            pid,
            paths.pid_file(port).read_text().strip()
            if paths.pid_file(port).exists()
            else "<no pid file>",
            port,
        )
    try:
        if sys.platform == "win32":
            # On Windows, signal.SIGTERM is mapped to TerminateProcess.
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        log.warning("failed to signal daemon pid %d: %s", pid, exc)
        return False

    deadline = time.time() + STOP_TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(0.1)
        # Port-listener probe: definitively gone when nothing's bound.
        # is_alive(pid) on Windows can lie about the process state (see
        # _listener_pid_for_port docstring), so the port being free is
        # the more reliable termination signal.
        if _listener_pid_for_port(port) is None and not is_alive(pid):
            paths.pid_file(port).unlink(missing_ok=True)
            return True
    log.warning("daemon pid %d did not exit within %ds", pid, STOP_TIMEOUT_SECONDS)
    return False


def write_pid_file(pid: int | None = None, *, port: int = DEFAULT_PORT) -> Path:
    """Used by the foreground server entry point to publish its PID."""
    paths.ensure_runtime_dirs()
    p = paths.pid_file(port)
    p.write_text(str(pid if pid is not None else os.getpid()))
    return p


def remove_pid_file(port: int = DEFAULT_PORT) -> None:
    """Ownership-guarded: only unlink the pid file if it still names
    THIS process. A duplicate / other-port daemon exiting must never
    wipe the live daemon's pid file (that orphaned it -> `mnemo daemon
    stop/status` went blind -> stale code kept serving)."""
    p = paths.pid_file(port)
    try:
        on_disk = int(p.read_text().strip())
    except (OSError, ValueError):
        return
    if on_disk == os.getpid():
        p.unlink(missing_ok=True)
