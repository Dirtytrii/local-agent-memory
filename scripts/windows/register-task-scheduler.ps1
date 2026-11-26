<#
.SYNOPSIS
    Register and start the local-agent-memory read-only Streamable HTTP MCP server in Windows Task Scheduler.
.DESCRIPTION
    Runs under the current interactive user account (never SYSTEM), using pythonw.exe for zero
    console window flash, with 1-minute auto-restart on failure and single-instance SQLite lock safety.
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'AgentMemory-BasicMemory-MCP',
    [string]$PythonwPath = (Join-Path $env:APPDATA 'uv\tools\basic-memory\Scripts\pythonw.exe'),
    [string]$LauncherPath = (Join-Path $env:USERPROFILE '.basic-memory\run_mcp_server.py'),
    [string]$WorkDir = (Join-Path $env:USERPROFILE '.basic-memory')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$UserId = "${env:USERDOMAIN}\${env:USERNAME}"

if (-not (Test-Path -LiteralPath $PythonwPath)) {
    throw "Missing pythonw.exe at: $PythonwPath (install via 'uv tool install basic-memory==1.0.0' or pass -PythonwPath)"
}
if (-not (Test-Path -LiteralPath $LauncherPath)) {
    throw "Missing launcher at: $LauncherPath (copy scripts/windows/run_mcp_server.py to ~/.basic-memory/ first)"
}

$action = New-ScheduledTaskAction -Execute $PythonwPath -Argument "`"$LauncherPath`"" -WorkingDirectory $WorkDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
$principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'local-agent-memory read-only Basic Memory Streamable HTTP MCP server (127.0.0.1:8765)' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3

$info = Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
$rt = Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object LastRunTime, LastTaskResult
Write-Host "Task Registered & Started: $($info.TaskName) | State=$($info.State) | LastRun=$($rt.LastRunTime) | LastResult=$($rt.LastTaskResult)"
