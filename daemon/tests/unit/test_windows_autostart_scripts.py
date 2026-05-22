"""v5.8.1 -- Windows autostart Task Scheduler scripts.

Contract this test file locks:

1. ``scripts/windows-autostart/mnemo-autostart.ps1`` exists and references
   the canonical ``mnemo.exe`` path + the ``/v1/health`` endpoint.
2. ``scripts/windows-autostart/install-task.ps1`` exists and uses
   ``Register-ScheduledTask`` with a logon trigger.
3. ``scripts/windows-autostart/uninstall-task.ps1`` exists and uses
   ``Unregister-ScheduledTask``.
4. ``docs/autostart-windows.md`` exists and links to all three scripts.

We don't actually run PowerShell in CI (the runners are Linux + macOS
for most jobs); the test verifies structure + key strings so a typo
in the docs / scripts surface as a unit-test failure.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "windows-autostart"
DOC_FILE = REPO_ROOT / "docs" / "autostart-windows.md"


def test_autostart_wrapper_exists() -> None:
    p = SCRIPTS_DIR / "mnemo-autostart.ps1"
    assert p.is_file(), f"missing wrapper script at {p}"


def test_autostart_wrapper_polls_health_endpoint() -> None:
    p = SCRIPTS_DIR / "mnemo-autostart.ps1"
    text = p.read_text(encoding="utf-8")
    assert "v1/health" in text, (
        "wrapper must poll /v1/health to verify the daemon is actually "
        "listening before exiting 0 (the whole reason we replaced the "
        "fire-and-forget .vbs)."
    )
    assert "Invoke-WebRequest" in text or "Invoke-RestMethod" in text, (
        "wrapper must use a PowerShell HTTP probe (Invoke-WebRequest / "
        "Invoke-RestMethod) for the health check."
    )


def test_autostart_wrapper_spawns_mnemo_exe() -> None:
    p = SCRIPTS_DIR / "mnemo-autostart.ps1"
    text = p.read_text(encoding="utf-8")
    assert "mnemo.exe" in text, (
        "wrapper must spawn the editable-install mnemo.exe (not bash, not the .py file directly)."
    )
    assert "daemon" in text, "wrapper must call the 'daemon' subcommand"
    assert "start" in text, (
        "wrapper must call 'mnemo daemon start' (the CLI surface that "
        "forks a detached --foreground subprocess)."
    )


def test_install_task_uses_register_scheduledtask() -> None:
    p = SCRIPTS_DIR / "install-task.ps1"
    assert p.is_file(), f"missing installer at {p}"
    text = p.read_text(encoding="utf-8")
    assert "Register-ScheduledTask" in text, (
        "installer must use Register-ScheduledTask (the official Task Scheduler cmdlet)."
    )
    assert "AtLogOn" in text, (
        "installer must use an AtLogOn trigger (-AtLogOn) so the task fires at user-logon time."
    )
    assert "RestartCount" in text, (
        "installer must configure auto-retry (-RestartCount); the whole "
        "point vs the .vbs is automatic recovery from transient failures."
    )


def test_uninstall_task_uses_unregister_scheduledtask() -> None:
    p = SCRIPTS_DIR / "uninstall-task.ps1"
    assert p.is_file(), f"missing uninstaller at {p}"
    text = p.read_text(encoding="utf-8")
    assert "Unregister-ScheduledTask" in text, (
        "uninstaller must use Unregister-ScheduledTask. Anything else "
        "leaves a stale task definition that re-fires on next logon."
    )


def test_autostart_doc_exists_and_links_all_scripts() -> None:
    assert DOC_FILE.is_file(), f"missing autostart docs at {DOC_FILE}"
    text = DOC_FILE.read_text(encoding="utf-8")
    assert "install-task.ps1" in text, "doc must reference install-task.ps1"
    assert "uninstall-task.ps1" in text, "doc must reference uninstall-task.ps1"
    assert "mnemo-autostart.ps1" in text, "doc must reference mnemo-autostart.ps1"
    assert "mnemo-daemon-autostart" in text, (
        "doc must reference the canonical task name (mnemo-daemon-autostart) "
        "so users running 'Get-ScheduledTask' or 'schtasks /query' know what "
        "to look for."
    )
