param(
    [switch]$ClearRuntime = $true
)

$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runtime = Join-Path $Root "memory\runtime"

function Stop-ById {
    param([int]$ProcessId, [string]$Why)
    if ($ProcessId -le 0 -or $ProcessId -eq $PID) { return }
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
        $name = [string]($proc.Name)
        $cmd = [string]($proc.CommandLine)
        if ($name -match "^ngrok(\.exe)?$" -or $cmd -match "(^|\s)ngrok(\.exe)?\s+http\s+8550(\s|$)") {
            Write-Host "  protected pid=$ProcessId ngrok tunnel ($Why)"
            return
        }
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  stopped pid=$ProcessId $Why"
    } catch {}
}

function Get-OwnedPython {
    param([string[]]$Patterns)
    try {
        return @(Get-CimInstance Win32_Process | Where-Object {
            if ($_.Name -notmatch "^python(\.exe)?$" -or -not $_.CommandLine) { return $false }
            foreach ($pattern in $Patterns) {
                if ($_.CommandLine -like "*$pattern*") { return $true }
            }
            return $false
        })
    } catch { return @() }
}

function Get-OwnedCmd {
    param([string[]]$Patterns)
    try {
        return @(Get-CimInstance Win32_Process | Where-Object {
            if ($_.Name -notmatch "^cmd(\.exe)?$" -or -not $_.CommandLine) { return $false }
            if ($_.CommandLine -notlike "*$Root*") { return $false }
            foreach ($pattern in $Patterns) {
                if ($_.CommandLine -like "*$pattern*") { return $true }
            }
            return $false
        })
    } catch { return @() }
}

function Get-OwnedDashboardPython {
    # Dashboard verification is intentionally port-scoped.  A validation or
    # development dashboard on another local port (for example 8551) is not
    # the 8550 service owned by this stop command and must not fail shutdown.
    try {
        return @(Get-CimInstance Win32_Process | Where-Object {
            if ($_.Name -notmatch "^python(\.exe)?$" -or -not $_.CommandLine) { return $false }
            if ($_.CommandLine -notmatch "dashboard_react\.server:app") { return $false }
            return $_.CommandLine -match "--port(?:=|\s+)8550(?:\s|$)"
        })
    } catch { return @() }
}

function Stop-OwnedPython {
    param([string[]]$Patterns, [string]$Why)
    foreach ($proc in (Get-OwnedPython -Patterns $Patterns)) {
        Stop-ById -ProcessId ([int]$proc.ProcessId) -Why $Why
    }
}

function Signal-AutonomousWorker {
    $stopPath = Join-Path $Runtime "autonomous_stock_worker.stop"
    $workerPattern = @("src.jobs.autonomous_stock_worker")
    $workers = @(Get-OwnedPython -Patterns $workerPattern)
    if ($workers.Count -eq 0) { return }
    New-Item -ItemType File -Path $stopPath -Force | Out-Null
    Write-Host "  signaled autonomous stock worker graceful stop"
    $deadline = (Get-Date).AddSeconds(35)
    do {
        Start-Sleep -Milliseconds 500
        $workers = @(Get-OwnedPython -Patterns $workerPattern)
    } while ($workers.Count -gt 0 -and (Get-Date) -lt $deadline)
    if ($workers.Count -gt 0) {
        Write-Host "  autonomous worker did not exit within graceful window; stopping exact owned process" -ForegroundColor Yellow
        Stop-OwnedPython -Patterns $workerPattern -Why "autonomous stock worker"
    }
}

Write-Host "Stopping Vitaly's Trading Bot dashboard services..."
Write-Host "Shutdown order: autonomous worker, execute worker, market data, crypto, dashboard."

# Graceful worker shutdown stops new candidates/LLM work, persists STOPPED,
# releases its lock, and does not cancel protective brackets.
Signal-AutonomousWorker

Stop-OwnedPython -Patterns @("--job execute_requests") -Why "execute worker"
Stop-OwnedPython -Patterns @("--job market_data") -Why "market-data collector"
Stop-OwnedPython -Patterns @("src.crypto.main --job worker", "-m src.crypto.main --job worker") -Why "crypto worker"

# Stop the dashboard API last. ngrok is intentionally not targeted.
try {
    netstat -ano -p tcp |
        Select-String -Pattern "[:.]8550\s+.*LISTENING\s+(\d+)" |
        ForEach-Object {
            Stop-ById -ProcessId ([int]$_.Matches[0].Groups[1].Value) -Why "dashboard API port 8550"
        }
} catch {}

$cmdPatterns = @(
    "run_react_dashboard.cmd",
    "run_autonomous_stock_worker.cmd",
    "run_execute_worker.cmd",
    "run_market_data_collector.cmd",
    "run_crypto_worker.cmd"
)
foreach ($proc in (Get-OwnedCmd -Patterns $cmdPatterns)) {
    Stop-ById -ProcessId ([int]$proc.ProcessId) -Why "dashboard cmd wrapper"
}

if ($ClearRuntime) {
    Remove-Item -LiteralPath (Join-Path $Runtime "execute_worker.json") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Runtime "market_data_collector.lock") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Root "memory\crypto\runtime\worker.lock") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Runtime "autonomous_stock_worker.stop") -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Milliseconds 500
$remaining = @()
try {
    $remaining += netstat -ano -p tcp |
        Select-String -Pattern "[:.]8550\s+.*LISTENING\s+(\d+)" |
        ForEach-Object { "dashboard API pid=$($_.Matches[0].Groups[1].Value)" }
} catch {}
foreach ($proc in (Get-OwnedPython -Patterns @(
    "--job execute_requests",
    "--job market_data",
    "src.crypto.main --job worker",
    "-m src.crypto.main --job worker",
    "src.jobs.autonomous_stock_worker"
))) {
    $remaining += "python service pid=$($proc.ProcessId)"
}
foreach ($proc in (Get-OwnedDashboardPython)) {
    $remaining += "python dashboard API pid=$($proc.ProcessId) port=8550"
}
foreach ($proc in (Get-OwnedCmd -Patterns $cmdPatterns)) {
    $remaining += "cmd wrapper pid=$($proc.ProcessId)"
}

$remaining = @($remaining | Where-Object { $_ } | Select-Object -Unique)
if ($remaining.Count -gt 0) {
    Write-Host "Dashboard service stop verification FAILED:" -ForegroundColor Red
    $remaining | ForEach-Object { Write-Host "  remaining $_" -ForegroundColor Red }
    exit 1
}

Write-Host "Verified stopped: autonomous stock worker, execute worker, market-data collector, crypto worker, and dashboard API."
Write-Host "ngrok, TWS/IB Gateway, browsers, and unrelated Python processes were not targeted."
exit 0
