[CmdletBinding()]
param(
    [switch]$SetTimeZone
)

$ErrorActionPreference = "Stop"

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script from an elevated PowerShell session. Administrator privileges are required to enforce the Windows timezone and register highest-privilege tasks."
    }
}

function Resolve-ProjectPaths {
    $scriptPath = $PSCommandPath
    if (-not $scriptPath) {
        $scriptPath = $MyInvocation.MyCommand.Path
    }
    $scriptsDir = Split-Path -Parent $scriptPath
    $projectRoot = (Resolve-Path -LiteralPath (Join-Path $scriptsDir "..")).Path
    $tradingAgentRoot = Split-Path -Parent $projectRoot

    $parentPython = Join-Path $tradingAgentRoot ".venv\Scripts\python.exe"
    $localPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $parentPython) {
        $pythonExecutable = (Resolve-Path -LiteralPath $parentPython).Path
    } elseif (Test-Path -LiteralPath $localPython) {
        $pythonExecutable = (Resolve-Path -LiteralPath $localPython).Path
    } else {
        throw "No project virtual-environment Python found at '$parentPython' or '$localPython'. Create the virtual environment before installing scheduled tasks."
    }

    [pscustomobject]@{
        ProjectRoot = $projectRoot
        TradingAgentRoot = $tradingAgentRoot
        PythonExecutable = $pythonExecutable
    }
}

function Assert-TimeZone {
    param([switch]$AllowSet)

    $timeZone = Get-TimeZone
    if ($timeZone.Id -eq "Eastern Standard Time") {
        return
    }

    if (-not $AllowSet) {
        throw "Windows timezone is '$($timeZone.Id)'. It must be 'Eastern Standard Time'. Re-run with -SetTimeZone from elevated PowerShell to set it during installation."
    }

    Set-TimeZone -Id "Eastern Standard Time"
    $updated = Get-TimeZone
    if ($updated.Id -ne "Eastern Standard Time") {
        throw "Failed to set Windows timezone to Eastern Standard Time. Current timezone is '$($updated.Id)'."
    }
}

function Assert-EnvironmentSafety {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $envPath = Join-Path $ProjectRoot ".env"
    if (-not (Test-Path -LiteralPath $envPath)) {
        throw ".env not found at '$envPath'. Create it before installing scheduled tasks."
    }

    $values = @{}
    Get-Content -LiteralPath $envPath | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]*)=(.*)$') {
            $values[$matches[1].Trim()] = $matches[2].Trim()
        }
    }

    if (($values["PAPER_TRADING"] -as [string]).ToLowerInvariant() -ne "true") {
        throw "PAPER_TRADING must remain true before installing scheduled tasks."
    }
    if (($values["DRY_RUN_MODE"] -as [string]).ToLowerInvariant() -ne "true") {
        throw "DRY_RUN_MODE must remain true during installation."
    }
}

function Assert-RequiredFiles {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$PythonExecutable
    )

    $requiredCmds = @(
        "run_premarket_task.cmd",
        "run_open_task.cmd",
        "run_intraday_task.cmd",
        "run_slack_task.cmd"
    )
    foreach ($fileName in $requiredCmds) {
        $path = Join-Path $ProjectRoot $fileName
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Required command file not found: $path"
        }
    }

    $importCheck = "import src.main, ib_insync, pandas, dotenv"
    Push-Location -LiteralPath $ProjectRoot
    try {
        & $PythonExecutable -c $importCheck
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($exitCode -ne 0) {
        throw "Python import validation failed for src.main, ib_insync, pandas, and dotenv."
    }
}

function New-WeekdayTrigger {
    param([Parameter(Mandatory = $true)][string]$At)
    $atParts = $At.Split(":")
    $atTime = [datetime]::Today.AddHours([int]$atParts[0]).AddMinutes([int]$atParts[1])
    New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $atTime
}

function New-FridayTrigger {
    param([Parameter(Mandatory = $true)][string]$At)
    $atParts = $At.Split(":")
    $atTime = [datetime]::Today.AddHours([int]$atParts[0]).AddMinutes([int]$atParts[1])
    New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Friday -At $atTime
}

