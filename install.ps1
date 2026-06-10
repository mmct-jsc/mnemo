# mnemo installer (Windows / PowerShell 5.1+).
#
# Idempotent: rerunning on an already-installed setup is a no-op.
#
# Steps:
#   1. Verify Python 3.11+ and uv are available.
#   2. Sync the daemon's dependencies via 'uv sync'.
#   3. Drop a 'mnemo.cmd' shim into ~/.local/bin so it's on PATH.
#   4. Register the 'mnemo mcp' server with Claude Code + print the two
#      /plugin commands that install the plugin (commands/hooks/skills).
#      (Modern Claude Code is marketplace-driven: a directory under
#      ~/.claude/plugins is ignored unless registered, so we no longer
#      junction there -- that silently did nothing.)
#   5. Run 'mnemo init' to register Scope B sources.
#
# Flags:
#   -NoInit          Skip step 5.
#   -NoMcp           Skip the 'claude mcp add' registration in step 4.
#   -NoStatusline    Skip wiring the Claude Code status line (step 4b).
#   -BinDir <path>   Override the install dir for the 'mnemo.cmd' shim.

[CmdletBinding()]
param(
    [switch]$NoInit,
    [switch]$NoMcp,
    [switch]$NoStatusline,
    [string]$BinDir = (Join-Path $HOME '.local\bin')
)

# NB: 'Continue', not 'Stop'. On Windows PowerShell 5.1, 'Stop' turns a native
# tool's stderr progress output (uv, claude) into a terminating NativeCommandError
# even on exit 0. We instead check $LASTEXITCODE explicitly after native calls
# (the faithful port of bash's `set -e` for a native-tool orchestration script).
$ErrorActionPreference = 'Continue'

$RepoRoot   = (Resolve-Path (Split-Path -Parent $PSCommandPath)).Path
$DaemonDir  = Join-Path $RepoRoot 'daemon'

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
# NB: single-quote the format string INSIDE a PS double-quoted arg. Windows
# PowerShell 5.1 mangles embedded double-quotes when passing -c to a native
# exe, which made python raise a SyntaxError here.
$pyVersion = & $python.Source -c "import sys; print('%d.%d' % sys.version_info[:2])"
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

# --- 4. Register the MCP server + print the /plugin commands --------------
#
# Claude Code is marketplace-driven: the plugin (commands/hooks/skills) is
# enabled by two /plugin commands the USER runs INSIDE Claude Code -- not by
# copying files. We register the MCP tool server here so the mnemo_* tools
# resolve, then print those two commands at the end.

if (-not $NoMcp) {
    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if ($claude) {
        $listed = & $claude.Source mcp list 2>$null | Select-String -SimpleMatch 'mnemo'
        if ($listed) {
            Ok "MCP server 'mnemo' already registered"
        } else {
            # Quote '--' so PowerShell passes it through to claude literally.
            & $claude.Source mcp add mnemo '--' mnemo mcp 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Ok 'registered MCP server (claude mcp add mnemo -- mnemo mcp)'
            } else {
                Warn 'could not auto-register the MCP server; run it manually:'
                Warn '  claude mcp add mnemo -- mnemo mcp'
            }
        }
    } else {
        Warn "'claude' not on PATH; register the MCP server manually:"
        Warn '  claude mcp add mnemo -- mnemo mcp'
        Warn '  (other hosts: see docs/integrations/)'
    }
}

# --- 4b. Wire the Claude Code status line (non-clobbering) ----------------
#
# A one-line presence cue in CC's status bar. mnemo can't OWN a status line
# (it's a user-settings feature), so we add 'mnemo statusline' to the user's
# settings.json only when none exists -- never clobbering a custom one.

if (-not $NoStatusline) {
    & $shim statusline-setup
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
Write-Host 'Done. To finish wiring mnemo into Claude Code:'
Write-Host ''
Write-Host '  1. In Claude Code, run these two commands, then RESTART Claude Code'
Write-Host '     (the /mnemo-* commands appear immediately; the hooks load at session start):'
Write-Host '       /plugin marketplace add mmct-jsc/mnemo'
Write-Host '       /plugin install mnemo@mnemo'
Write-Host "  2. Run 'mnemo reindex' to ingest your memory (first run downloads MiniLM ~22MB)."
Write-Host "  3. Run 'mnemo doctor' to verify every link is green."
Write-Host ''
Write-Host "  - If you saw the PATH warning above, add $BinDir to PATH and open a new shell."
Write-Host "  - 'mnemo daemon start' enables the web UI at http://127.0.0.1:7373/ (optional)."
