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


def status(port: int = DEFAULT_PORT) -> DaemonStatus:
    pid = read_pid(port)
    if pid is None:
        return DaemonStatus(running=False, pid=None, pid_file_present=False, stale=False)
    alive = is_alive(pid)
    return DaemonStatus(
        running=alive,
        pid=pid,
        pid_file_present=True,
        stale=not alive,
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
    """Stop the daemon if running. Returns True if a daemon was stopped."""
    s = status(port)
    if not s.running:
        if s.stale:
            paths.pid_file(port).unlink(missing_ok=True)
        return False
    pid = s.pid
    assert pid is not None
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
        if not is_alive(pid):
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
