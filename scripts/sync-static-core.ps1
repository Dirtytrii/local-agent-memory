<#
.SYNOPSIS
    One-way managed-block renderer & synchronizer for local-agent-memory core-profile.md.
.DESCRIPTION
    - Default mode is DRY-RUN (pass -Apply to write files).
    - Enforces UTF-8 without BOM and LF line endings on all written files.
    - Protects YAML frontmatter lines 1-3 (`trigger: always_on`) for Antigravity-style rules.
    - Creates timestamped .bak backup on first injection into an existing file.
    - Verifies SHA-256 of existing managed blocks; aborts with diff on drift unless -Force is passed.
    - Supports per-target conditional blocks (`<!-- target: ... -->`) and `core-lite` extraction.
    - Refuses to modify USER.md or MEMORY.md (preserves native agent scratchpads).
#>
[CmdletBinding()]
param(
    [string]$Target = 'all',
    [switch]$Apply,
    [switch]$Force,
    [string]$CorePath = '',
    [string]$ManifestPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Normalize-Lf {
    param([string]$Text)
    if ($null -eq $Text) { return '' }
    return ($Text -replace "`r`n", "`n") -replace "`r", "`n"
}

function Get-Sha256Hex {
    param([string]$Text)
    $bytes = $Utf8NoBom.GetBytes($Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash($bytes)
        return ([System.BitConverter]::ToString($hash) -replace '-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-SourceGitSha {
    param([string]$FilePath)
    $dir = Split-Path -Parent $FilePath
    try {
        $sha = (& git -C $dir rev-parse --short HEAD 2>$null)
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sha)) {
            return 'uncommitted'
        }
        $dirty = (& git -C $dir status --porcelain -- $FilePath 2>$null)
        if (-not [string]::IsNullOrWhiteSpace(($dirty -join ''))) {
            return "${sha}-dirty"
        }
        return $sha.Trim()
    }
    catch {
        return 'uncommitted'
    }
}

function Render-CoreContent {
    param(
        [string]$RawCore,
        [string]$RenderTarget,
        [ValidateSet('full', 'lite')]
        [string]$Mode
    )

    $text = Normalize-Lf $RawCore

    if ($Mode -eq 'lite') {
        $liteMatch = [regex]::Match(
            $text,
            '<!-- core-lite:begin -->\n(.*?)<!-- core-lite:end -->',
            [System.Text.RegularExpressions.RegexOptions]::Singleline
        )
        if (-not $liteMatch.Success) {
            throw 'core-profile.md is missing <!-- core-lite:begin --> / <!-- core-lite:end --> markers.'
        }
        $text = $liteMatch.Groups[1].Value
    }
    else {
        $text = [regex]::Replace($text, '<!-- core-lite:(?:begin|end) -->\n?', '')
    }

    $evaluator = [System.Text.RegularExpressions.MatchEvaluator]{
        param($m)
        $targets = $m.Groups[1].Value.Split(',') | ForEach-Object { $_.Trim() }
        if (($targets -contains $RenderTarget) -or ($targets -contains 'all')) {
            return $m.Groups[2].Value
        }
        return ''
    }

    $rendered = [regex]::Replace(
        $text,
        '<!-- target: ([^>]*?) -->\n(.*?)<!-- /target -->\n?',
        $evaluator,
        [System.Text.RegularExpressions.RegexOptions]::Singleline
    )

    $rendered = [regex]::Replace($rendered, "`n{3,}", "`n`n")
    return $rendered.Trim()
}

function Show-LineDiff {
    param(
        [string]$Expected,
        [string]$Actual
    )
    $expLines = (Normalize-Lf $Expected).Split("`n")
    $actLines = (Normalize-Lf $Actual).Split("`n")
    $max = [Math]::Max($expLines.Length, $actLines.Length)
    for ($i = 0; $i -lt $max; $i++) {
        $e = if ($i -lt $expLines.Length) { $expLines[$i] } else { $null }
        $a = if ($i -lt $actLines.Length) { $actLines[$i] } else { $null }
        if ($e -ne $a) {
            if ($null -ne $a) { Write-Host "  - [local]    L$($i+1): $a" -ForegroundColor Yellow }
            if ($null -ne $e) { Write-Host "  + [rendered] L$($i+1): $e" -ForegroundColor Cyan }
        }
    }
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($CorePath)) {
    $CorePath = Join-Path (Split-Path -Parent $ScriptDir) 'core\core-profile.md'
}
$CorePath = (Resolve-Path $CorePath).Path

$RawCore = [System.IO.File]::ReadAllText($CorePath, $Utf8NoBom)
$SrcSha = Get-SourceGitSha -FilePath $CorePath

$DefaultTargets = @(
    [pscustomobject]@{
        name         = 'antigravity'
        group        = 'antigravity'
        renderTarget = 'antigravity'
        mode         = 'full'
        strategy     = 'antigravity_frontmatter_replace'
        path         = Join-Path $env:USERPROFILE '.gemini\config\plugins\personal-memory\rules\core-profile.md'
    },
    [pscustomobject]@{
        name         = 'codex'
        group        = 'codex'
        renderTarget = 'codex'
        mode         = 'full'
        strategy     = 'preserve_outside_append'
        path         = Join-Path $env:USERPROFILE '.codex\AGENTS.md'
    }
)

$LocalManifest = Join-Path $ScriptDir 'sync-targets.local.json'
if (-not [string]::IsNullOrWhiteSpace($ManifestPath)) {
    $TargetList = Get-Content -LiteralPath $ManifestPath -Encoding utf8 -Raw | ConvertFrom-Json
}
elseif (Test-Path -LiteralPath $LocalManifest) {
    $TargetList = Get-Content -LiteralPath $LocalManifest -Encoding utf8 -Raw | ConvertFrom-Json
}
else {
    $TargetList = $DefaultTargets
}

$TargetList = @($TargetList)

$Selected = $TargetList | Where-Object {
    if ($Target -eq 'all') { return $true }
    return ($_.group -eq $Target -or $_.renderTarget -eq $Target -or $_.name -eq $Target)
}

$ModeBanner = if ($Apply) { 'APPLY' } else { 'DRY-RUN (pass -Apply to write)' }
Write-Host "=== local-agent-memory Static Core Sync [${ModeBanner}] ==="
Write-Host "Source: ${CorePath} (git=${SrcSha})"

if (@($Selected).Count -eq 0) {
    Write-Host "[WARN] No targets matched filter '${Target}'. Copy scripts/sync-targets.example.json to scripts/sync-targets.local.json to register custom targets."
    exit 0
}

$DriftCount = 0
$ErrorCount = 0
$BlockPattern = '<!-- AGENTMEMORY:CORE:BEGIN\s+src=([^\s]+)\s+target=([^\s]+)\s+sha256=([0-9a-fA-F]+)\s*-->\n(.*?)\n<!-- AGENTMEMORY:CORE:END -->'

foreach ($t in $Selected) {
    $tName = $t.name
    $tPath = [Environment]::ExpandEnvironmentVariables($t.path)
    $tRender = $t.renderTarget
    $tMode = $t.mode
    $tStrategy = $t.strategy

    $leaf = Split-Path -Leaf $tPath
    if ($leaf -in @('USER.md', 'MEMORY.md')) {
        Write-Host "[BLOCKED] ${tName}: Refusing to modify ${tPath} (native agent scratchpads must not be overwritten)." -ForegroundColor Red
        $ErrorCount++
        continue
    }

    $renderedBody = Render-CoreContent -RawCore $RawCore -RenderTarget $tRender -Mode $tMode
    $newHash = Get-Sha256Hex -Text $renderedBody
    $newHashShort = $newHash.Substring(0, 16)
    $renderedBytes = $Utf8NoBom.GetByteCount($renderedBody)
    $managedBlock = "<!-- AGENTMEMORY:CORE:BEGIN src=core-profile.md@${SrcSha} target=${tRender} sha256=${newHashShort} -->`n${renderedBody}`n<!-- AGENTMEMORY:CORE:END -->"

    $exists = Test-Path -LiteralPath $tPath
    if (-not $exists) {
        if ($tStrategy -eq 'preserve_outside_append' -and $tMode -eq 'lite') {
            Write-Host "[SKIP] ${tName}: target not found (${tPath})"
            continue
        }
        $existingText = ''
    }
    else {
        $existingText = Normalize-Lf ([System.IO.File]::ReadAllText($tPath, $Utf8NoBom))
    }

    $hasBlock = $false
    $m = [regex]::Match($existingText, $BlockPattern, [System.Text.RegularExpressions.RegexOptions]::Singleline)
    if ($m.Success) {
        $hasBlock = $true
        $recordedHash = $m.Groups[3].Value.ToLowerInvariant()
        $currentInner = $m.Groups[4].Value
        $actualFullHash = Get-Sha256Hex -Text $currentInner
        $actualHash = if ($recordedHash.Length -eq 16) { $actualFullHash.Substring(0, 16) } else { $actualFullHash }

        if ($actualHash -ne $recordedHash) {
            Write-Host "[DRIFT] ${tName} (${tPath}): recorded=${recordedHash}, actual=${actualHash}" -ForegroundColor Red
            Show-LineDiff -Expected $renderedBody -Actual $currentInner
            if (-not $Force) {
                Write-Host "  -> Aborted write for ${tName}. Re-run with -Force to overwrite local drift." -ForegroundColor Red
                $DriftCount++
                continue
            }
            Write-Host "  -> -Force specified; proceeding to overwrite drifted managed block." -ForegroundColor Yellow
        }
        elseif ($actualHash -eq $newHashShort -and $m.Groups[2].Value -eq $tRender) {
            Write-Host "[UNCHANGED] ${tName} (${tPath}) | target=${tRender} mode=${tMode} bytes=${renderedBytes} sha256=${newHashShort}"
            continue
        }

        $finalText = $existingText.Substring(0, $m.Index) + $managedBlock + $existingText.Substring($m.Index + $m.Length)
    }
    else {
        if ($tStrategy -eq 'antigravity_frontmatter_replace') {
            $fm = "---`ntrigger: always_on`n---"
            if ($existingText -match '^---\n[\s\S]*?\n---') {
                $fm = $Matches[0]
            }
            $finalText = "${fm}`n`n${managedBlock}`n"
        }
        else {
            if ([string]::IsNullOrWhiteSpace($existingText)) {
                $finalText = "${managedBlock}`n"
            }
            else {
                $trimmed = $existingText.TrimEnd("`n")
                $finalText = "${trimmed}`n`n${managedBlock}`n"
            }
        }
    }

    $finalText = (Normalize-Lf $finalText).TrimEnd("`n") + "`n"

    if ($tStrategy -eq 'antigravity_frontmatter_replace') {
        $topLines = $finalText.Split("`n")
        if ($topLines.Length -lt 3 -or $topLines[0] -ne '---' -or $topLines[1] -ne 'trigger: always_on' -or $topLines[2] -ne '---') {
            Write-Host "[ERROR] ${tName}: Antigravity lines 1-3 frontmatter guard failed!" -ForegroundColor Red
            $ErrorCount++
            continue
        }
    }

    $actionTag = if ($hasBlock) { 'UPDATE' } else { 'INJECT (first-run)' }
    Write-Host "[${actionTag}] ${tName} -> ${tPath} | target=${tRender} mode=${tMode} bytes=${renderedBytes} sha256=${newHashShort}"

    if ($Apply) {
        $parentDir = Split-Path -Parent $tPath
        if (-not (Test-Path -LiteralPath $parentDir)) {
            New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
        }
        if ($exists -and -not $hasBlock) {
            $ts = Get-Date -Format 'yyyyMMdd-HHmmss'
            $bakPath = "${tPath}.${ts}.bak"
            Copy-Item -LiteralPath $tPath -Destination $bakPath -Force
            Write-Host "  -> Backup created: ${bakPath}"
        }
        [System.IO.File]::WriteAllText($tPath, $finalText, $Utf8NoBom)
        Write-Host "  -> Written UTF-8 (no BOM) + LF."
    }
    else {
        if ($exists -and -not $hasBlock) {
            Write-Host "  -> [dry-run] would create timestamped .bak backup before first-run injection"
        }
        Write-Host "  -> [dry-run] final file length: $($Utf8NoBom.GetByteCount($finalText)) bytes (UTF-8 no BOM, LF)"
    }
}

if ($ErrorCount -gt 0) { exit 1 }
if ($DriftCount -gt 0) { exit 2 }
exit 0
