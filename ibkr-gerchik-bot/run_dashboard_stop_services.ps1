param(
    [switch]$ClearRuntime = $true
)

$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Stop-ById {
    param([int]$ProcessId, [string]$Why)
    if ($ProcessId -le 0 -or $ProcessId -eq $PID) { return }
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  stopped pid=$ProcessId $Why"
    } catch {}
}

Write-Host "Stopping Vitaly's Trading Bot dashboard services..."

# 1) Stop only known dashboard service windows. Do not use broad title patterns
# that can catch unrelated terminals such as ngrok.
$dashboardTitlePattern = "^(Vitaly's Trading Bot|Gerchik).*(React Dashboard|Execute Worker|Market Data Collector|Crypto Worker|OKX 24/7 Collector)"

Get-Process |
    Where-Object {
        $_.MainWindowTitle -and
        ($_.MainWindowTitle -match $dashboardTitlePattern)
    } |
    ForEach-Object { Stop-ById -ProcessId $_.Id -Why "window '$($_.MainWindowTitle)'" }

# 2) Stop the dashboard API listener on localhost:8550. ngrok is not the
# listener on this port; it forwards traffic to it.
try {
    netstat -ano -p tcp |
        Select-String -Pattern "[:.]8550\s+.*LISTENING\s+(\d+)" |
        ForEach-Object {
            $portPid = [int]$_.Matches[0].Groups[1].Value
            Stop-ById -ProcessId $portPid -Why "dashboard API port 8550"
        }
} catch {}

# 3) Stop Python services by their dashboard/bot command line. These patterns do
# not match ngrok, browser, TWS, or unrelated terminals.
$pythonPatterns = @(
    "dashboard_react.server:app",
    "--job execute_requests",
    "--job market_data",
    "src.crypto.main --job worker",
    "-m src.crypto.main --job worker"
)

try {
    Get-CimInstance Win32_Process |
        Where-Object {
            if ($_.Name -notmatch "^python(\.exe)?$" -or -not $_.CommandLine) { return $false }
            foreach ($pattern in $pythonPatterns) {
                if ($_.CommandLine -like "*$pattern*") { return $true }
            }
            return $false
        } |
        ForEach-Object { Stop-ById -ProcessId $_.ProcessId -Why "python dashboard service" }
} catch {
    Write-Host "  process command-line scan unavailable; window-title stop was still attempted"
}

# 4) Stop cmd wrappers that launched these exact dashboard scripts from this repo.
$cmdScriptPatterns = @(
    "run_react_dashboard.cmd",
    "run_execute_worker.cmd",
    "run_market_data_collector.cmd",
    "run_crypto_worker.cmd"
)

try {
    Get-CimInstance Win32_Process |
        Where-Object {
            if ($_.Name -notmatch "^cmd(\.exe)?$" -or -not $_.CommandLine) { return $false }
            if ($_.CommandLine -notlike "*$Root*") { return $false }
            foreach ($pattern in $cmdScriptPatterns) {
                if ($_.CommandLine -like "*$pattern*") { return $true }
            }
            return $false
        } |
        ForEach-Object { Stop-ById -ProcessId $_.ProcessId -Why "dashboard cmd wrapper" }
} catch {}

if ($ClearRuntime) {
    Remove-Item -LiteralPath (Join-Path $Root "memory\runtime\execute_worker.json") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Root "memory\runtime\market_data_collector.lock") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Root "memory\crypto\runtime\worker.lock") -Force -ErrorAction SilentlyContinue
}

Write-Host "Dashboard service stop complete. ngrok was not targeted."
