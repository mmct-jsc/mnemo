"""mnemo statusline -- a one-line presence cue for the Claude Code status bar.

Claude Code pipes its status payload (model, cwd, session_id, ...) to the
command configured under settings.json ``statusLine``; ``mnemo statusline``
(cli.py) hands that stdin to :func:`render`. Kept dependency-light and fast:

- a hard-timeout health probe that NEVER opens the SQLite store -- opening
  it would contend with the live daemon's lock and could hang the bar, and
- a best-effort per-session inject count written by the ``user-prompt-submit``
  hook so the bar can show ``up{N}`` (the "injected N memories" signal).

:func:`render` MUST NEVER raise -- a thrown statusline command breaks the
user's status bar. Every failure path degrades to ``"mnemo offline"``.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

from mnemo import paths
from mnemo.daemon import DEFAULT_PORT

_SESSION_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_SESSION_TTL_S = 86_400  # prune per-session inject files older than a day


def _compact_count(n: int) -> str:
    """Human-compact node count: 42 -> '42', 17919 -> '17.9k', 1.5e6 -> '1.5M'."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def format_statusline(health: dict | None, inject_count: int | None = None) -> str:
    """Pure formatter. ``health`` is the /v1/health JSON (or None when the
    daemon is unreachable); ``inject_count`` is the last per-session
    injection size (or None / 0 to omit)."""
    if not health:
        return "mnemo offline"
    line = f"mnemo {_compact_count(int(health.get('node_count', 0)))}"
    if inject_count:
        line += f" up{inject_count}"
    return line


# --- settings.json wiring (installer + doctor share this single source) ----

STATUSLINE_COMMAND = "mnemo statusline"


def statusline_is_mnemo(settings: dict) -> bool:
    """True if settings.json's ``statusLine`` already points at mnemo."""
    sl = settings.get("statusLine")
    return isinstance(sl, dict) and STATUSLINE_COMMAND in str(sl.get("command", ""))


def ensure_statusline(settings: dict) -> tuple[dict, str]:
    """Non-clobbering, idempotent add of mnemo's statusLine to a settings
    dict. Returns ``(settings, action)``:

    - ``"added"``        -- no statusLine existed; mnemo's was inserted (a
      shallow copy is returned; the input dict is not mutated).
    - ``"exists_mnemo"`` -- already mnemo's; returned unchanged.
    - ``"exists_other"`` -- a different statusLine exists; left untouched.
    """
    if not settings.get("statusLine"):
        new = dict(settings)
        new["statusLine"] = {"type": "command", "command": STATUSLINE_COMMAND, "padding": 0}
        return new, "added"
    if statusline_is_mnemo(settings):
        return settings, "exists_mnemo"
    return settings, "exists_other"


def probe_health(timeout: float = 2.0, port: int = DEFAULT_PORT) -> dict | None:
    """GET /v1/health with a short timeout. Returns parsed JSON or None.
    Never opens the store; never raises.

    NB: 2.0s, not 250ms. Live verify showed /v1/health on a large warm
    corpus under concurrent load can exceed 250ms, so a tight timeout
    rendered "mnemo offline" against a HEALTHY daemon. urlopen returns as
    soon as the daemon answers, and the status bar reruns per assistant
    message (not per frame), so a higher ceiling only lengthens the rare
    daemon-down case before it shows "offline". (A cached count + a raw
    socket-connect liveness probe could make this instant; deferred.)"""
    try:
        url = f"http://127.0.0.1:{port}/v1/health"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _session_file(session_id: str) -> Path:
    safe = _SESSION_ID_RE.sub("", session_id) or "unknown"
    return paths.sessions_dir() / f"{safe}.json"


def write_inject_count(session_id: str | None, count: int) -> None:
    """Record the last injection size for ``session_id`` so the statusline
    can show ``up{N}``. Best-effort; never raises. Opportunistically prunes
    stale session files."""
    if not session_id:
        return
    try:
        f = _session_file(session_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"count": int(count)}), encoding="utf-8")
        _prune_stale(f.parent)
    except Exception:
        return


def read_inject_count(session_id: str | None) -> int | None:
    """Read the last injection size for ``session_id`` (or None)."""
    if not session_id:
        return None
    try:
        data = json.loads(_session_file(session_id).read_text(encoding="utf-8"))
        return int(data["count"])
    except Exception:
        return None


def _prune_stale(sessions_dir: Path) -> None:
    try:
        cutoff = time.time() - _SESSION_TTL_S
        for f in sessions_dir.glob("*.json"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                continue
    except Exception:
        return


def render(stdin_text: str) -> str:
    """Top-level entrypoint for ``mnemo statusline``. Parses CC's payload,
    probes the daemon, reads the inject count, formats one line. NEVER
    raises."""
    session_id = None
    try:
        # Parse from the first "{": Windows shells prepend a UTF-8 BOM to
        # pipes, arriving as U+FEFF or cp1252 mojibake depending on the
        # console codepage; json.loads rejects both.
        raw = stdin_text or ""
        start = raw.find("{")
        if start != -1:
            session_id = json.loads(raw[start:]).get("session_id")
    except Exception:
        session_id = None
    return format_statusline(probe_health(), read_inject_count(session_id))
