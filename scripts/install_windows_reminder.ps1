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
        (Join-Path $WrapperDir "run_daily_reminder.vbs"),
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
$wscript = Join-Path $env:SystemRoot "System32\wscript.exe"
$wrapperPath = Join-Path $WrapperDir "daily_reminder.ps1"
$vbsPath = Join-Path $WrapperDir "run_daily_reminder.vbs"
$wrapperLog = Join-Path $WrapperDir "daily_reminder.log"
$reminderConfigPath = Join-Path $WrapperDir "reminder.json"
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapperPath`""

if ($DryRun) {
    Write-Output "Task: $taskName"
    Write-Output "Daily reminder time: $Time"
    Write-Output "Schedule: runs once per day at this time"
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
    defaultReminderTime = $Time
}
$reminderConfig | ConvertTo-Json | Set-Content -LiteralPath $reminderConfigPath -Encoding UTF8

$configLiteral = "'" + $reminderConfigPath.Replace("'", "''") + "'"
$logLiteral = "'" + $wrapperLog.Replace("'", "''") + "'"
$wrapper = @"
`$ErrorActionPreference = "Stop"
`$wsl = "$wsl"
`$config = Get-Content -Raw -LiteralPath $configLiteral | ConvertFrom-Json
`$url = "`$(`$config.webUrl)/?view=chat&reminder=daily"
`$webPort = ([Uri]`$config.webUrl).Port
`$defaultReminderTime = [string]`$config.defaultReminderTime
`$log = $logLiteral
`$reminderScript = "`$(`$config.projectPath.TrimEnd('/'))/scripts/daily_reminder.sh"
`$wslExitCode = 1

try {
    # Do not splat a native-command argument array here. Windows PowerShell can
    # pass it to WSL as a literal @-d token when this task runs non-interactively.
    if ([string]::IsNullOrWhiteSpace([string]`$config.distro)) {
        & `$wsl "--exec" "env" "LEDGER_AGENT_PORT=`$webPort" "LEDGER_REMINDER_DEFAULT_TIME=`$defaultReminderTime" "bash" `$reminderScript *>> `$log
    } else {
        & `$wsl "-d" ([string]`$config.distro) "--exec" "env" "LEDGER_AGENT_PORT=`$webPort" "LEDGER_REMINDER_DEFAULT_TIME=`$defaultReminderTime" "bash" `$reminderScript *>> `$log
    }
    `$wslExitCode = `$LASTEXITCODE
    if (`$wslExitCode -eq 10) {
        exit 0
    }
    if (`$wslExitCode -ne 0) {
        throw "WSL reminder failed with exit code `$wslExitCode."
    }

    "`$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') reminder due" | Out-File -FilePath `$log -Append -Encoding utf8
    Start-Sleep -Seconds 2
    Invoke-WebRequest -Uri "`$(`$config.webUrl)/api/health" -UseBasicParsing -TimeoutSec 15 | Out-Null
    if (`$env:LEDGER_REMINDER_NO_BROWSER -ne "1") {
        Start-Process `$url
    }
} catch {
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

$vbs = @"
Set shell = CreateObject("WScript.Shell")
shell.Run Chr(34) & "$execute" & Chr(34) & " -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & "$wrapperPath" & Chr(34), 0, False
"@
Set-Content -Path $vbsPath -Value $vbs -Encoding ASCII

$taskCommand = "`"$wscript`" `"$vbsPath`""
& schtasks.exe /Create /TN $taskName /TR $taskCommand /SC DAILY /ST $Time /F | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to register scheduled task."
}

Write-Output "Installed scheduled task: $taskName at $Time"
