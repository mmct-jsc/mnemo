# mnemo installer (Windows / PowerShell 5.1+).
#
# Idempotent: rerunning on an already-installed setup is a no-op.
#
# Steps:
#   1. Verify Python 3.11+ and uv are available.
#   2. Sync the daemon's dependencies via 'uv sync'.
#   3. Drop a 'mnemo.cmd' shim into ~/.local/bin so it's on PATH.
#   4. Junction the plugin scaffold into ~/.claude/plugins/mnemo so Claude
#      Code picks up the hooks, commands, and skills.
#   5. Run 'mnemo init' to register Scope B sources.
#
# Flags:
#   -NoInit          Skip step 5.
#   -NoPluginLink    Skip step 4.
#   -BinDir <path>   Override the install dir for the 'mnemo.cmd' shim.

[CmdletBinding()]
param(
    [switch]$NoInit,
    [switch]$NoPluginLink,
    [string]$BinDir = (Join-Path $HOME '.local\bin')
)

$ErrorActionPreference = 'Stop'

$RepoRoot   = (Resolve-Path (Split-Path -Parent $PSCommandPath)).Path
$DaemonDir  = Join-Path $RepoRoot 'daemon'
$PluginDest = Join-Path $HOME '.claude\plugins\mnemo'

function Log  { param($m) Write-Host "[mnemo] $m" -ForegroundColor Cyan }
function Ok   { param($m) Write-Host "[ok]    $m" -ForegroundColor Green }
function Warn { param($m) Write-Host "[warn]  $m" -ForegroundColor Yellow }
function Fail { param($m) Write-Host "[fail]  $m" -ForegroundColor Red; exit 1 }

# --- 1. Prerequisites -----------------------------------------------------

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $python) {
    Fail 'python not found. Install Python 3.11+ first.'
}
$pyVersion = & $python.Source -c 'import sys; print("%d.%d" % sys.version_info[:2])'
$parts = $pyVersion.Split('.')
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
    Fail "Python 3.11+ required (found $pyVersion)."
}
Ok "Python $pyVersion"

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Warn 'uv not found. Install with:'
    Warn '  irm https://astral.sh/uv/install.ps1 | iex'
    Fail 'Install uv, then re-run this script.'
}
$uvVersion = (& $uv.Source --version).Split(' ')[1]
Ok "uv $uvVersion"

# --- 2. Sync daemon deps --------------------------------------------------

Log 'syncing daemon dependencies (this may take a minute on first run)'
Push-Location $DaemonDir
try {
    & $uv.Source sync
    if ($LASTEXITCODE -ne 0) { Fail 'uv sync failed' }
} finally {
    Pop-Location
}
Ok 'daemon deps installed'

# --- 3. Shim the mnemo binary onto PATH -----------------------------------

$venvBin = Join-Path $DaemonDir '.venv\Scripts\mnemo.exe'
if (-not (Test-Path $venvBin)) {
    Fail "expected venv binary at $venvBin but it's missing"
}

if (-not (Test-Path $BinDir)) {
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
}

$shim = Join-Path $BinDir 'mnemo.cmd'
$shimContent = "@echo off`r`n""$venvBin"" %*`r`n"
$existing = ''
if (Test-Path $shim) {
    $existing = Get-Content $shim -Raw -ErrorAction SilentlyContinue
}
if ($existing -eq $shimContent) {
    Ok "mnemo shim already present at $shim"
} else {
    Set-Content -Path $shim -Value $shimContent -NoNewline -Encoding ASCII
    Ok "mnemo shim -> $shim"
}

$pathDirs = $env:PATH -split ';'
if ($pathDirs -notcontains $BinDir) {
    Warn "$BinDir is not on PATH."
    Warn 'Add it for the current user with:'
    Warn "  [Environment]::SetEnvironmentVariable('PATH', `"$BinDir;`" + [Environment]::GetEnvironmentVariable('PATH', 'User'), 'User')"
    Warn '...then open a new shell.'
}

# --- 4. Plugin link -------------------------------------------------------

if (-not $NoPluginLink) {
    $pluginParent = Split-Path -Parent $PluginDest
    if (-not (Test-Path $pluginParent)) {
        New-Item -ItemType Directory -Path $pluginParent -Force | Out-Null
    }
    if (Test-Path $PluginDest) {
        $item = Get-Item $PluginDest -Force
        $isOurs = $item.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint) -and `
                  ($item.Target -eq $RepoRoot)
        if ($isOurs) {
            Ok "plugin already linked at $PluginDest"
        } else {
            Warn "$PluginDest exists and is not our junction; leaving it alone."
            Warn 'Move or remove it, then rerun, or pass -NoPluginLink.'
        }
    } else {
        # Directory junction: doesn't require admin, works for read-only access.
        cmd /c mklink /J "$PluginDest" "$RepoRoot" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Fail 'mklink /J failed'
        }
        Ok "plugin linked: $PluginDest -> $RepoRoot"
    }
}

# --- 5. Register default sources ------------------------------------------

if (-not $NoInit) {
    Log 'registering default Scope B sources'
    & $shim init
    if ($LASTEXITCODE -ne 0) {
        Warn 'mnemo init failed (you can run it manually later)'
    }
}

# --- Done -----------------------------------------------------------------

Write-Host ''
Write-Host 'Done. Next steps:'
Write-Host ''
Write-Host "  - If you saw the PATH warning above, add $BinDir to PATH and open a new shell."
Write-Host "  - Run 'mnemo reindex' to ingest your existing memory (downloads MiniLM ~22MB on first run)."
Write-Host "  - Run 'mnemo daemon start' to enable the web UI at http://127.0.0.1:7373/."
Write-Host '  - Restart Claude Code so the plugin loads its hooks and slash commands.'