function New-RepeatingWeekdayTrigger {
    param(
        [Parameter(Mandatory = $true)][string]$At,
        [Parameter(Mandatory = $true)][TimeSpan]$Interval,
        [Parameter(Mandatory = $true)][TimeSpan]$Duration
    )
    $trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $At
    $trigger.Repetition.Interval = "PT$([int]$Interval.TotalMinutes)M"
    $trigger.Repetition.Duration = "PT$([int]$Duration.TotalHours)H"
    $trigger
}

function ConvertTo-TaskXmlText {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    [System.Security.SecurityElement]::Escape($Value)
}

function New-SlackTaskXml {
    param(
        [Parameter(Mandatory = $true)][string]$TaskName,
        [Parameter(Mandatory = $true)][string]$Execute,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$UserId
    )

    $taskNameText = ConvertTo-TaskXmlText $TaskName
    $executeText = ConvertTo-TaskXmlText $Execute
    $workingDirectoryText = ConvertTo-TaskXmlText $WorkingDirectory
    $userIdText = ConvertTo-TaskXmlText $UserId
    $date = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")

@"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>$date</Date>
    <Author>$userIdText</Author>
    <Description>Disabled Slack polling task. Remains disabled until assigned a non-conflicting IBKR client ID.</Description>
    <URI>\$taskNameText</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT2M</Interval>
        <Duration>PT11H</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2026-01-05T07:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>
          <Monday />
          <Tuesday />
          <Wednesday />
          <Thursday />
          <Friday />
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$userIdText</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>false</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <DisallowStartOnRemoteAppSession>false</DisallowStartOnRemoteAppSession>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT72H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$executeText</Command>
      <WorkingDirectory>$workingDirectoryText</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}

function New-TaskSettings {
    param(
        [Parameter(Mandatory = $true)][TimeSpan]$ExecutionTimeLimit,
        [switch]$StartWhenAvailable
    )

    $settingsParameters = @{
        MultipleInstances = "IgnoreNew"
        ExecutionTimeLimit = $ExecutionTimeLimit
        AllowStartIfOnBatteries = $true
        DontStopIfGoingOnBatteries = $true
    }
    if ($StartWhenAvailable) {
        $settingsParameters.StartWhenAvailable = $true
    }
    New-ScheduledTaskSettingsSet @settingsParameters
}

function New-TaskPrincipal {
    param([Parameter(Mandatory = $true)][ValidateSet("Highest", "Limited")][string]$RunLevel)
    New-ScheduledTaskPrincipal `
        -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
        -LogonType Interactive `
        -RunLevel $RunLevel
}

