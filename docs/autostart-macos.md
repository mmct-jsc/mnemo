# Auto-starting the mnemo daemon on macOS

The mnemo daemon is meant to be running whenever you're at your machine —
the Claude Code plugin hooks, the Claude Desktop MCP subprocess, and
everything else assumes `http://127.0.0.1:7373` answers. v5.10.0 ships a
production-grade launchd user agent so the daemon comes up at user
logon without manual intervention.

This is the macOS parallel of `docs/autostart-windows.md` (Task
Scheduler, v5.8.1) and `docs/autostart-linux.md` (systemd-user, v5.10.0).
Same contract — different surface.

## Why launchd (not a login item)

The naive autostart approach on macOS is a "Login Item" in System
Settings → General → Login Items. Two real limitations:

1. **Fire-and-forget.** A login item just launches the binary at logon
   and forgets it. No health check, no log. If `mnemo` is missing or
   the daemon fails to bind to `127.0.0.1:7373`, the failure is silent
   and you only notice when Claude Desktop's MCP queries start
   returning thin results.
2. **No retry.** A transient failure (network not yet up, keyring
   prompt timing out) means the daemon stays down until manual
   restart.

launchd fixes both: the wrapper script polls `/v1/health` until the
daemon answers (or times out), `KeepAlive` respawns the agent on
unexpected exit, and `launchctl list` shows whether the agent is
loaded for diagnosis.

## Install

```bash
cd ~/path/to/mnemo
bash scripts/macos-autostart/install-launchd.sh
```

Idempotent — re-running unloads the existing agent first, then
rewrites and reloads.

The installer prints follow-up commands you can copy:

```
Test now (without waiting for next logon):
  launchctl kickstart -k gui/$(id -u)/com.mnemo.daemon

View status:
  launchctl list | grep com.mnemo.daemon

Logs at: ~/Library/Logs/mnemo/autostart.log
  (also launchd-stdout.log / launchd-stderr.log for raw process output)
```

If `mnemo` isn't on your PATH (e.g. you installed into a venv that
isn't sourced by your login shell), set `MNEMO_BIN` before running the
installer:

```bash
MNEMO_BIN=/abs/path/to/.venv/bin/mnemo bash scripts/macos-autostart/install-launchd.sh
```

## Uninstall

```bash
bash scripts/macos-autostart/uninstall-launchd.sh
```

The mnemo daemon itself is unaffected; only the autostart wiring is
removed. Start the daemon manually with `mnemo daemon start` afterwards.

## What's in the agent

| Setting | Value |
|---|---|
| Label | `com.mnemo.daemon` |
| Trigger | `RunAtLoad` (fires at agent load, i.e. at user logon) |
| ProgramArguments | `/bin/bash scripts/macos-autostart/mnemo-autostart.sh` |
| Restart on failure | `KeepAlive` with `SuccessfulExit=false` (respawns on unexpected exit) |
| Throttle | 60 s between respawns |
| ProcessType | Background (lower scheduling priority) |
| Plist location | `~/Library/LaunchAgents/com.mnemo.daemon.plist` |

## Logs

Every autostart attempt appends a timestamped line to:

```
~/Library/Logs/mnemo/autostart.log
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

When you see a FATAL line, check:

1. **Does `mnemo daemon start` work manually?** If not, the issue is
   the daemon itself, not the autostart.
2. **Is the path to `mnemo` correct?** The installer captures
   `command -v mnemo` at install time and bakes it into the rendered
   plist. If you moved the venv since installing, re-run the
   installer (it's idempotent).
3. **Did the orphan-detection in v5.6.0 catch a stuck pid file?** Run
   `mnemo daemon status` — if it reports `orphaned=True`, the
   listener pid is recoverable via `mnemo daemon stop` (v5.6.0 made
   `stop()` target the actual listener, not the stale pid-file pid).

## Anti-goal

This file documents macOS-only autostart. Linux users see
`docs/autostart-linux.md` (systemd-user). Windows users see
`docs/autostart-windows.md` (Task Scheduler). All three follow the
same contract: wrapper polls `/v1/health`, structured log file,
auto-retry on failure, idempotent install + clean uninstall.
