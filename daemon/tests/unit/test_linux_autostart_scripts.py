"""v5.10.0 -- Linux systemd-user autostart scripts.

Mirror of ``test_windows_autostart_scripts.py`` (v5.8.1) and
``test_macos_autostart_scripts.py`` (v5.10.0) for the Linux platform.
v5.10.0 closes the open invitation in ``docs/autostart-windows.md``'s
anti-goal section by shipping the parallel scripts. Same contract
shape; different surface (systemd-user unit + ``systemctl --user``
instead of Task Scheduler / launchd).

Contract this test file locks:

1. ``scripts/linux-autostart/mnemo-autostart.sh`` exists, polls
   ``/v1/health``, and spawns the ``mnemo daemon start`` CLI.
2. ``scripts/linux-autostart/mnemo-daemon.service.template`` exists
   and contains the canonical systemd unit keys (``[Unit]``,
   ``[Service]``, ``[Install]``, plus ``ExecStart`` + ``Restart``).
3. ``scripts/linux-autostart/install-systemd.sh`` exists, renders the
   template into ``~/.config/systemd/user/``, and calls
   ``systemctl --user`` to daemon-reload + enable + start the unit.
4. ``scripts/linux-autostart/uninstall-systemd.sh`` exists and calls
   ``systemctl --user`` to disable + stop the unit, then removes the
   file.
5. ``docs/autostart-linux.md`` exists, links all three scripts, and
   names the canonical unit name (``mnemo-daemon.service``) so users
   running ``systemctl --user list-units`` know what to look for.

Same rationale as v5.8.1 / macOS: we don't run systemctl in CI; the
test verifies file presence + key strings so a typo surfaces as a
unit-test failure on every platform.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "linux-autostart"
DOC_FILE = REPO_ROOT / "docs" / "autostart-linux.md"


def test_autostart_wrapper_exists() -> None:
    p = SCRIPTS_DIR / "mnemo-autostart.sh"
    assert p.is_file(), f"missing wrapper script at {p}"


def test_autostart_wrapper_polls_health_endpoint() -> None:
    p = SCRIPTS_DIR / "mnemo-autostart.sh"
    text = p.read_text(encoding="utf-8")
    assert "v1/health" in text, (
        "wrapper must poll /v1/health to verify the daemon is actually "
        "listening before exiting 0 (same contract as Windows v5.8.1 -- "
        "fire-and-forget is the bug we're fixing on every platform)."
    )
    assert "curl" in text, (
        "wrapper must use 'curl' for the health probe (bash + curl is the "
        "Linux equivalent of PowerShell's Invoke-WebRequest)."
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


def test_systemd_unit_template_has_canonical_sections() -> None:
    p = SCRIPTS_DIR / "mnemo-daemon.service.template"
    assert p.is_file(), f"missing systemd unit template at {p}"
    text = p.read_text(encoding="utf-8")
    # Three canonical systemd unit sections.
    for section in ("[Unit]", "[Service]", "[Install]"):
        assert section in text, (
            f"systemd unit template missing {section} -- a user unit needs "
            f"all three canonical sections to load + enable cleanly."
        )
    # ExecStart drives the wrapper, Restart=on-failure provides retry.
    assert "ExecStart=" in text, "unit template missing ExecStart= -- systemd has nothing to run."
    assert "Restart=" in text, (
        "unit template missing Restart= -- without it transient failures "
        "(D: drive not mounted, network not yet ready) leave the daemon "
        "down. Retry-on-failure is the whole point vs raw .desktop autostart."
    )
    # default.target is the user-session equivalent of multi-user.target.
    assert "default.target" in text, (
        "unit template must wire WantedBy=default.target so 'systemctl --user "
        "enable' actually enables it at user-session startup."
    )


def test_install_systemd_reloads_and_enables() -> None:
    p = SCRIPTS_DIR / "install-systemd.sh"
    assert p.is_file(), f"missing installer at {p}"
    text = p.read_text(encoding="utf-8")
    assert "systemd/user" in text, (
        "installer must drop the rendered unit into ~/.config/systemd/user/ "
        "(the user-level systemd directory; system-level needs root)."
    )
    assert "systemctl" in text, (
        "installer must call systemctl --user to register + start the unit. "
        "Without it the unit is on disk but never evaluated."
    )
    assert "daemon-reload" in text, (
        "installer must call 'systemctl --user daemon-reload' so systemd "
        "picks up the new unit file (otherwise enable + start fail with "
        "Unit not found)."
    )
    assert "enable" in text, (
        "installer must call 'systemctl --user enable' so the unit fires "
        "at next user-session start (not just for the current session)."
    )
    assert "mnemo-daemon" in text, (
        "installer must reference the canonical unit name 'mnemo-daemon' "
        "so the rendered unit matches what the uninstaller expects to stop."
    )


def test_uninstall_systemd_disables_and_stops() -> None:
    p = SCRIPTS_DIR / "uninstall-systemd.sh"
    assert p.is_file(), f"missing uninstaller at {p}"
    text = p.read_text(encoding="utf-8")
    assert "systemctl" in text, "uninstaller must call systemctl --user to disable + stop the unit."
    assert "disable" in text, (
        "uninstaller must call 'systemctl --user disable' so the unit "
        "doesn't fire at next user-session start."
    )
    assert "stop" in text, (
        "uninstaller must call 'systemctl --user stop' to terminate the "
        "currently-running daemon (disable alone leaves the daemon up "
        "until next reboot)."
    )
    assert "mnemo-daemon" in text, (
        "uninstaller must reference the canonical unit name 'mnemo-daemon' "
        "to target the same unit the installer registered."
    )


def test_autostart_doc_exists_and_links_all_scripts() -> None:
    assert DOC_FILE.is_file(), f"missing autostart docs at {DOC_FILE}"
    text = DOC_FILE.read_text(encoding="utf-8")
    assert "install-systemd.sh" in text, "doc must reference install-systemd.sh"
    assert "uninstall-systemd.sh" in text, "doc must reference uninstall-systemd.sh"
    assert "mnemo-autostart.sh" in text, "doc must reference mnemo-autostart.sh"
    assert "mnemo-daemon.service" in text, (
        "doc must reference the canonical unit name (mnemo-daemon.service) "
        "so users running 'systemctl --user list-units | grep mnemo' know "
        "what to look for."
    )
