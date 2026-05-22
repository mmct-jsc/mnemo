# install-task.ps1 - register the mnemo daemon autostart with Windows Task Scheduler.
#
# v5.8.1: production-grade replacement for the Startup-folder .vbs. Run this
# once after installing mnemo (or after `git pull` if the wrapper changed):
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows-autostart\install-task.ps1
#
# Idempotent - re-running just updates the existing task.
#
# Why Task Scheduler over Startup folder:
#  - Fires earlier on logon (Startup folder waits for shell init).
#  - Auto-retry: 3 attempts with 30s gap, configured below.
#  - Visible in Task Manager + schtasks.exe for diagnosis.
#  - Survives explorer.exe crash + restart.
#  - Decouples from the Startup folder hack, which Windows Defender
#    occasionally flags as a script-on-logon risk.

[CmdletBinding()]
param(
    [string]$TaskName = 'mnemo-daemon-autostart',
    [string]$WrapperScript = ''
)

$ErrorActionPreference = 'Stop'

# Resolve the wrapper path AFTER the param block. $PSScriptRoot is reliably
# populated by then; using it as a param default sometimes hits an empty
# string under PS 5.1 when invoked via ``powershell -File ...``.
if ([string]::IsNullOrEmpty($WrapperScript)) {
    $scriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $WrapperScript = Join-Path $scriptRoot 'mnemo-autostart.ps1'
}

if (-not (Test-Path $WrapperScript)) {
    throw "wrapper script missing: $WrapperScript"
}

# Action: run PowerShell on the wrapper script with execution policy bypass +
# no profile (faster startup, no user-rc pollution).
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$WrapperScript`""

# Trigger: at logon of the CURRENT user only (Task Scheduler will substitute
# the current user's SID at register-time).
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Settings: run hidden, retry on failure, kill if stuck.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -Hidden

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'mnemo daemon autostart (v5.8.1 production wrapper). Starts the mnemo HTTP daemon at user logon and polls /v1/health until it answers. Replaces the legacy Startup-folder .vbs. Removable via uninstall-task.ps1.'

# Idempotent register - overwrites existing entry with the same name.
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' (logon trigger, 3-retry, hidden)."
Write-Host ""
Write-Host "Test now (without waiting for logon):"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
Write-Host ""
Write-Host "View status:"
Write-Host "  Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host ""
Write-Host "Logs at: $env:APPDATA\Claude\mnemo\logs\autostart.log"
Write-Host ""
Write-Host "Don't forget: the old Startup-folder .vbs should be removed if you used it."
Write-Host "  Remove-Item `"$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\mnemo-daemon.vbs`" -ErrorAction SilentlyContinue"
