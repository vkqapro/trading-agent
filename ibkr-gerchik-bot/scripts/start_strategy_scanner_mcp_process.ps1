param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,
    [Parameter(Mandatory = $true)]
    [string]$EngineDir,
    [Parameter(Mandatory = $true)]
    [string]$PidFile,
    [Parameter(Mandatory = $true)]
    [string]$StdoutLog,
    [Parameter(Mandatory = $true)]
    [string]$StderrLog
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    Write-Error "Python executable not found: $PythonExe"
    exit 1
}
if (-not (Test-Path -LiteralPath $EngineDir -PathType Container)) {
    Write-Error "Engine directory not found: $EngineDir"
    exit 1
}

$command = @(
    'set "PYTHONUNBUFFERED=1"',
    'set "STRATEGY_SCANNER_MCP_HOST=127.0.0.1"',
    'set "STRATEGY_SCANNER_MCP_PORT=8766"',
    "set `"STRATEGY_SCANNER_MCP_PID_FILE=$PidFile`"",
    "cd /d `"$EngineDir`"",
    "`"$PythonExe`" -m scanner_mcp.server 1>>`"$StdoutLog`" 2>>`"$StderrLog`""
) -join ' && '

$cmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
if (-not (Test-Path -LiteralPath $cmdExe -PathType Leaf)) {
    $cmdExe = 'C:\Windows\System32\cmd.exe'
}

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $cmdExe
$psi.Arguments = "/d /s /c ""$command"""
$psi.WorkingDirectory = $EngineDir
$psi.UseShellExecute = $true
$psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

[void][System.Diagnostics.Process]::Start($psi)
