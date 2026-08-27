# Runs the three approximate-backtest diagnostics that check the scoring
# methodology itself (not the scores) -- weight_config_backtest.py,
# spread_decomposition_backtest.py, risk_subcomponent_backtest.py.
#
# Weekly, not daily: each one re-pulls 2y of price history for the whole
# universe, and the methodology_caveat baked into every report explains why
# day-to-day reruns wouldn't tell you anything new anyway (approximate,
# survivorship-biased backtest against results_validated.csv's current
# scores -- see any of the three .json reports in scoring/ for the full
# text). This exists because weight_config_backtest_report.json sat
# unnoticed and unrun for a month (2026-07-30 -> 2026-08-27) despite
# flagging a real problem with current_final's discriminative power --
# see docs/system_map.html's changelog for that story. Running on a fixed
# schedule doesn't replace someone actually reading the output, but it
# removes "nobody remembered to run it again" as a way for a finding to go
# stale unnoticed.

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

try { chcp 65001 > $null } catch { }

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "weekly_methodology_$Stamp.log"

Start-Transcript -Path $LogFile -Force | Out-Null
try {
    Write-Host "Weekly methodology checks started: $(Get-Date -Format o)"
    Write-Host "Workspace: $Root"

    python scoring/weight_config_backtest.py
    python scoring/spread_decomposition_backtest.py
    python scoring/risk_subcomponent_backtest.py

    Write-Host "Weekly methodology checks finished: $(Get-Date -Format o)"
    Write-Host "Reports written to scoring/*_report.json -- read them, this script only reruns them."
}
finally {
    Stop-Transcript | Out-Null
}
