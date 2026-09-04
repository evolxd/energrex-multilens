$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$EnvNames = @("MARKETDATA_API_KEY", "POLYGON_API_KEY", "SEC_USER_AGENT")
foreach ($EnvName in $EnvNames) {
    if (-not [Environment]::GetEnvironmentVariable($EnvName, "Process")) {
        $UserValue = [Environment]::GetEnvironmentVariable($EnvName, "User")
        if ($UserValue) { [Environment]::SetEnvironmentVariable($EnvName, $UserValue, "Process") }
    }
}

try { chcp 65001 > $null } catch { }

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "daily_scores_$Stamp.log"

Start-Transcript -Path $LogFile -Force | Out-Null
try {
    Write-Host "ai_valuation daily score refresh started: $(Get-Date -Format o)"
    Write-Host "Workspace: $Root"

    python refresh_scores.py

    Write-Host "ai_valuation daily score refresh finished: $(Get-Date -Format o)"
}
finally {
    Stop-Transcript | Out-Null
}
