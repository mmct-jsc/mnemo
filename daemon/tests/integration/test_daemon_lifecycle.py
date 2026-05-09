"""Integration test for daemon spawn.

Disabled by default: spawning a real detached uvicorn process is fragile on
Windows (subprocess inheritance + uvicorn's stdio handling). The daemon
module's pure helpers (read_pid, is_alive, status, stop) are covered by
``tests/unit/test_daemon.py`` against an isolated MNEMO_HOME.

To exercise the real spawn path manually::

    uv run python -m mnemo.cli daemon start
    curl http://127.0.0.1:7373/health
    uv run python -m mnemo.cli daemon stop
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="manual-only; see module docstring for the smoke recipe")
def test_daemon_start_health_stop_manual() -> None:  # pragma: no cover
    """Placeholder so the file isn't empty."""
