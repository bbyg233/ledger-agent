param(
    [string]$ConfigPath = "",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $PSScriptRoot "launcher.json"
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Launcher configuration not found: $ConfigPath. Run install_windows_launcher.ps1 first."
}

$config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
$distro = [string]$config.distro
$projectPath = ([string]$config.projectPath).TrimEnd("/")
$webUrl = [string]$config.webUrl
if ([string]::IsNullOrWhiteSpace($projectPath)) {
    throw "The launcher configuration does not contain projectPath."
}
if ([string]::IsNullOrWhiteSpace($webUrl)) {
    $webUrl = "http://127.0.0.1:8000"
}

$logPath = Join-Path $PSScriptRoot "launcher.log"
$wsl = Join-Path $env:SystemRoot "System32\wsl.exe"
$startScript = "$projectPath/scripts/start_web.sh"
$wslArgs = @()
if (-not [string]::IsNullOrWhiteSpace($distro)) {
    $wslArgs += @("-d", $distro)
}
$wslArgs += @(
    "--exec",
    "bash",
    $startScript
)

try {
    $output = & $wsl @wslArgs 2>&1
    $output | Set-Content -Path $logPath -Encoding UTF8
    if ($LASTEXITCODE -ne 0) {
        throw "WSL startup failed with exit code $LASTEXITCODE."
    }
    if (-not $NoBrowser) {
        Start-Process $webUrl
    }
}
catch {
    $message = "Ledger Agent failed to start.`n`n$($_.Exception.Message)`n`nLog: $logPath"
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        $message,
        "Ledger Agent",
        [System.Windows.MessageBoxButton]::OK,
        [System.Windows.MessageBoxImage]::Error
    ) | Out-Null
    exit 1
}
