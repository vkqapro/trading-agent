$ErrorActionPreference = 'Stop'
$Repository = (Resolve-Path "$PSScriptRoot\..").Path
$Python = Join-Path $Repository '.venv312\Scripts\python.exe'
Set-Location -LiteralPath $Repository

$runtime = Join-Path $Repository 'memory\runtime'
$lockPath = Join-Path $runtime 'market_data_collector.lock'
if (-not (Test-Path -LiteralPath $lockPath)) {
    throw 'Market Data lock is unavailable; host-assisted preflight remains blocked.'
}

$lockParts = (Get-Content -LiteralPath $lockPath -Raw).Trim() -split '\|', 2
$targetPid = 0
if ($lockParts.Count -gt 0) { [int]::TryParse($lockParts[0].Trim(), [ref]$targetPid) | Out-Null }
if ($targetPid -le 0) { throw 'Market Data lock owner is invalid; host-assisted preflight remains blocked.' }

$rows = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { [int]$_.ProcessId -gt 0 } | ForEach-Object {
    [ordered]@{
        pid = [int]$_.ProcessId
        parent_pid = if ($_.ParentProcessId) { [int]$_.ParentProcessId } else { $null }
        name = [string]$_.Name
        executable = [string]$_.ExecutablePath
        command_line = [string]$_.CommandLine
        created = if ($_.CreationDate) { $_.CreationDate.ToUniversalTime().ToString('o') } else { $null }
    }
})
$target = $rows | Where-Object { $_.pid -eq $targetPid } | Select-Object -First 1
if ($null -eq $target -or $target.command_line -notmatch '(?i)--job\s+market_data') {
    throw "Market Data PID $targetPid was not independently identified by the direct host query."
}

$evidence = [ordered]@{
    context = 'direct-codex-host'
    complete = $true
    collected = [DateTime]::UtcNow.ToString('o')
    target = $target
    lock_owner = $targetPid
    lock_created_at = if ($lockParts.Count -gt 1) { $lockParts[1].Trim() } else { $null }
    processes = $rows
}

$json = $evidence | ConvertTo-Json -Depth 6 -Compress
$json | & $Python -m tools.trading_work_operator host-preflight
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
