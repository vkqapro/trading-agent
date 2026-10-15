param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,
    [Parameter(Mandatory = $true)]
    [string]$WorkingDirectory,
    [Parameter(Mandatory = $true)]
    [string[]]$Arguments,
    [Parameter(Mandatory = $true)]
    [string]$PidFile,
    [Parameter(Mandatory = $true)]
    [string]$StdoutLog,
    [Parameter(Mandatory = $true)]
    [string]$StderrLog,
    [string[]]$Environment = @()
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    Write-Error "Python executable not found: $PythonExe"
    exit 1
}
if (-not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
    Write-Error "Working directory not found: $WorkingDirectory"
    exit 1
}

$stdoutParent = Split-Path -Parent $StdoutLog
$stderrParent = Split-Path -Parent $StderrLog
if ($stdoutParent) {
    New-Item -ItemType Directory -Force -Path $stdoutParent | Out-Null
}
if ($stderrParent) {
    New-Item -ItemType Directory -Force -Path $stderrParent | Out-Null
}

$cmdLine = @(
    'set "PYTHONUNBUFFERED=1"',
    "cd /d `"$WorkingDirectory`""
)
foreach ($entry in $Environment) {
    $cmdLine += "set `"$entry`""
}
$escapedArgs = $Arguments | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }
$cmdLine += "`"$PythonExe`" $($escapedArgs -join ' ') 1>>`"$StdoutLog`" 2>>`"$StderrLog`""
$command = $cmdLine -join ' && '

$cmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
if (-not (Test-Path -LiteralPath $cmdExe -PathType Leaf)) {
    $cmdExe = 'C:\Windows\System32\cmd.exe'
}

$launcher = [System.Diagnostics.ProcessStartInfo]::new()
$launcher.FileName = $cmdExe
$launcher.Arguments = "/d /s /c ""$command"""
$launcher.WorkingDirectory = $WorkingDirectory
$launcher.UseShellExecute = $true
$launcher.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

$process = [System.Diagnostics.Process]::Start($launcher)
if ($null -eq $process) {
    Write-Error "Failed to start detached service process."
    exit 1
}

Set-Content -LiteralPath $PidFile -Value ([string]$process.Id) -Encoding ASCII
