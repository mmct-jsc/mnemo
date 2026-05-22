# mnemo-autostart.ps1 - production wrapper for the mnemo daemon autostart.
#
# v5.8.1 replaces the Startup-folder .vbs (which fired-and-forgot) with a
# Task Scheduler entry that runs THIS wrapper. The wrapper:
#
#   1. Spawns ``mnemo daemon start`` via the editable-install ``mnemo.exe``.
#   2. Polls ``/v1/health`` for up to 60 s, exiting 0 only when the daemon
#      is provably listening and answering.
#   3. Appends a timestamped line to ``%APPDATA%\Claude\mnemo\logs\
#      autostart.log`` so future debugging has evidence the autostart fired.
#
# Task Scheduler treats exit 0 as success and exit 1 as failure (and can
# auto-retry per the task's settings). The wrapper deliberately does NOT
# swallow errors - the user can read the log + the task's last-run-result
# to diagnose.
#
# Intentionally a separate PS1 (not inlined into the scheduled task action)
# so it's editable + version-controlled + testable without touching Task
# Scheduler's XML.

[CmdletBinding()]
param(
    [string]$MnemoExe = 'D:\Repository\knowledge-base\daemon\.venv\Scripts\mnemo.exe',
    [string]$HealthUrl = 'http://127.0.0.1:7373/v1/health',
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = 'Continue'

$logDir = Join-Path $env:APPDATA 'Claude\mnemo\logs'
$null = New-Item -ItemType Directory -Path $logDir -Force -ErrorAction SilentlyContinue
$logFile = Join-Path $logDir 'autostart.log'

function Write-AutoStartLog {
    param([string]$message)
    $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff')
    Add-Content -Path $logFile -Value "$ts $message" -Encoding utf8
}

Write-AutoStartLog "autostart fired (pid $PID)"

if (-not (Test-Path $MnemoExe)) {
    Write-AutoStartLog "FATAL: mnemo.exe missing at $MnemoExe"
    exit 1
}

# Fire the daemon start. mnemo.exe forks a detached --foreground subprocess
# and returns; we don't wait on this Start-Process.
try {
    $proc = Start-Process -FilePath $MnemoExe -ArgumentList 'daemon', 'start' `
        -WindowStyle Hidden -PassThru
    Write-AutoStartLog "spawned $MnemoExe daemon start (pid $($proc.Id))"
} catch {
    Write-AutoStartLog "FATAL: Start-Process threw: $_"
    exit 1
}

# Poll /v1/health until the daemon answers or we hit the timeout.
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$lastError = $null
while ((Get-Date) -lt $deadline) {
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            Write-AutoStartLog "daemon healthy at $HealthUrl after $([int]((Get-Date) - $proc.StartTime).TotalSeconds)s"
            exit 0
        }
    } catch {
        $lastError = $_.ToString()
    }
    Start-Sleep -Milliseconds 1500
}

Write-AutoStartLog "FATAL: daemon did not answer $HealthUrl within ${TimeoutSeconds}s. Last error: $lastError"
exit 1
