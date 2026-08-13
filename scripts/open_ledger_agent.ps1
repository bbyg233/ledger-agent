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
$webPort = ([Uri]$webUrl).Port
if ($webPort -lt 1) {
    throw "The launcher configuration contains an invalid webUrl: $webUrl"
}

$logPath = Join-Path $PSScriptRoot "launcher.log"
$wsl = Join-Path $env:SystemRoot "System32\wsl.exe"
$startScript = "$projectPath/scripts/start_web.sh"

try {
    # Pass every native WSL argument explicitly. Splatted argument arrays can
    # split the Linux project path when it contains spaces.
    # Native-command stderr must remain non-terminating so it can be saved to
    # launcher.log instead of being reduced to a malformed PowerShell message.
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        if ([string]::IsNullOrWhiteSpace($distro)) {
            $output = & $wsl "--exec" "env" "LEDGER_AGENT_PORT=$webPort" "bash" $startScript 2>&1
        } else {
            $output = & $wsl "-d" $distro "--exec" "env" "LEDGER_AGENT_PORT=$webPort" "bash" $startScript 2>&1
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $outputText = (($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine).Trim()
    $outputText | Set-Content -Path $logPath -Encoding UTF8
    if ($exitCode -ne 0) {
        $detail = if ($outputText) { "`n`n$outputText" } else { "" }
        throw "WSL startup failed with exit code $exitCode.$detail"
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
