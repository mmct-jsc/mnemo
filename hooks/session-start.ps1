# mnemo SessionStart hook (PowerShell). Fails open: silent if mnemo missing.
$ErrorActionPreference = 'SilentlyContinue'

$mnemo = Get-Command mnemo -ErrorAction SilentlyContinue
if (-not $mnemo) { exit 0 }

$status = & mnemo status 2>$null
if ($LASTEXITCODE -ne 0 -or -not $status) { exit 0 }

@"
## mnemo memory map

``````
$status
``````

Use ``/mnemo-query <text>`` for ad-hoc memory recall, or let auto-injection do it for you.
"@
