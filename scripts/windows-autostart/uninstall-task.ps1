# uninstall-task.ps1 - remove the mnemo daemon Task Scheduler entry.
#
# Run this if you want to disable autostart or revert to manual start.
# Does NOT touch the daemon itself - just the scheduled task.

[CmdletBinding()]
param(
    [string]$TaskName = 'mnemo-daemon-autostart'
)

$ErrorActionPreference = 'Continue'

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "No scheduled task named '$TaskName' found - nothing to remove."
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Unregistered scheduled task '$TaskName'."
Write-Host ""
Write-Host "The mnemo daemon will NOT autostart on next logon."
Write-Host "Start it manually with: mnemo daemon start"