function Register-ManagedTask {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)]$Action,
        [Parameter(Mandatory = $true)]$Trigger,
        [Parameter(Mandatory = $true)]$Settings,
        [Parameter(Mandatory = $true)]$Principal,
        [Parameter(Mandatory = $true)][string]$Description,
        [switch]$DisableAfterRegistration
    )

    if ($Name -notlike "IBKR Bot - *") {
        throw "Refusing to manage task outside the IBKR Bot namespace: $Name"
    }

    $existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($existing) {
        $runtimeDir = Join-Path $script:ProjectRoot "memory\runtime"
        New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
        $safeName = ($Name -replace '[^A-Za-z0-9_.-]', '_')
        $exportPath = Join-Path $runtimeDir "$safeName.scheduled-task.xml"
        Export-ScheduledTask -TaskName $Name | Set-Content -LiteralPath $exportPath -Encoding UTF8
        Write-Host "Recorded existing task configuration: $exportPath"
    }

    if ($Action -is [string] -and $Action.TrimStart().StartsWith("<?xml")) {
        Register-ScheduledTask -TaskName $Name -Xml $Action -Force | Out-Null
        if ($DisableAfterRegistration) {
            Disable-ScheduledTask -TaskName $Name | Out-Null
        }
        return
    }

    $task = New-ScheduledTask `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Description $Description

    Register-ScheduledTask -TaskName $Name -InputObject $task -Force | Out-Null

    if ($DisableAfterRegistration) {
        Disable-ScheduledTask -TaskName $Name | Out-Null
    }
}

function Get-TriggerSummary {
    param($Task)
    @(
        foreach ($trigger in $Task.Triggers) {
            [pscustomobject]@{
                StartBoundary = $trigger.StartBoundary
                DaysOfWeek = $trigger.DaysOfWeek
                RepetitionInterval = $trigger.Repetition.Interval
                RepetitionDuration = $trigger.Repetition.Duration
            }
        }
    )
}

Assert-Administrator
Assert-TimeZone -AllowSet:$SetTimeZone

$paths = Resolve-ProjectPaths
$script:ProjectRoot = $paths.ProjectRoot
$projectRoot = $paths.ProjectRoot
$pythonExecutable = $paths.PythonExecutable

Assert-EnvironmentSafety -ProjectRoot $projectRoot
Assert-RequiredFiles -ProjectRoot $projectRoot -PythonExecutable $pythonExecutable

$weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
$highestPrincipal = New-TaskPrincipal -RunLevel Highest
$limitedPrincipal = New-TaskPrincipal -RunLevel Limited
$limit72Hours = New-TimeSpan -Hours 72
$limit3Hours = New-TimeSpan -Hours 3
$limit20Minutes = New-TimeSpan -Minutes 20

$slackAction = New-ScheduledTaskAction -Execute (Join-Path $projectRoot "run_slack_task.cmd") -WorkingDirectory $projectRoot
$slackTaskXml = New-SlackTaskXml `
    -TaskName "IBKR Bot - Slack" `
    -Execute (Join-Path $projectRoot "run_slack_task.cmd") `
    -WorkingDirectory $projectRoot `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$cmdAction = {
    param([string]$FileName)
    New-ScheduledTaskAction -Execute (Join-Path $projectRoot $FileName) -WorkingDirectory $projectRoot
}
$pythonAction = {
    param([string]$Arguments)
    New-ScheduledTaskAction -Execute $pythonExecutable -Argument $Arguments -WorkingDirectory $projectRoot
}

$tasks = @(
    @{
        Name = "IBKR Bot - Slack"
        Action = $slackTaskXml
        Trigger = New-WeekdayTrigger -At "07:00"
        Settings = New-TaskSettings -ExecutionTimeLimit $limit72Hours
        Principal = $highestPrincipal
        Description = "Disabled Slack polling task. Remains disabled until assigned a non-conflicting IBKR client ID."
        Disable = $true
    },
    @{
        Name = "IBKR Bot - IRS Premarket"
        Action = & $pythonAction "-m src.main --job irs_scan --irs-mode PREMARKET_CONTEXT --client-id 71"
        Trigger = New-WeekdayTrigger -At "08:00"
        Settings = New-TaskSettings -ExecutionTimeLimit $limit3Hours -StartWhenAvailable
        Principal = $limitedPrincipal
        Description = "IRS premarket context scan for paper trading."
    },
    @{
        Name = "IBKR Bot - Premarket"
        Action = & $cmdAction "run_premarket_task.cmd"
        Trigger = New-WeekdayTrigger -At "08:20"
        Settings = New-TaskSettings -ExecutionTimeLimit $limit72Hours
        Principal = $highestPrincipal
        Description = "Premarket bot job for paper trading."
    },
    @{
        Name = "IBKR Bot - Open"
        Action = & $cmdAction "run_open_task.cmd"
        Trigger = New-WeekdayTrigger -At "09:36"
        Settings = New-TaskSettings -ExecutionTimeLimit $limit72Hours
        Principal = $highestPrincipal
        Description = "Market-open bot job for paper trading."
    },
    @{
        Name = "IBKR Bot - Intraday"
        Action = & $cmdAction "run_intraday_task.cmd"
        Trigger = New-WeekdayTrigger -At "09:45"
        Settings = New-TaskSettings -ExecutionTimeLimit $limit72Hours
        Principal = $highestPrincipal
        Description = "Intraday bot job for paper trading. One trigger only."
    },
    @{
        Name = "IBKR Bot - IRS Confirmation"
        Action = & $pythonAction "-m src.main --job irs_scan --irs-mode FIFTEEN_MIN_CONFIRMATION_SCAN --client-id 71"
        Trigger = @(
            "09:46", "10:01", "10:16", "10:31", "10:46",
            "11:01", "11:16", "11:31", "11:46",
            "12:01", "12:16", "12:31", "12:46",
            "13:01", "13:16", "13:31", "13:46",
            "14:01", "14:16", "14:31", "14:46",
            "15:01", "15:16", "15:31", "15:46"
        ) | ForEach-Object { New-WeekdayTrigger -At $_ }
        Settings = New-TaskSettings -ExecutionTimeLimit $limit3Hours -StartWhenAvailable
        Principal = $limitedPrincipal
        Description = "IRS fifteen-minute confirmation scans for paper trading."
    },
    @{
        Name = "IBKR Bot - IRS Hourly"
        Action = & $pythonAction "-m src.main --job irs_scan --irs-mode HOURLY_SETUP_SCAN --client-id 71"
        Trigger = @("10:33", "11:33", "12:33", "13:33", "14:33", "15:33") | ForEach-Object { New-WeekdayTrigger -At $_ }
        Settings = New-TaskSettings -ExecutionTimeLimit $limit3Hours -StartWhenAvailable
        Principal = $limitedPrincipal
        Description = "IRS hourly setup scans for paper trading."
    },
    @{
        Name = "IBKR Bot - IRS EOD"
        Action = & $pythonAction "-m src.main --job irs_scan --irs-mode EOD_REPORT --client-id 71"
        Trigger = New-WeekdayTrigger -At "16:05"
        Settings = New-TaskSettings -ExecutionTimeLimit $limit3Hours -StartWhenAvailable
        Principal = $limitedPrincipal
        Description = "IRS end-of-day report for paper trading."
    },
    @{
        Name = "IBKR Bot - EOD"
        Action = & $pythonAction "-m src.main --job eod --dry-run"
        Trigger = New-WeekdayTrigger -At "16:10"
        Settings = New-TaskSettings -ExecutionTimeLimit $limit20Minutes -StartWhenAvailable
        Principal = $highestPrincipal
        Description = "End-of-day bot job. Dry run is intentional."
    },
    @{
        Name = "IBKR Bot - Weekly"
        Action = & $pythonAction "-m src.main --job weekly --dry-run"
        Trigger = New-FridayTrigger -At "16:25"
        Settings = New-TaskSettings -ExecutionTimeLimit $limit20Minutes -StartWhenAvailable
        Principal = $highestPrincipal
        Description = "Weekly bot job. Dry run is intentional."
    }
)

foreach ($definition in $tasks) {
    Register-ManagedTask `
        -Name $definition.Name `
        -Action $definition.Action `
        -Trigger $definition.Trigger `
        -Settings $definition.Settings `
        -Principal $definition.Principal `
        -Description $definition.Description `
        -DisableAfterRegistration:([bool]$definition.Disable)
}

