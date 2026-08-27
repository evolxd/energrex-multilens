# Registers a weekly Windows scheduled task for Update-WeeklyMethodologyChecks.ps1.
# Default: Monday 09:00 local time -- after the weekend, before the week's
# first daily score refresh, so a fresh methodology report is sitting there
# if anyone looks at the dashboard early in the week.
param(
    [string]$TaskName = "AI Valuation Weekly Methodology Check",
    [string]$Time = "09:00"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $Root "Update-WeeklyMethodologyChecks.ps1"

if (-not (Test-Path -LiteralPath $Script)) {
    throw "Missing script: $Script"
}

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
Write-Host "Registered scheduled task: $TaskName"
Write-Host "Schedule: Mondays at $Time (local time)"
Write-Host "Script: $Script"
Write-Host "Logs: $(Join-Path $Root 'logs')"
Write-Host "Reports: scoring/weight_config_backtest_report.json, scoring/spread_decomposition_report.json, scoring/risk_subcomponent_report.json"
