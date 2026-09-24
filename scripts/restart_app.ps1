<#
.SYNOPSIS
    杀掉所有 Streamlit 实例，确认端口空出来，然后只启动一个。

.DESCRIPTION
    这个脚本存在的原因是同一类事故反复发生过三次：

    1. 机器上有两份这个项目的克隆，在一份里 git pull、启动的却是另一份，
       于是"代码改了页面没变"连续三轮（start_app.bat 曾把路径写死成旧克隆）。
    2. 8501 被旧进程占着时 Streamlit 会自动跳到 8502，于是两个实例同时跑。
       这不只是碍眼——account_monitor.py 的 BackgroundScheduler 会跟着起两份，
       09:35/12:30/15:30/16:30 的 _auto_sync 各跑一遍，而 positions 是只增不
       改的追加表，同一时刻两次同步就会给同一只股票写进两行。BD 被重复计到
       378% 就是这么来的。@st.cache_resource 的去重只在单进程内有效，拦不住
       两个进程。
    3. 代码推在功能分支上，而本地停在 master，git pull 永远拉不到。

    所以启动前把这三件事全查一遍，有问题就停下来说清楚，而不是默默起一个。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\restart_app.ps1

.EXAMPLE
    # 只想看看现状，不真的重启
    powershell -ExecutionPolicy Bypass -File scripts\restart_app.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [int]    $Port     = 8501,
    # 留空则用脚本所在的仓库当前分支，不强制切
    [string] $Branch   = "",
    # 跳过"另一份克隆"的扫描（扫描要遍历用户目录，慢的话可以关）
    [switch] $SkipCloneScan
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }

Say "`n仓库: $Repo" 'Cyan'

# ── 1. 杀掉所有 Streamlit 实例 ────────────────────────────────────────
# 只挑命令行里带 streamlit 的，不误伤其它 python。
$procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
           Where-Object { $_.CommandLine -match 'streamlit' })
if ($procs.Count -eq 0) {
    Say "没有正在跑的 Streamlit。"
} else {
    Say "发现 $($procs.Count) 个 Streamlit 进程：" 'Yellow'
    foreach ($p in $procs) {
        $port = if ($p.CommandLine -match 'server\.port\s+(\d+)') { $Matches[1] } else { '默认' }
        Say "   PID $($p.ProcessId)  端口 $port"
    }
    if ($procs.Count -gt 1) {
        Say "   ⚠ 多于一个——定时任务会各跑一遍，positions 表可能已经有重复行。" 'Red'
        Say "     启动后跑 python scripts\diagnose_sync.py 查一下。" 'Red'
    }
    if ($PSCmdlet.ShouldProcess("$($procs.Count) 个 Streamlit 进程", "结束")) {
        foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
        Say "已全部结束。" 'Green'
    }
}

# ── 2. 确认端口真的空了 ───────────────────────────────────────────────
# 不确认的话，端口还占着时 Streamlit 会静默跳到下一个端口，又变成两个实例。
$still = @(Get-NetTCPConnection -LocalPort $Port, ($Port + 1) -State Listen -ErrorAction SilentlyContinue)
if ($still.Count -gt 0) {
    Say "`n端口还被占着，没清干净：" 'Red'
    $still | ForEach-Object { Say "   端口 $($_.LocalPort)  PID $($_.OwningProcess)" 'Red' }
    Say "先手动结束这些 PID 再重跑本脚本。没清干净就启动，只会又多一个实例。" 'Red'
    exit 1
}
Say "端口 $Port / $($Port + 1) 都空着。" 'Green'

# ── 3. 分支和提交 ─────────────────────────────────────────────────────
if ($Branch) {
    if ($PSCmdlet.ShouldProcess($Branch, "fetch 并切换分支")) {
        git fetch origin --quiet
        git checkout $Branch --quiet
        git pull --quiet
    }
}
$current = (git rev-parse --abbrev-ref HEAD).Trim()
$commit  = (git log --oneline -1).Trim()
Say "`n分支: $current"
Say "提交: $commit"

$behind = (git rev-list --count "HEAD..origin/$current" 2>$null)
if ($behind -and [int]$behind -gt 0) {
    Say "   ⚠ 落后远端 $behind 个提交，先 git pull。" 'Yellow'
}

# ── 4. 机器上还有没有别的克隆 ─────────────────────────────────────────
# 这是"改了代码没生效"最常见的真实原因：改的和跑的不是同一份。
if (-not $SkipCloneScan) {
    $others = @(Get-ChildItem $env:USERPROFILE -Recurse -Depth 4 -Filter 'home.py' `
                  -ErrorAction SilentlyContinue |
                ForEach-Object { $_.DirectoryName } |
                Where-Object { $_ -ne $Repo })
    if ($others.Count -gt 0) {
        Say "`n⚠ 机器上还有 $($others.Count) 份其它克隆：" 'Yellow'
        $others | ForEach-Object {
            $c = (git -C $_ log --oneline -1 2>$null)
            Say "   $_`n      $c" 'Yellow'
        }
        Say "   本次启动的是最上面那个「仓库:」路径。别的那几份建议改名归档，" 'Yellow'
        Say "   免得下次又在其中一份里 pull、却启动另一份。" 'Yellow'
    }
}

# ── 5. 启动，只启动一个 ───────────────────────────────────────────────
Say "`n启动 http://localhost:$Port  （Ctrl+C 停止）`n" 'Cyan'
if ($PSCmdlet.ShouldProcess("home.py", "在端口 $Port 启动 Streamlit")) {
    python -X utf8 -m streamlit run home.py --server.port $Port
}