$managedTasks = @(Get-ScheduledTask -TaskName "IBKR Bot -*" | Sort-Object TaskName)
if ($managedTasks.Count -ne 10) {
    throw "Expected exactly 10 IBKR Bot tasks, found $($managedTasks.Count)."
}

$duplicates = $managedTasks | Group-Object TaskName | Where-Object Count -gt 1
if ($duplicates) {
    throw "Duplicate IBKR Bot task names detected: $($duplicates.Name -join ', ')"
}

$summary = @(
    foreach ($task in $managedTasks) {
        $info = Get-ScheduledTaskInfo -TaskName $task.TaskName
        $action = $task.Actions | Select-Object -First 1
        [pscustomobject]@{
            TaskName = $task.TaskName
            State = $task.State
            Enabled = ($task.State -ne "Disabled")
            Execute = $action.Execute
            Arguments = $action.Arguments
            WorkingDirectory = $action.WorkingDirectory
            Triggers = Get-TriggerSummary -Task $task
            RunAsUser = $task.Principal.UserId
            RunLevel = $task.Principal.RunLevel
            MultipleInstances = $task.Settings.MultipleInstances
            ExecutionTimeLimit = $task.Settings.ExecutionTimeLimit
            NextRunTime = $info.NextRunTime
        }
    }
)

$runtimeDirectory = Join-Path $projectRoot "memory\runtime"
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$summaryPath = Join-Path $runtimeDirectory "windows_scheduled_tasks_summary.json"
$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding UTF8

Write-Host "Installed or updated 10 IBKR Bot scheduled tasks."
Write-Host "Project root: $projectRoot"
Write-Host "Python executable: $pythonExecutable"
Write-Host "Run-as user: $([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)"
Write-Host "Timezone: $((Get-TimeZone).Id)"
Write-Host "Slack task disabled: $((Get-ScheduledTask -TaskName 'IBKR Bot - Slack').State -eq 'Disabled')"
Write-Host "Verification summary: $summaryPath"
Write-Host ""
$summary | Select-Object TaskName, State, Execute, Arguments, WorkingDirectory, RunAsUser, RunLevel, MultipleInstances, ExecutionTimeLimit, NextRunTime | Format-Table -AutoSize -Wrap
