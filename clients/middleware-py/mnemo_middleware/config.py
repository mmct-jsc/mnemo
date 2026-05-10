"""Runtime configuration for mnemo-middleware. Read from env vars."""

from __future__ import annotations

import os


def daemon_url() -> str:
    """Where the local mnemo daemon listens. Override with
    ``MNEMO_DAEMON_URL``."""
    return os.environ.get("MNEMO_DAEMON_URL", "http://127.0.0.1:7373")


def default_budget() -> int:
    """Default token budget for retrieve_context() responses."""
    try:
        return int(os.environ.get("MNEMO_DEFAULT_BUDGET", "800"))
    except ValueError:
        return 800


def default_k() -> int:
    """Default number of hits to request."""
    try:
        return int(os.environ.get("MNEMO_DEFAULT_K", "5"))
    except ValueError:
        return 5


def timeout_seconds() -> float:
    """Per-call timeout when talking to the daemon. Slow daemon must
    never block a model call indefinitely -- if we exceed this, the
    middleware logs a warning and proceeds without injection."""
    try:
        return float(os.environ.get("MNEMO_TIMEOUT", "2.0"))
    except ValueError:
        return 2.0
