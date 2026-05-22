"""v5.10.0 -- macOS launchd autostart scripts.

Mirror of ``test_windows_autostart_scripts.py`` (v5.8.1) for the macOS
platform. v5.10.0 closes the open invitation in
``docs/autostart-windows.md``'s anti-goal section by shipping the
parallel scripts. Same contract shape; different surface (launchd
plist + ``launchctl`` instead of Task Scheduler + ``Register-ScheduledTask``).

Contract this test file locks:

1. ``scripts/macos-autostart/mnemo-autostart.sh`` exists, polls
   ``/v1/health``, and spawns the ``mnemo daemon start`` CLI.
2. ``scripts/macos-autostart/com.mnemo.daemon.plist.template`` exists
   and contains the canonical launchd keys (Label, ProgramArguments,
   RunAtLoad, KeepAlive).
3. ``scripts/macos-autostart/install-launchd.sh`` exists, renders the
   template into ``~/Library/LaunchAgents/``, and calls ``launchctl load``.
4. ``scripts/macos-autostart/uninstall-launchd.sh`` exists and calls
   ``launchctl unload`` for the canonical Label.
5. ``docs/autostart-macos.md`` exists, links all three scripts, and
   names the canonical launchd Label (``com.mnemo.daemon``) so users
   running ``launchctl list`` know what to look for.

We don't actually run ``launchctl`` in CI (only Windows runners have
Windows Task Scheduler; only macOS runners would have launchd). The
test verifies file presence + key strings so a typo surfaces as a
unit-test failure on every platform, identical to v5.8.1's approach.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "macos-autostart"
DOC_FILE = REPO_ROOT / "docs" / "autostart-macos.md"


def test_autostart_wrapper_exists() -> None:
    p = SCRIPTS_DIR / "mnemo-autostart.sh"
    assert p.is_file(), f"missing wrapper script at {p}"


def test_autostart_wrapper_polls_health_endpoint() -> None:
    p = SCRIPTS_DIR / "mnemo-autostart.sh"
    text = p.read_text(encoding="utf-8")
    assert "v1/health" in text, (
        "wrapper must poll /v1/health to verify the daemon is actually "
        "listening before exiting 0 (same contract as Windows v5.8.1 -- "
        "the whole reason we replaced fire-and-forget Startup wiring)."
    )
    assert "curl" in text, (
        "wrapper must use 'curl' for the health probe; bash + curl is the "
        "macOS / Linux equivalent of PowerShell's Invoke-WebRequest."
    )


def test_autostart_wrapper_spawns_mnemo_cli() -> None:
    p = SCRIPTS_DIR / "mnemo-autostart.sh"
    text = p.read_text(encoding="utf-8")
    assert "mnemo" in text, "wrapper must spawn the mnemo CLI (the editable-install entry point)."
    assert "daemon" in text, "wrapper must call the 'daemon' subcommand"
    assert "start" in text, (
        "wrapper must call 'mnemo daemon start' (the CLI surface that "
        "forks a detached --foreground subprocess)."
    )


def test_plist_template_has_canonical_keys() -> None:
    p = SCRIPTS_DIR / "com.mnemo.daemon.plist.template"
    assert p.is_file(), f"missing launchd plist template at {p}"
    text = p.read_text(encoding="utf-8")
    # Canonical Label (also matches the docs + uninstaller).
    assert "com.mnemo.daemon" in text, (
        "plist template must declare Label 'com.mnemo.daemon' so launchctl "
        "list shows a stable, greppable identifier."
    )
    # The four launchd keys that make this an autostart-with-retry agent.
    for key in ("Label", "ProgramArguments", "RunAtLoad", "KeepAlive"):
        assert f"<key>{key}</key>" in text, (
            f"plist template missing <key>{key}</key> -- launchd needs all "
            f"four canonical keys (Label + ProgramArguments + RunAtLoad + "
            f"KeepAlive) to act as an autostart-with-retry agent."
        )


def test_install_launchd_loads_agent() -> None:
    p = SCRIPTS_DIR / "install-launchd.sh"
    assert p.is_file(), f"missing installer at {p}"
    text = p.read_text(encoding="utf-8")
    assert "LaunchAgents" in text, (
        "installer must drop the rendered plist into ~/Library/LaunchAgents/ "
        "(the user-level launchd directory; system-level needs root)."
    )
    assert "launchctl" in text, (
        "installer must call launchctl (load / bootstrap) to register the "
        "agent with launchd. Without it the plist is on disk but never "
        "evaluated."
    )
    assert "com.mnemo.daemon" in text, (
        "installer must reference the canonical Label so the rendered plist "
        "matches what the uninstaller expects to unload."
    )


def test_uninstall_launchd_unloads_agent() -> None:
    p = SCRIPTS_DIR / "uninstall-launchd.sh"
    assert p.is_file(), f"missing uninstaller at {p}"
    text = p.read_text(encoding="utf-8")
    assert "launchctl" in text, (
        "uninstaller must call launchctl (unload / bootout) to detach the "
        "agent from launchd. Anything else leaves a stale plist that "
        "re-fires on next logon."
    )
    assert "com.mnemo.daemon" in text, (
        "uninstaller must reference the canonical Label so it targets the "
        "same agent the installer registered."
    )


def test_autostart_doc_exists_and_links_all_scripts() -> None:
    assert DOC_FILE.is_file(), f"missing autostart docs at {DOC_FILE}"
    text = DOC_FILE.read_text(encoding="utf-8")
    assert "install-launchd.sh" in text, "doc must reference install-launchd.sh"
    assert "uninstall-launchd.sh" in text, "doc must reference uninstall-launchd.sh"
    assert "mnemo-autostart.sh" in text, "doc must reference mnemo-autostart.sh"
    assert "com.mnemo.daemon" in text, (
        "doc must reference the canonical launchd Label (com.mnemo.daemon) "
        "so users running 'launchctl list | grep mnemo' know what to look for."
    )
