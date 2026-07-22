param(
    [string]$Distro = "",
    [string]$ProjectPath = "",
    [string]$ConfigPath = "",
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$Time = "22:00",
    [string]$WrapperDir = (Join-Path $env:LOCALAPPDATA "LedgerAgent\reminder"),
    [string]$WebUrl = "",
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$taskName = "Ledger Agent Daily Reminder"

if ($Uninstall) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output "Removed scheduled task: $taskName"
    } else {
        Write-Output "Scheduled task does not exist: $taskName"
    }
    $generatedFiles = @(
        (Join-Path $WrapperDir "daily_reminder.ps1"),
        (Join-Path $WrapperDir "daily_reminder.log"),
        (Join-Path $WrapperDir "reminder.json")
    )
    foreach ($generatedFile in $generatedFiles) {
        if (Test-Path $generatedFile) {
            Remove-Item $generatedFile -Force
        }
    }
    exit 0
}

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $localConfig = Join-Path $PSScriptRoot "launcher.json"
    $defaultConfig = Join-Path $env:LOCALAPPDATA "LedgerAgent\launcher.json"
    if (Test-Path -LiteralPath $localConfig) {
        $ConfigPath = $localConfig
    } elseif (Test-Path -LiteralPath $defaultConfig) {
        $ConfigPath = $defaultConfig
    }
}

if (-not [string]::IsNullOrWhiteSpace($ConfigPath) -and (Test-Path -LiteralPath $ConfigPath)) {
    $launcherConfig = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
    if ([string]::IsNullOrWhiteSpace($Distro)) {
        $Distro = [string]$launcherConfig.distro
    }
    if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
        $ProjectPath = [string]$launcherConfig.projectPath
    }
    if ([string]::IsNullOrWhiteSpace($WebUrl)) {
        $WebUrl = [string]$launcherConfig.webUrl
    }
}

if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
    throw "ProjectPath is required. Run install_windows_launcher.ps1 first or pass -ProjectPath."
}
if (-not $ProjectPath.StartsWith("/")) {
    throw "ProjectPath must be an absolute Linux path."
}
if ([string]::IsNullOrWhiteSpace($WebUrl)) {
    $WebUrl = "http://127.0.0.1:8000"
}
if (-not $WebUrl.StartsWith("http://127.0.0.1:")) {
    throw "WebUrl must use the local loopback address http://127.0.0.1."
}

$wsl = Join-Path $env:SystemRoot "System32\wsl.exe"
$execute = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$wrapperPath = Join-Path $WrapperDir "daily_reminder.ps1"
$wrapperLog = Join-Path $WrapperDir "daily_reminder.log"
$reminderConfigPath = Join-Path $WrapperDir "reminder.json"
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapperPath`""

if ($DryRun) {
    Write-Output "Task: $taskName"
    Write-Output "Time: $Time"
    Write-Output "Execute: $execute"
    Write-Output "Arguments: $arguments"
    Write-Output "Wrapper: $wrapperPath"
    Write-Output "Log: $wrapperLog"
    Write-Output "Distro: $Distro"
    Write-Output "Project: $ProjectPath"
    exit 0
}

New-Item -ItemType Directory -Path $WrapperDir -Force | Out-Null
$reminderConfig = [ordered]@{
    distro = $Distro
    projectPath = $ProjectPath.TrimEnd("/")
    webUrl = $WebUrl.TrimEnd("/")
}
$reminderConfig | ConvertTo-Json | Set-Content -LiteralPath $reminderConfigPath -Encoding UTF8

$configLiteral = "'" + $reminderConfigPath.Replace("'", "''") + "'"
$logLiteral = "'" + $wrapperLog.Replace("'", "''") + "'"
$wrapper = @"
`$ErrorActionPreference = "Stop"
`$wsl = "$wsl"
`$config = Get-Content -Raw -LiteralPath $configLiteral | ConvertFrom-Json
`$url = "`$(`$config.webUrl)/?view=chat&reminder=daily"
`$log = $logLiteral
`$reminderScript = "`$(`$config.projectPath.TrimEnd('/'))/scripts/daily_reminder.sh"
`$wslExitCode = 1

try {
    "`$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') reminder wrapper started" | Out-File -FilePath `$log -Append -Encoding utf8
    `$previousReminderNoBrowser = `$env:LEDGER_REMINDER_NO_BROWSER
    `$env:LEDGER_REMINDER_NO_BROWSER = "1"
    # Do not splat a native-command argument array here. Windows PowerShell can
    # pass it to WSL as a literal @-d token when this task runs non-interactively.
    if ([string]::IsNullOrWhiteSpace([string]`$config.distro)) {
        & `$wsl "--exec" "bash" `$reminderScript *>> `$log
    } else {
        & `$wsl "-d" ([string]`$config.distro) "--exec" "bash" `$reminderScript *>> `$log
    }
    `$env:LEDGER_REMINDER_NO_BROWSER = `$previousReminderNoBrowser
    `$wslExitCode = `$LASTEXITCODE
    "`$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') WSL exit code: `$wslExitCode" | Out-File -FilePath `$log -Append -Encoding utf8
    if (`$wslExitCode -ne 0) {
        throw "WSL reminder failed with exit code `$wslExitCode."
    }

    Start-Sleep -Seconds 2
    Invoke-WebRequest -Uri "`$(`$config.webUrl)/api/health" -UseBasicParsing -TimeoutSec 15 | Out-Null
    if (`$env:LEDGER_REMINDER_NO_BROWSER -ne "1") {
        Start-Process `$url
    }
} catch {
    `$env:LEDGER_REMINDER_NO_BROWSER = `$previousReminderNoBrowser
    "`$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') reminder failed: `$(`$_.Exception.Message)" | Out-File -FilePath `$log -Append -Encoding utf8
    exit `$wslExitCode
}
"@
$parseTokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseInput(
    $wrapper,
    [ref]$parseTokens,
    [ref]$parseErrors
) | Out-Null
if ($parseErrors.Count -gt 0) {
    $messages = ($parseErrors | ForEach-Object { $_.Message }) -join "; "
    throw "Generated reminder wrapper is invalid: $messages"
}
Set-Content -Path $wrapperPath -Value $wrapper -Encoding UTF8

$action = New-ScheduledTaskAction -Execute $execute -Argument $arguments -WorkingDirectory $WrapperDir
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Open the local Ledger Agent UI every day for bookkeeping." `
    -Force | Out-Null

Write-Output "Installed scheduled task: $taskName at $Time"
