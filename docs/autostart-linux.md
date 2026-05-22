# Auto-starting the mnemo daemon on Linux

The mnemo daemon is meant to be running whenever you're at your machine —
the Claude Code plugin hooks, the Claude Desktop MCP subprocess, and
everything else assumes `http://127.0.0.1:7373` answers. v5.10.0 ships a
production-grade systemd-user unit so the daemon comes up at user
logon without manual intervention.

This is the Linux parallel of `docs/autostart-windows.md` (Task
Scheduler, v5.8.1) and `docs/autostart-macos.md` (launchd, v5.10.0).
Same contract — different surface.

## Why systemd-user (not a .desktop autostart)

The naive autostart approach on Linux is a `.desktop` file dropped
into `~/.config/autostart/`. Two real limitations:

1. **Fire-and-forget.** The `.desktop` launches the binary at session
   start and forgets it. No health check, no log. If `mnemo` is
   missing or the daemon fails to bind to `127.0.0.1:7373`, the
   failure is silent and you only notice when Claude Desktop's MCP
   queries start returning thin results.
2. **No retry.** A transient failure (network not yet up, keyring
   prompt timing out, `/v1/health` probe race) means the daemon stays
   down until manual restart.

systemd-user fixes both: the wrapper script polls `/v1/health` until
the daemon answers (or times out), the unit's `Restart=on-failure`
respawns it 60 s later if it exits non-zero, and `systemctl --user
status mnemo-daemon` shows whether the unit is loaded for diagnosis.

## Install

```bash
cd ~/path/to/mnemo
bash scripts/linux-autostart/install-systemd.sh
```

Idempotent — re-running rewrites the unit file and reloads.

The installer prints follow-up commands you can copy:

```
View status:
  systemctl --user status mnemo-daemon.service

Manual restart:
  systemctl --user restart mnemo-daemon.service

Logs:
  journalctl --user -u mnemo-daemon.service -f
  (wrapper log also at $XDG_STATE_HOME/mnemo/logs/autostart.log)
```

If `mnemo` isn't on your PATH (e.g. you installed into a venv that
isn't sourced by your login shell), set `MNEMO_BIN` before running
the installer:

```bash
MNEMO_BIN=/abs/path/to/.venv/bin/mnemo bash scripts/linux-autostart/install-systemd.sh
```

### Lingering (autostart without a graphical session)

By default, systemd-user units only run while the user has at least
one active session. If you want the mnemo daemon to start at machine
boot (e.g. headless server), enable lingering once:

```bash
loginctl enable-linger $USER
```

Disable it again with `loginctl disable-linger $USER`.

## Uninstall

```bash
bash scripts/linux-autostart/uninstall-systemd.sh
```

The mnemo daemon itself is unaffected; only the autostart wiring is
removed. Start the daemon manually with `mnemo daemon start` afterwards.

## What's in the unit

| Setting | Value |
|---|---|
| Name | `mnemo-daemon.service` |
| Trigger | `WantedBy=default.target` (user-session start) |
| ExecStart | `/bin/bash scripts/linux-autostart/mnemo-autostart.sh` |
| Type | `oneshot` + `RemainAfterExit=true` |
| Restart on failure | `Restart=on-failure`, `RestartSec=60` |
| Unit location | `~/.config/systemd/user/mnemo-daemon.service` |
| Wrapper log | `$XDG_STATE_HOME/mnemo/logs/autostart.log` (defaults to `~/.local/state/mnemo/logs/autostart.log`) |
| journal | `journalctl --user -u mnemo-daemon.service` |

## Logs

Every autostart attempt appends a timestamped line to:

```
$XDG_STATE_HOME/mnemo/logs/autostart.log
(defaults to ~/.local/state/mnemo/logs/autostart.log)
```

Successful boot looks like:

```
2026-05-22 19:55:32 autostart fired (pid 12340)
2026-05-22 19:55:32 spawned mnemo daemon start
2026-05-22 19:55:34 daemon healthy at http://127.0.0.1:7373/v1/health after 2s
```

A failure looks like:

```
2026-05-22 19:55:32 autostart fired (pid 12340)
2026-05-22 19:55:32 spawned mnemo daemon start
2026-05-22 19:56:32 FATAL: daemon did not answer http://127.0.0.1:7373/v1/health within 60s. Last error: ...
```

Systemd also captures the wrapper's stdout/stderr to the journal:

```bash
journalctl --user -u mnemo-daemon.service --since "10 min ago"
```

When you see a FATAL line, check:

1. **Does `mnemo daemon start` work manually?** If not, the issue is
   the daemon itself, not the autostart.
2. **Is the path to `mnemo` correct?** The installer captures
   `command -v mnemo` at install time and bakes it into the rendered
   unit. If you moved the venv since installing, re-run the
   installer (it's idempotent).
3. **Did the orphan-detection in v5.6.0 catch a stuck pid file?** Run
   `mnemo daemon status` — if it reports `orphaned=True`, the
   listener pid is recoverable via `mnemo daemon stop` (v5.6.0 made
   `stop()` target the actual listener, not the stale pid-file pid).

## Anti-goal

This file documents Linux-only autostart. macOS users see
`docs/autostart-macos.md` (launchd). Windows users see
`docs/autostart-windows.md` (Task Scheduler). All three follow the
same contract: wrapper polls `/v1/health`, structured log file,
auto-retry on failure, idempotent install + clean uninstall.
