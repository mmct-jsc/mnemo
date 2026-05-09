# mnemo PostToolUse hook (PowerShell). Async reindex on memory-shaped edits.
$ErrorActionPreference = 'SilentlyContinue'

$mnemo = Get-Command mnemo -ErrorAction SilentlyContinue
if (-not $mnemo) { exit 0 }

$payload = $input | Out-String
if (-not $payload) { exit 0 }

try {
    $obj = $payload | ConvertFrom-Json
    $path = $obj.tool_input.file_path
} catch {
    exit 0
}
if (-not $path) { exit 0 }

if ($path -match '/memory/.*\.md$' -or $path -match '/CLAUDE\.md$' -or $path -match '/docs/plans/.*\.md$') {
    # Detached background reindex.
    Start-Process -FilePath $mnemo.Source `
        -ArgumentList 'reindex', '--no-embed' `
        -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
}

exit 0
