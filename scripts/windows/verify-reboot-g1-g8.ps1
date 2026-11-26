<#
.SYNOPSIS
    Read-only Windows post-reboot verification for AgentMemory (G1 Loopback/Auto-Start,
    G8 Local Embedding Digest & 1024-dim check, G0 Security Headers & Read-Only MCP Surface).
.DESCRIPTION
    Strictly read-only: makes zero file, registry, or service mutations, and avoids CLI
    commands (such as `ollama ps`) that auto-launch the Ollama GUI app on Windows.
    Explicitly bypasses system HTTP proxy for loopback probes and supports both
    Windows PowerShell 5.1 and PowerShell 7+.
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'AgentMemory-BasicMemory-MCP',
    [string]$OllamaTaskName = 'AgentMemory-Ollama-Serve',
    [string]$McpUrl = 'http://127.0.0.1:8765/mcp',
    [string]$OllamaBaseUrl = 'http://127.0.0.1:11434',
    [string]$EmbeddingModel = 'bge-m3',
    [int]$ExpectedDim = 1024,
    [string]$ModelsDir = '',
    [string]$ExpectedGgufDigest = '',
    [string]$ProjectName = 'agent-memory',
    [string]$ProbeQuery = 'memory'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:PassCount = 0
$script:FailCount = 0
$script:WarnCount = 0

function Write-Check {
    param(
        [ValidateSet('PASS', 'FAIL', 'WARN')]
        [string]$Status,
        [string]$Gate,
        [string]$Message
    )
    switch ($Status) {
        'PASS' {
            $script:PassCount++
            Write-Host ("[PASS] [{0}] {1}" -f $Gate, $Message) -ForegroundColor Green
        }
        'FAIL' {
            $script:FailCount++
            Write-Host ("[FAIL] [{0}] {1}" -f $Gate, $Message) -ForegroundColor Red
        }
        'WARN' {
            $script:WarnCount++
            Write-Host ("[WARN] [{0}] {1}" -f $Gate, $Message) -ForegroundColor Yellow
        }
    }
}

function Has-Prop {
    param(
        [object]$Obj,
        [string]$Name
    )
    if ($null -eq $Obj) { return $false }
    return (@($Obj.PSObject.Properties.Match($Name)).Count -gt 0)
}

