[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('status', 'start', 'stop', 'restart')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$StartLauncher = Join-Path $Root 'run_dashboard_api.cmd'
$Port = 8550

function Get-DashboardListener {
    $connections = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($connection in $connections) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)"
        if (-not $process) { continue }
        $command = [string]$process.CommandLine
        $normalizedCommand = $command -replace '"', ''
        if ($normalizedCommand -match 'uvicorn\s+dashboard_react\.server:app' -and $normalizedCommand -match '--host\s+127\.0\.0\.1' -and $normalizedCommand -match '--port\s+8550') {
            return [pscustomobject]@{
                ListenerPid = [int]$connection.OwningProcess
                Process = $process
            }
        }
    }
    return $null
}

function Get-ProcessInfo([int]$ProcessId) {
    if ($ProcessId -le 0) { return $null }
    return Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
}

function Show-Status {
    $listener = Get-DashboardListener
    if (-not $listener) {
        Write-Output 'dashboard_api status=STOPPED listener=none'
        return
    }
    $process = $listener.Process
    Write-Output ("dashboard_api status=RUNNING listener_pid=$($listener.ListenerPid) parent_pid=$($process.ParentProcessId)")
    Write-Output ("executable=$($process.ExecutablePath)")
    Write-Output ("command_line=$($process.CommandLine)")
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8550/api/daily-trader/health' -Method Get -TimeoutSec 3
        Write-Output ("health_status=$($health.status) read_only=$($health.read_only) orders_submitted=$($health.orders_submitted)")
    } catch {
        Write-Output 'health_status=UNAVAILABLE'
    }
}

function Wait-ForStopped {
    for ($i = 0; $i -lt 40; $i++) {
        if (-not (Get-DashboardListener)) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Wait-ForReady {
    for ($i = 0; $i -lt 40; $i++) {
        $listener = Get-DashboardListener
        if ($listener) {
            try {
                $null = Invoke-RestMethod -Uri 'http://127.0.0.1:8550/api/radar/top?limit=1' -Method Get -TimeoutSec 2
                return $listener
            } catch { }
        }
        Start-Sleep -Milliseconds 250
    }
    return $null
}

function Stop-DashboardOnly {
    $listener = Get-DashboardListener
    if (-not $listener) {
        Write-Output 'dashboard_api stop=NOOP reason=no_matching_dashboard_listener'
        return
    }

    $target = $listener.Process
    $parent = Get-ProcessInfo ([int]$target.ParentProcessId)
    Write-Output ("dashboard_api stopping_listener_pid=$($listener.ListenerPid) parent_pid=$($target.ParentProcessId)")
    Stop-Process -Id $listener.ListenerPid -ErrorAction Stop

    if (-not (Wait-ForStopped)) {
        throw 'Dashboard listener did not stop within the bounded timeout; refusing to start a replacement.'
    }

    if ($parent) {
        $parentCommand = [string]$parent.CommandLine
        $normalizedParentCommand = $parentCommand -replace '"', ''
        if ($normalizedParentCommand -match 'uvicorn\s+dashboard_react\.server:app' -and $normalizedParentCommand -match '--port\s+8550') {
            if (Get-ProcessInfo ([int]$parent.ProcessId)) {
                Stop-Process -Id ([int]$parent.ProcessId) -ErrorAction Stop
                Start-Sleep -Milliseconds 250
            }
        } elseif (Get-ProcessInfo ([int]$parent.ProcessId)) {
            throw "Dashboard listener stopped, but its parent is not an independently identifiable Dashboard process: PID $($parent.ProcessId)."
        }
    }
    Write-Output 'dashboard_api stop=OK'
}

switch ($Action) {
    'status' { Show-Status; break }
    'start' {
        if (Get-DashboardListener) { Show-Status; break }
        if (-not (Test-Path -LiteralPath $StartLauncher -PathType Leaf)) { throw "Missing dedicated launcher: $StartLauncher" }
        & $StartLauncher
        if ($LASTEXITCODE -ne 0) { throw "Dedicated Dashboard launcher failed with exit code $LASTEXITCODE." }
        $ready = Wait-ForReady
        if (-not $ready) { throw 'Dedicated Dashboard did not become ready within the bounded timeout.' }
        Show-Status
        break
    }
    'stop' { Stop-DashboardOnly; break }
    'restart' {
        Stop-DashboardOnly
        & $StartLauncher
        if ($LASTEXITCODE -ne 0) { throw "Dedicated Dashboard launcher failed with exit code $LASTEXITCODE." }
        $ready = Wait-ForReady
        if (-not $ready) { throw 'Dedicated Dashboard did not become ready within the bounded timeout.' }
        Show-Status
        break
    }
}
