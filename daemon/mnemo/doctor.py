"""mnemo doctor -- end-to-end install verifier (v5.24.0).

The pre-v5.24.0 install path fails OPEN: hooks ``command -v mnemo || exit 0``
and installer warnings-not-errors mean a half-wired install looks identical to
no install. ``mnemo doctor`` replaces that silence with a LOUD, actionable
checklist -- one line per link in the chain, a concrete fix for each problem,
and a nonzero exit if a REQUIRED link is broken.

Each ``check_*`` is a pure function with its dependencies injected, so it is
unit-testable against a synthetic environment. ``gather()`` wires the real
implementations; ``render()`` formats + computes the exit code.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CheckResult:
    """One row of the doctor report.

    ``ok``: True -> [ok], False -> [FAIL], None -> [?] (couldn't determine).
    ``required``: a False on a required check makes ``doctor`` exit nonzero.
    """

    name: str
    ok: bool | None
    detail: str
    hint: str = ""
    required: bool = True


# --- individual checks (pure; deps injected) ------------------------------


def check_mnemo_on_path(*, which: Callable[[str], str | None] = shutil.which) -> CheckResult:
    path = which("mnemo")
    return CheckResult(
        name="mnemo on PATH",
        ok=bool(path),
        detail=f"found at {path}" if path else "not found on PATH",
        hint="add the mnemo shim dir (e.g. ~/.local/bin) to PATH; re-run install.sh / install.ps1",
        required=True,
    )


def check_index(node_count: int) -> CheckResult:
    return CheckResult(
        name="memory index",
        ok=node_count > 0,
        detail=f"{node_count} nodes indexed",
        hint="mnemo reindex   (downloads the MiniLM embedder ~22MB on first run)",
        required=True,
    )


def check_daemon(*, probe: Callable[[], tuple[bool, str | None]]) -> CheckResult:
    ok, version = probe()
    return CheckResult(
        name="daemon (web UI + HTTP API)",
        ok=ok,
        detail=f"running, version {version}" if ok else "not reachable on 127.0.0.1:7373",
        hint="mnemo daemon start   (optional: hooks + MCP tools work without it)",
        required=False,
    )


def check_mcp_registered(list_output: str | None) -> CheckResult:
    if list_output is None:
        return CheckResult(
            name="MCP server registered",
            ok=None,
            detail="could not run `claude mcp list` (claude not on PATH?) -- verify manually",
            hint="claude mcp add mnemo -- mnemo mcp",
            required=False,
        )
    ok = "mnemo" in list_output
    return CheckResult(
        name="MCP server registered",
        ok=ok,
        detail="mnemo present in `claude mcp list`"
        if ok
        else "mnemo absent from `claude mcp list`",
        hint="claude mcp add mnemo -- mnemo mcp",
        required=False,
    )


def check_plugin_registered(claude_home: Path) -> CheckResult:
    """The check that was silently failing: is mnemo a REGISTERED Claude Code
    plugin? Needs BOTH ``enabledPlugins`` (settings.json) and an entry in
    ``installed_plugins.json``. A bare directory in ~/.claude/plugins/ does
    NOT count -- that was the whole "no commands show up" bug.
    """
    enabled = _has_mnemo_enabled(claude_home / "settings.json")
    listed = _has_mnemo_installed(claude_home / "plugins" / "installed_plugins.json")
    ok = enabled and listed
    if ok:
        detail = "registered + enabled in Claude Code"
    elif enabled or listed:
        detail = f"PARTIALLY registered (enabled={enabled}, installed={listed})"
    else:
        detail = "not registered as a Claude Code plugin (commands/hooks will not load)"
    return CheckResult(
        name="plugin registered (Claude Code)",
        ok=ok,
        detail=detail,
        hint="/plugin marketplace add mmct-jsc/mnemo   then   /plugin install mnemo@mnemo",
        required=True,
    )


def check_statusline(settings_path: Path) -> CheckResult:
    """Advisory (v5.25.0): is mnemo's statusline wired into Claude Code
    settings.json? An optional presence cue, so 'not configured' renders as
    [?] (not [FAIL]) and never affects the exit code."""
    from mnemo.statusline import statusline_is_mnemo

    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    ok = statusline_is_mnemo(data) if isinstance(data, dict) else False
    return CheckResult(
        name="statusline (Claude Code)",
        ok=True if ok else None,
        detail="mnemo statusline configured" if ok else "not configured (optional presence cue)",
        hint="mnemo statusline-setup",
        required=False,
    )


def _has_mnemo_enabled(settings_path: Path) -> bool:
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    enabled = data.get("enabledPlugins", {})
    return any(str(k).split("@", 1)[0] == "mnemo" and v for k, v in enabled.items())


def _has_mnemo_installed(installed_path: Path) -> bool:
    try:
        data = json.loads(installed_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    plugins = data.get("plugins", {})
    return any(str(k).split("@", 1)[0] == "mnemo" for k in plugins)


# --- real-environment wiring ----------------------------------------------


def _default_daemon_probe() -> tuple[bool, str | None]:
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:7373/v1/health", timeout=2) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        return True, data.get("version")
    except Exception:
        return False, None


def _default_mcp_list() -> str | None:
    import subprocess

    if not shutil.which("claude"):
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            ["claude", "mcp", "list"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=15,
        )
        return (proc.stdout or "") + (proc.stderr or "")
    except Exception:
        return None


def _node_count() -> int:
    from mnemo import paths
    from mnemo.store import Store

    try:
        store = Store(paths.db_path())
    except Exception:
        return 0
    try:
        return sum(store.count_nodes().values())
    except Exception:
        return 0
    finally:
        store.close()


def gather() -> list[CheckResult]:
    """Run every check against the live environment."""
    from mnemo import paths

    return [
        check_mnemo_on_path(),
        check_index(_node_count()),
        check_plugin_registered(paths.claude_home()),
        check_statusline(paths.claude_home() / "settings.json"),
        check_daemon(probe=_default_daemon_probe),
        check_mcp_registered(_default_mcp_list()),
    ]


# --- rendering ------------------------------------------------------------

_GLYPH = {True: "[ok]  ", False: "[FAIL]", None: "[?]   "}


def render(results: list[CheckResult]) -> tuple[str, int]:
    """Format the report + compute the exit code (1 if any required [FAIL])."""
    lines = ["mnemo doctor", ""]
    exit_code = 0
    for r in results:
        lines.append(f"  {_GLYPH[r.ok]} {r.name}: {r.detail}")
        if r.ok is not True and r.hint:
            lines.append(f"           fix: {r.hint}")
        if r.ok is False and r.required:
            exit_code = 1
    lines.append("")
    lines.append(
        "all required checks passed."
        if exit_code == 0
        else "some REQUIRED checks failed -- apply the fixes above, then re-run `mnemo doctor`."
    )
    return "\n".join(lines), exit_code