function Invoke-LoopbackHttp {
    param(
        [string]$Url,
        [string]$Method = 'GET',
        [hashtable]$Headers = @{},
        [string]$Body = $null,
        [int]$TimeoutMs = 15000
    )
    $req = [System.Net.HttpWebRequest]::Create($Url)
    $req.Method = $Method
    $req.Proxy = [System.Net.GlobalProxySelection]::GetEmptyWebProxy()
    $req.Timeout = $TimeoutMs
    $req.ReadWriteTimeout = $TimeoutMs

    foreach ($k in $Headers.Keys) {
        switch ($k.ToLowerInvariant()) {
            'host'         { $req.Host = $Headers[$k] }
            'accept'       { $req.Accept = $Headers[$k] }
            'content-type' { $req.ContentType = $Headers[$k] }
            default        { $req.Headers.Add($k, $Headers[$k]) }
        }
    }

    if ($Method -ne 'GET' -and $Method -ne 'HEAD' -and -not [string]::IsNullOrEmpty($Body)) {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Body)
        $req.ContentLength = $bytes.Length
        $stream = $req.GetRequestStream()
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Close()
    }

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $resp = [System.Net.HttpWebResponse]$req.GetResponse()
        $reader = New-Object System.IO.StreamReader($resp.GetResponseStream(), [System.Text.Encoding]::UTF8)
        $text = $reader.ReadToEnd()
        $reader.Close()
        $sw.Stop()
        $code = [int]$resp.StatusCode
        $hdrs = @{}
        foreach ($hk in $resp.Headers.AllKeys) {
            $hdrs[$hk.ToLowerInvariant()] = $resp.Headers[$hk]
        }
        $resp.Close()
        return [pscustomobject]@{
            StatusCode = $code
            Headers    = $hdrs
            Body       = $text
            ElapsedMs  = [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
            Error      = $null
        }
    } catch [System.Net.WebException] {
        if ($null -ne $_.Exception.Response) {
            $resp = [System.Net.HttpWebResponse]$_.Exception.Response
            $reader = New-Object System.IO.StreamReader($resp.GetResponseStream(), [System.Text.Encoding]::UTF8)
            $text = $reader.ReadToEnd()
            $reader.Close()
            $sw.Stop()
            $code = [int]$resp.StatusCode
            $resp.Close()
            return [pscustomobject]@{
                StatusCode = $code
                Headers    = @{}
                Body       = $text
                ElapsedMs  = [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
                Error      = $null
            }
        }
        $sw.Stop()
        return [pscustomobject]@{
            StatusCode = -1
            Headers    = @{}
            Body       = ''
            ElapsedMs  = [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
            Error      = $_.Exception.Message
        }
    }
}

function Parse-McpJson {
    param([string]$RawBody)
    if ([string]::IsNullOrWhiteSpace($RawBody)) { return $null }
    foreach ($line in ($RawBody -split "`r?`n")) {
        if ($line.StartsWith('data: ')) {
            try { return ($line.Substring(6) | ConvertFrom-Json) } catch {}
        }
    }
    try { return ($RawBody | ConvertFrom-Json) } catch { return $null }
}

function Get-EffectiveEnv {
    param([string]$Name)
    foreach ($scope in @('Process', 'User', 'Machine')) {
        $val = [Environment]::GetEnvironmentVariable($Name, $scope)
        if (-not [string]::IsNullOrWhiteSpace($val)) { return $val }
    }
    return $null
}

Write-Host '=== AgentMemory Post-Reboot Read-Only Verification (G1 / G8 / G0-Security) ===' -ForegroundColor Cyan

# --- 1. G1: Scheduled Tasks & Loopback Ports ---
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Check FAIL G1 "Scheduled task '$TaskName' not found."
} else {
    $tInfo = Get-ScheduledTaskInfo -TaskName $TaskName
    $okResult = ($tInfo.LastTaskResult -eq 0 -or $tInfo.LastTaskResult -eq 267009)
    if ($task.State -eq 'Running' -and $okResult) {
        Write-Check PASS G1 "Task '$TaskName' Running (LastRun=$($tInfo.LastRunTime), LastResult=$($tInfo.LastTaskResult))."
    } else {
        Write-Check FAIL G1 "Task '$TaskName' State=$($task.State), LastResult=$($tInfo.LastTaskResult)."
    }
}

if (-not [string]::IsNullOrWhiteSpace($OllamaTaskName)) {
    $oTask = Get-ScheduledTask -TaskName $OllamaTaskName -ErrorAction SilentlyContinue
    if ($null -ne $oTask) {
        $oInfo = Get-ScheduledTaskInfo -TaskName $OllamaTaskName
        $oOk = ($oInfo.LastTaskResult -eq 0 -or $oInfo.LastTaskResult -eq 267009)
        if ($oTask.State -eq 'Running' -and $oOk) {
            Write-Check PASS G1 "Task '$OllamaTaskName' Running (LastRun=$($oInfo.LastRunTime), LastResult=$($oInfo.LastTaskResult))."
        } else {
            Write-Check FAIL G1 "Task '$OllamaTaskName' State=$($oTask.State), LastResult=$($oInfo.LastTaskResult)."
        }
    }
}

$p8765 = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
if ($p8765.Count -gt 0) {
    Write-Check PASS G1 "MCP loopback 127.0.0.1:8765 listening (PID=$($p8765[0].OwningProcess))."
} else {
    Write-Check FAIL G1 'MCP loopback 127.0.0.1:8765 is NOT listening.'
}

$p11434 = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue)
if ($p11434.Count -gt 0) {
    Write-Check PASS G1 "Ollama loopback 127.0.0.1:11434 listening (PID=$($p11434[0].OwningProcess))."
} else {
    Write-Check FAIL G1 'Ollama loopback 127.0.0.1:11434 is NOT listening.'
}

