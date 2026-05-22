# Auto-starting the mnemo daemon on Windows

The mnemo daemon is meant to be running whenever you're at your machine —
the Claude Code plugin hooks, the Claude Desktop MCP subprocess, and
everything else assumes `http://127.0.0.1:7373` answers. v5.8.1 ships a
production-grade Task Scheduler autostart so the daemon comes up at user
logon without manual intervention.

## Why Task Scheduler (not the Startup folder)

The pre-v5.8.1 autostart was a `.vbs` script dropped into
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`. Two real
limitations:

1. **Fire-and-forget.** The .vbs spawned `mnemo daemon start` and
   returned immediately — no health check. If `mnemo.exe` was missing or
   the daemon failed to bind, the failure was silent and the user only
   noticed when Claude Desktop's MCP queries started returning thin
   results (or `mnemo daemon status` reported `not running`).
2. **No retry.** A transient failure (D: drive not yet mounted, Python
   environment not yet warmed) meant the daemon stayed down until manual
   restart.

Task Scheduler fixes both: the wrapper script polls `/v1/health` until
the daemon answers (or times out), the task itself auto-retries 3 times
with a 30 s gap on failure, and the task is visible in Task Manager and
`schtasks.exe` for diagnosis.

## Install

```powershell
cd D:\Repository\knowledge-base
powershell -ExecutionPolicy Bypass -File scripts\windows-autostart\install-task.ps1
```

Idempotent — re-running just refreshes the existing task definition.

The installer prints follow-up commands you can copy:

```
Test now (without waiting for logon):
  Start-ScheduledTask -TaskName mnemo-daemon-autostart

View status:
  Get-ScheduledTask -TaskName mnemo-daemon-autostart | Get-ScheduledTaskInfo

Logs at: %APPDATA%\Claude\mnemo\logs\autostart.log
```

If you previously installed the v5.8.0-and-earlier `.vbs` autostart,
remove it before logout-logon:

```powershell
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\mnemo-daemon.vbs" -ErrorAction SilentlyContinue
```

(The .vbs is harmless to leave — it just races the scheduled task — but
removing it keeps the Startup folder uncluttered.)

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows-autostart\uninstall-task.ps1
```

The mnemo daemon itself is unaffected; only the autostart wiring is
removed. Start the daemon manually with `mnemo daemon start` afterwards.

## What's in the task

| Setting | Value |
|---|---|
| Name | `mnemo-daemon-autostart` |
| Trigger | At logon (current user only) |
| Action | `powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File scripts\windows-autostart\mnemo-autostart.ps1` |
| Restart on failure | 3 attempts, 30 s gap |
| Execution time limit | 5 minutes |
| Hidden | yes (no console flash) |
| Run level | Limited (no UAC prompt) |

## Logs

Every autostart attempt appends a timestamped line to:

```
%APPDATA%\Claude\mnemo\logs\autostart.log
```

Successful boot looks like:

```
2026-05-22 19:55:32.018 autostart fired (pid 12340)
2026-05-22 19:55:32.301 spawned D:\Repository\knowledge-base\daemon\.venv\Scripts\mnemo.exe daemon start (pid 12380)
2026-05-22 19:55:34.119 daemon healthy at http://127.0.0.1:7373/v1/health after 2s
```

A failure looks like:

```
2026-05-22 19:55:32.018 autostart fired (pid 12340)
2026-05-22 19:55:32.301 spawned ... (pid 12380)
2026-05-22 19:56:32.844 FATAL: daemon did not answer http://127.0.0.1:7373/v1/health within 60s. Last error: ...
```

When you see a FATAL line, check:

1. **Does `mnemo daemon start` work manually?** If not, the issue is the
   daemon itself, not the autostart.
2. **Is the path to `mnemo.exe` correct?** The wrapper hardcodes
   `D:\Repository\knowledge-base\daemon\.venv\Scripts\mnemo.exe` —
   override with `-MnemoExe <path>` if you cloned the repo elsewhere.
3. **Did the orphan-detection in v5.6.0 catch a stuck pid file?** Run
   `mnemo daemon status` — if it reports `orphaned=True`, the listener
   pid is recoverable via `mnemo daemon stop` (v5.6.0 made stop()
   target the actual listener, not the stale pid-file pid).

## Parallel autostart on macOS / Linux

v5.10.0 ships matching scripts for the other two platforms following
the same contract (wrapper polls `/v1/health`, structured log file,
auto-retry on failure, idempotent install + clean uninstall):

- **macOS**: `scripts/macos-autostart/` + [docs/autostart-macos.md](autostart-macos.md) (launchd user agent, `KeepAlive` for retry).
- **Linux**: `scripts/linux-autostart/` + [docs/autostart-linux.md](autostart-linux.md) (systemd-user unit, `Restart=on-failure`).

The canonical service identifiers across platforms:

| Platform | Identifier | Lookup |
|---|---|---|
| Windows | `mnemo-daemon-autostart` (Task Scheduler task name) | `Get-ScheduledTask mnemo-daemon-autostart` |
| macOS | `com.mnemo.daemon` (launchd Label) | `launchctl list \| grep com.mnemo.daemon` |
| Linux | `mnemo-daemon.service` (systemd-user unit) | `systemctl --user status mnemo-daemon.service` |
