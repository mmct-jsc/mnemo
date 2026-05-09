# mnemo UserPromptSubmit hook (PowerShell). Fails open on any error.
$ErrorActionPreference = 'SilentlyContinue'

$mnemo = Get-Command mnemo -ErrorAction SilentlyContinue
if (-not $mnemo) { exit 0 }

# Read the hook payload from stdin.
$payload = $input | Out-String
if (-not $payload) { exit 0 }

try {
    $obj = $payload | ConvertFrom-Json
    $prompt = $obj.prompt
} catch {
    exit 0
}
if (-not $prompt) { exit 0 }

$json = & mnemo query "$prompt" --json --budget 800 --k 5 2>$null
if ($LASTEXITCODE -ne 0 -or -not $json) { exit 0 }

try {
    $data = $json | ConvertFrom-Json
} catch {
    exit 0
}

if (-not $data.hits) { exit 0 }

Write-Output "## Relevant memory (mnemo)"
Write-Output ""
foreach ($h in $data.hits) {
    $desc = if ($h.description) { ($h.description -replace "`n", ' ') } else { '' }
    Write-Output "- $($h.citation) [$($h.type)] $($h.name): $desc"
    if ($h.body) {
        $snippet = if ($h.body.Length -le 400) { $h.body } else { $h.body.Substring(0, 400).TrimEnd() + '...' }
        foreach ($line in ($snippet -split "`n")) {
            Write-Output "  $line"
        }
    }
}
Write-Output ""
$tags = if ($data.intent_tags) { ($data.intent_tags -join ', ') } else { 'none' }
Write-Output "intent: $tags | tokens used: $($data.tokens_used) | k: $($data.hits.Count)"