# Environment checks
$noProxy = Get-EffectiveEnv 'NO_PROXY'
if ($noProxy -and ($noProxy -match '127\.0\.0\.1') -and ($noProxy -match 'localhost')) {
    Write-Check PASS G1 "NO_PROXY bypasses loopback ($noProxy)."
} else {
    Write-Check WARN G1 "NO_PROXY missing 127.0.0.1/localhost (current='$noProxy')."
}

$envModels = Get-EffectiveEnv 'OLLAMA_MODELS'
if ($envModels) {
    Write-Check PASS G8 "OLLAMA_MODELS='$envModels'."
} else {
    Write-Check WARN G8 'OLLAMA_MODELS is unset (falling back to default ~/.ollama/models).'
}

$envKeepAlive = Get-EffectiveEnv 'OLLAMA_KEEP_ALIVE'
if ($envKeepAlive) {
    Write-Check PASS G8 "OLLAMA_KEEP_ALIVE='$envKeepAlive'."
} else {
    Write-Check WARN G8 'OLLAMA_KEEP_ALIVE is unset (Ollama defaults to 5m).'
}

# --- 2. G8: Local Model Manifest, Resident State & 1024-dim Embedding ---
$resolvedModelsDir = if (-not [string]::IsNullOrWhiteSpace($ModelsDir)) {
    $ModelsDir
} elseif ($envModels) {
    $envModels
} else {
    Join-Path $env:USERPROFILE '.ollama\models'
}

$modelName = $EmbeddingModel.Split(':')[0]
$modelTag = if ($EmbeddingModel -match ':') { $EmbeddingModel.Split(':')[1] } else { 'latest' }
$manifestPath = Join-Path $resolvedModelsDir "manifests\registry.ollama.ai\library\$modelName\$modelTag"

