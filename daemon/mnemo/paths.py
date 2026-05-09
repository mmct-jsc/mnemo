"""Runtime path resolution for mnemo.

The mnemo daemon stores its database, model cache, and logs under
``~/.claude/mnemo/``. This module is the single source of truth for those
paths and exposes overrides via the ``MNEMO_HOME`` environment variable
(used by tests and by users who want to relocate the runtime dir).
"""

from __future__ import annotations

import os
from pathlib import Path


def claude_home() -> Path:
    """Claude Code config directory: ``~/.claude/``."""
    override = os.environ.get("CLAUDE_HOME")
    return Path(override) if override else Path.home() / ".claude"


def mnemo_home() -> Path:
    """mnemo runtime directory: ``~/.claude/mnemo/`` (overridable via ``MNEMO_HOME``)."""
    override = os.environ.get("MNEMO_HOME")
    return Path(override) if override else claude_home() / "mnemo"


def db_path() -> Path:
    return mnemo_home() / "mnemo.db"


def vec_path() -> Path:
    return mnemo_home() / "mnemo.vec"


def cache_dir() -> Path:
    return mnemo_home() / "cache"


def logs_dir() -> Path:
    return mnemo_home() / "logs"


def pid_file() -> Path:
    return mnemo_home() / "pid"


def ensure_runtime_dirs() -> Path:
    """Create runtime directories if missing. Returns ``mnemo_home``."""
    home = mnemo_home()
    home.mkdir(parents=True, exist_ok=True)
    cache_dir().mkdir(exist_ok=True)
    logs_dir().mkdir(exist_ok=True)
    return home
