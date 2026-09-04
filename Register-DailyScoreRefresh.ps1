# Local time is Pacific, so 10:00 lands mid-session (13:00 ET) and 15:00 lands
# after the close (18:00 ET). The snapshot logger keeps only the last entry per
# (date, ticker), so the intraday run refreshes the dashboard while the
# post-close run is what gets recorded for backtesting.
param(
    [string]$TaskName = "AI Valuation Daily Score Refresh",
    [string[]]$Times = @("10:00", "15:00")
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $Root "Update-DailyScores.ps1"

if (-not (Test-Path -LiteralPath $Script)) {
    throw "Missing script: $Script"
}

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" -WorkingDirectory $Root
$Triggers = @()
foreach ($T in $Times) {
    $Triggers += New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $T
}
# IgnoreNew matters more with two runs a day: if a refresh ever overruns, the
# next trigger is skipped rather than starting a second concurrent write to
# results_validated.csv.
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers -Settings $Settings -Principal $Principal -Force | Out-Null
Write-Host "Registered scheduled task: $TaskName"
Write-Host "Schedule: weekdays at $($Times -join ', ') (local time)"
Write-Host "Script: $Script"
Write-Host "Logs: $(Join-Path $Root 'logs')"