if (Test-Path -LiteralPath $manifestPath) {
    $mfObj = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $modelLayer = @($mfObj.layers | Where-Object { $_.mediaType -eq 'application/vnd.ollama.image.model' }) | Select-Object -First 1
    if ($null -ne $modelLayer) {
        $digest = [string]$modelLayer.digest
        $blobPath = Join-Path $resolvedModelsDir ("blobs\" + ($digest -replace ':', '-'))
        $blobExists = Test-Path -LiteralPath $blobPath
        if (-not $blobExists) {
            Write-Check FAIL G8 "Model GGUF blob missing on disk: $blobPath"
        } elseif (-not [string]::IsNullOrWhiteSpace($ExpectedGgufDigest)) {
            if ($digest -eq $ExpectedGgufDigest) {
                Write-Check PASS G8 "Manifest GGUF digest matches pinned digest ($digest)."
            } else {
                Write-Check FAIL G8 "Manifest GGUF digest drift: got '$digest', expected '$ExpectedGgufDigest'."
            }
        } else {
            Write-Check PASS G8 "Manifest & GGUF blob verified ($digest, $([math]::Round($modelLayer.size / 1MB, 1)) MiB)."
        }
    } else {
        Write-Check FAIL G8 "No model layer found in manifest: $manifestPath"
    }
} else {
    Write-Check FAIL G8 "Model manifest not found at: $manifestPath"
}

if ($p11434.Count -gt 0) {
    $tagsResp = Invoke-LoopbackHttp -Url "$OllamaBaseUrl/api/tags" -Method GET
    if ($tagsResp.StatusCode -eq 200) {
        $tagsJson = $tagsResp.Body | ConvertFrom-Json
        $modelsArr = if (Has-Prop $tagsJson 'models') { @($tagsJson.models) } else { @() }
        $hasModel = @($modelsArr | Where-Object { $_.name -like "$modelName*" }).Count -gt 0
        if ($hasModel) {
            Write-Check PASS G8 "Ollama /api/tags lists '$EmbeddingModel' ($($tagsResp.ElapsedMs)ms)."
        } else {
            Write-Check FAIL G8 "Ollama /api/tags does NOT list '$EmbeddingModel'."
        }
    } else {
        Write-Check FAIL G8 "Ollama /api/tags failed (HTTP $($tagsResp.StatusCode), $($tagsResp.Error))."
    }

    $psBefore = Invoke-LoopbackHttp -Url "$OllamaBaseUrl/api/ps" -Method GET
    if ($psBefore.StatusCode -eq 200) {
        $psJson = $psBefore.Body | ConvertFrom-Json
        $psArr = if (Has-Prop $psJson 'models') { @($psJson.models) } else { @() }
        $psDesc = @($psArr | ForEach-Object {
            $vramMb = if (Has-Prop $_ 'size_vram') { [math]::Round($_.size_vram / 1MB, 0) } else { 0 }
            $exp = if (Has-Prop $_ 'expires_at') { $_.expires_at } else { 'unknown' }
            "$($_.name) [VRAM=${vramMb}MiB, expires=$exp]"
        })
        $loadedStr = if ($psDesc.Count -gt 0) { $psDesc -join '; ' } else { 'none (cold)' }
        Write-Host "[INFO] [G8] Pre-probe /api/ps: $loadedStr" -ForegroundColor DarkGray
    }

    $embBody = (@{ model = $EmbeddingModel; input = 'AgentMemory G8 1024-dim read-only probe' } | ConvertTo-Json -Compress)
    $embResp = Invoke-LoopbackHttp -Url "$OllamaBaseUrl/v1/embeddings" -Method POST -Headers @{ 'Content-Type' = 'application/json' } -Body $embBody -TimeoutMs 60000
    if ($embResp.StatusCode -eq 200) {
        $embJson = $embResp.Body | ConvertFrom-Json
        $dim = @($embJson.data[0].embedding).Count
        if ($dim -eq $ExpectedDim) {
            Write-Check PASS G8 "POST /v1/embeddings returned $dim-dim vector ($($embResp.ElapsedMs)ms)."
        } else {
            Write-Check FAIL G8 "Embedding dimension mismatch: got $dim, expected $ExpectedDim."
        }
    } else {
        Write-Check FAIL G8 "POST /v1/embeddings failed (HTTP $($embResp.StatusCode), $($embResp.Error))."
    }

    $psAfter = Invoke-LoopbackHttp -Url "$OllamaBaseUrl/api/ps" -Method GET
    if ($psAfter.StatusCode -eq 200) {
        $psJson2 = $psAfter.Body | ConvertFrom-Json
        $psArr2 = if (Has-Prop $psJson2 'models') { @($psJson2.models) } else { @() }
        $psDesc2 = @($psArr2 | ForEach-Object {
            $vramMb = if (Has-Prop $_ 'size_vram') { [math]::Round($_.size_vram / 1MB, 0) } else { 0 }
            $sizeMb = if (Has-Prop $_ 'size') { [math]::Round($_.size / 1MB, 0) } else { 0 }
            $exp = if (Has-Prop $_ 'expires_at') { $_.expires_at } else { 'unknown' }
            "$($_.name) [Total=${sizeMb}MiB, VRAM=${vramMb}MiB, expires=$exp]"
        })
        if ($psDesc2.Count -gt 0) {
            Write-Host "[INFO] [G8] Post-probe /api/ps: $($psDesc2 -join '; ')" -ForegroundColor DarkGray
        }
    }
}

# --- 3. G0/G1: MCP Security Headers, Read-Only Surface, text & hybrid Probes ---
if ($p8765.Count -gt 0) {
    $h421 = Invoke-LoopbackHttp -Url $McpUrl -Method POST -Headers @{
        'Host'         = 'evil.example'
        'Content-Type' = 'application/json'
        'Accept'       = 'application/json, text/event-stream'
    } -Body '{}'
    if ($h421.StatusCode -eq 421) {
        Write-Check PASS G0-Sec "Host header protection active (evil.example -> HTTP 421, $($h421.ElapsedMs)ms)."
    } else {
        Write-Check FAIL G0-Sec "Expected HTTP 421 for Host=evil.example, got HTTP $($h421.StatusCode)."
    }

    $h403 = Invoke-LoopbackHttp -Url $McpUrl -Method POST -Headers @{
        'Origin'       = 'https://evil.example'
        'Content-Type' = 'application/json'
        'Accept'       = 'application/json, text/event-stream'
    } -Body '{}'
    if ($h403.StatusCode -eq 403) {
        Write-Check PASS G0-Sec "Origin header protection active (https://evil.example -> HTTP 403, $($h403.ElapsedMs)ms)."
    } else {
        Write-Check FAIL G0-Sec "Expected HTTP 403 for Origin=https://evil.example, got HTTP $($h403.StatusCode)."
    }

    $initBody = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"g1-g8-check","version":"1.0"}}}'
    $initResp = Invoke-LoopbackHttp -Url $McpUrl -Method POST -Headers @{
        'Content-Type' = 'application/json'
        'Accept'       = 'application/json, text/event-stream'
    } -Body $initBody
    $sid = if ($initResp.Headers.ContainsKey('mcp-session-id')) { $initResp.Headers['mcp-session-id'] } else { $null }

    if ($initResp.StatusCode -eq 200 -and -not [string]::IsNullOrWhiteSpace($sid)) {
        Write-Check PASS G1 "MCP initialize succeeded ($($initResp.ElapsedMs)ms)."
        $mcpHeaders = @{
            'Content-Type'   = 'application/json'
            'Accept'         = 'application/json, text/event-stream'
            'Mcp-Session-Id' = $sid
        }
        $null = Invoke-LoopbackHttp -Url $McpUrl -Method POST -Headers $mcpHeaders -Body '{"jsonrpc":"2.0","method":"notifications/initialized"}'

        $listResp = Invoke-LoopbackHttp -Url $McpUrl -Method POST -Headers $mcpHeaders -Body '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
        $listJson = Parse-McpJson $listResp.Body
        $tools = @()
        if ($listJson -and (Has-Prop $listJson 'result') -and (Has-Prop $listJson.result 'tools')) {
            $tools = @($listJson.result.tools | ForEach-Object { $_.name })
        }
        $forbidden = @('bm_write', 'bm_edit', 'bm_move', 'bm_delete', 'bm_project_delete')
        $leaked = @($tools | Where-Object { $forbidden -contains $_ })
        if ($leaked.Count -eq 0 -and $tools.Count -gt 0) {
            Write-Check PASS G0-RO "Read-only MCP surface intact ($($tools.Count) tools, 0 write tools: $($tools -join ', '))."
        } else {
            Write-Check FAIL G0-RO "Unexpected MCP tool exposure (count=$($tools.Count), leakedWrite=$($leaked -join ','))."
        }

        foreach ($mode in @('text', 'hybrid')) {
            $reqId = if ($mode -eq 'text') { 10 } else { 11 }
            $callBody = (@{
                jsonrpc = '2.0'
                id      = $reqId
                method  = 'tools/call'
                params  = @{
                    name      = 'bm_search'
                    arguments = @{
                        query     = $ProbeQuery
                        project   = $ProjectName
                        mode      = $mode
                        page_size = 3
                    }
                }
            } | ConvertTo-Json -Depth 6 -Compress)
            $callResp = Invoke-LoopbackHttp -Url $McpUrl -Method POST -Headers $mcpHeaders -Body $callBody -TimeoutMs 25000
            $callJson = Parse-McpJson $callResp.Body
            $hasRpcErr = ($null -ne $callJson) -and (Has-Prop $callJson 'error')
            $hasToolErr = ($null -ne $callJson) -and (Has-Prop $callJson 'result') -and (Has-Prop $callJson.result 'isError') -and ($callJson.result.isError -eq $true)
            $okCall = ($callResp.StatusCode -eq 200) -and ($null -ne $callJson) -and (-not $hasRpcErr) -and (-not $hasToolErr)
            if ($okCall) {
                Write-Check PASS G1 "bm_search(mode=$mode) succeeded ($($callResp.ElapsedMs)ms)."
            } else {
                Write-Check FAIL G1 "bm_search(mode=$mode) failed (HTTP $($callResp.StatusCode), $($callResp.ElapsedMs)ms)."
            }
        }
    } else {
        Write-Check FAIL G1 "MCP initialize failed (HTTP $($initResp.StatusCode), $($initResp.Error))."
    }
}

Write-Host ("=== Summary: PASS={0}, WARN={1}, FAIL={2} ===" -f $script:PassCount, $script:WarnCount, $script:FailCount) -ForegroundColor Cyan
if ($script:FailCount -gt 0) { exit 1 } else { exit 0 }
