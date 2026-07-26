[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [string]$PythonExecutable = "",
    [int]$ClientId = 71
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = $PSScriptRoot
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path

if (-not $PythonExecutable) {
    $repositoryVenv = Join-Path (Split-Path $ProjectRoot -Parent) ".venv\Scripts\python.exe"
    $localVenv = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $repositoryVenv) {
        $PythonExecutable = $repositoryVenv
    } elseif (Test-Path -LiteralPath $localVenv) {
        $PythonExecutable = $localVenv
    } else {
        $PythonExecutable = (Get-Command python.exe -ErrorAction Stop).Source
    }
}

$weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

function New-IrsAction {
    param([Parameter(Mandatory = $true)][string]$Mode)
    New-ScheduledTaskAction `
        -Execute $PythonExecutable `
        -Argument "-m src.main --job irs_scan --irs-mode $Mode --client-id $ClientId" `
        -WorkingDirectory $ProjectRoot
}

function New-WeekdayTriggers {
    param([Parameter(Mandatory = $true)][string[]]$Times)
    @(
        foreach ($time in $Times) {
            New-ScheduledTaskTrigger `
                -Weekly `
                -WeeksInterval 1 `
                -DaysOfWeek $weekdays `
                -At $time
        }
    )
}

$definitions = @(
    @{
        Name = "IBKR Bot - IRS Premarket"
        Description = "IRS full-universe premarket context scan."
        Mode = "PREMARKET_CONTEXT"
        Times = @("08:00")
    },
    @{
        Name = "IBKR Bot - IRS Hourly"
        Description = "IRS full-universe setup scan after each completed hourly bar."
        Mode = "HOURLY_SETUP_SCAN"
        Times = @("10:33", "11:33", "12:33", "13:33", "14:33", "15:33")
    },
    @{
        Name = "IBKR Bot - IRS Confirmation"
        Description = "IRS active-setup confirmation scan after each completed 15-minute bar."
        Mode = "FIFTEEN_MIN_CONFIRMATION_SCAN"
        Times = @(
            "09:46", "10:01", "10:16", "10:31",
            "10:46", "11:01", "11:16", "11:31",
            "11:46", "12:01", "12:16", "12:31",
            "12:46", "13:01", "13:16", "13:31",
            "13:46", "14:01", "14:16", "14:31",
            "14:46", "15:01", "15:16", "15:31",
            "15:46"
        )
    },
    @{
        Name = "IBKR Bot - IRS EOD"
        Description = "IRS end-of-day expiration and summary workflow."
        Mode = "EOD_REPORT"
        Times = @("16:05")
    }
)

foreach ($definition in $definitions) {
    $task = New-ScheduledTask `
        -Action (New-IrsAction -Mode $definition.Mode) `
        -Trigger (New-WeekdayTriggers -Times $definition.Times) `
        -Settings $settings `
        -Principal $principal `
        -Description $definition.Description
    Register-ScheduledTask `
        -TaskName $definition.Name `
        -InputObject $task `
        -Force | Out-Null
    Write-Host "Installed: $($definition.Name)"
}

$runtimeDirectory = Join-Path $ProjectRoot "memory\runtime"
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$manifestPath = Join-Path $runtimeDirectory "irs_scheduler_tasks.json"
$manifestTemporary = "$manifestPath.$PID.tmp"
$manifest = @{
    installed_at = (Get-Date).ToString("o")
    client_id = $ClientId
    python_executable = $PythonExecutable
    project_root = $ProjectRoot
    tasks = @(
        foreach ($definition in $definitions) {
            @{
                task_name = $definition.Name
                mode = $definition.Mode
                weekdays = $weekdays
                times = $definition.Times
            }
        }
    )
}
$manifestJson = $manifest | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText(
    $manifestTemporary,
    $manifestJson,
    [System.Text.UTF8Encoding]::new($false)
)
Move-Item -LiteralPath $manifestTemporary -Destination $manifestPath -Force

Write-Host ""
Write-Host "IRS scheduling is ready. Existing non-IRS tasks were not changed."
Get-ScheduledTask -TaskName "IBKR Bot - IRS *" |
    Sort-Object TaskName |
    Select-Object TaskName, State
