param(
    [string]$Distro = "",
    [string]$ProjectPath = "",
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA "LedgerAgent"),
    [string]$ShortcutDirectory = ([Environment]::GetFolderPath("Desktop")),
    [string]$WebUrl = "http://127.0.0.1:8000",
    [switch]$DryRun,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$shortcutName = (-join @([char]0x8BB0, [char]0x8D26, [char]0x52A9, [char]0x624B)) + ".lnk"
$shortcutPath = Join-Path $ShortcutDirectory $shortcutName
$launcherScript = Join-Path $InstallDirectory "open_ledger_agent.ps1"
$configPath = Join-Path $InstallDirectory "launcher.json"

if ($Uninstall) {
    if (Test-Path -LiteralPath $shortcutPath) {
        Remove-Item -LiteralPath $shortcutPath -Force
    }
    foreach ($name in @("open_ledger_agent.ps1", "open_ledger_agent.cmd", "install_windows_launcher.ps1", "install_windows_reminder.ps1", "launcher.json", "launcher.log")) {
        $path = Join-Path $InstallDirectory $name
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force
        }
    }
    Write-Output "Removed launcher files from: $InstallDirectory"
    exit 0
}

if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
    throw "ProjectPath is required, for example: /home/user/financial-agent"
}
if (-not $ProjectPath.StartsWith("/")) {
    throw "ProjectPath must be an absolute Linux path."
}
if (-not $WebUrl.StartsWith("http://127.0.0.1:")) {
    throw "WebUrl must use the local loopback address http://127.0.0.1."
}

$sourceLauncher = Join-Path $PSScriptRoot "open_ledger_agent.ps1"
$sourceCommand = Join-Path $PSScriptRoot "open_ledger_agent.cmd"
$sourceInstaller = Join-Path $PSScriptRoot "install_windows_launcher.ps1"
$sourceReminderInstaller = Join-Path $PSScriptRoot "install_windows_reminder.ps1"
foreach ($source in @($sourceLauncher, $sourceCommand, $sourceInstaller, $sourceReminderInstaller)) {
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Required installer file not found: $source"
    }
}

if ($DryRun) {
    Write-Output "Distro: $Distro"
    Write-Output "Project: $ProjectPath"
    Write-Output "Install directory: $InstallDirectory"
    Write-Output "Configuration: $configPath"
    Write-Output "Shortcut: $shortcutPath"
    exit 0
}

New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $ShortcutDirectory -Force | Out-Null
$copies = @(
    [pscustomobject]@{ Source = $sourceLauncher; Destination = $launcherScript },
    [pscustomobject]@{ Source = $sourceCommand; Destination = (Join-Path $InstallDirectory "open_ledger_agent.cmd") },
    [pscustomobject]@{ Source = $sourceInstaller; Destination = (Join-Path $InstallDirectory "install_windows_launcher.ps1") },
    [pscustomobject]@{ Source = $sourceReminderInstaller; Destination = (Join-Path $InstallDirectory "install_windows_reminder.ps1") }
)
foreach ($copy in $copies) {
    $sourceFullPath = [IO.Path]::GetFullPath($copy.Source)
    $destinationFullPath = [IO.Path]::GetFullPath($copy.Destination)
    if (-not $sourceFullPath.Equals($destinationFullPath, [StringComparison]::OrdinalIgnoreCase)) {
        Copy-Item -LiteralPath $copy.Source -Destination $copy.Destination -Force
    }
}

$config = [ordered]@{
    distro = $Distro
    projectPath = $ProjectPath.TrimEnd("/")
    webUrl = $WebUrl.TrimEnd("/")
}
$config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$shortcut.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcherScript`""
$shortcut.WorkingDirectory = $InstallDirectory
$shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,109"
$shortcut.Description = "Start the local Ledger Agent and open it in a browser"
$shortcut.Save()

Write-Output "Created: $shortcutPath"
Write-Output "Configuration: $configPath"
