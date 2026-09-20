#!/usr/bin/env python3
"""One command that answers "why isn't the data updating".

Every fix so far has been a hypothesis formed from reading code, because the
machine that actually runs the sync is not the machine the code gets written
on. This collects the runtime facts in one pass so the next change is aimed at
something observed instead of something guessed.

    python scripts/diagnose_sync.py

Read-only. Touches no browser, writes nothing, and never prints a password --
credentials are reported as configured / not configured only.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CDP_ADDR = "localhost:9222"
ACCOUNT = "account_1"


def section(title: str) -> None:
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def _ago(stamp: str | None) -> str:
    if not stamp:
        return "（无）"
    try:
        when = dt.datetime.fromisoformat(str(stamp))
    except ValueError:
        return f"{stamp}（无法解析）"
    now = dt.datetime.now(when.tzinfo) if when.tzinfo else dt.datetime.now()
    hours = (now - when).total_seconds() / 3600
    return f"{stamp}  ({hours/24:.1f} 天前)" if hours >= 24 else f"{stamp}  ({hours:.1f} 小时前)"


def check_code_version() -> None:
    section("1. 代码版本（确认修复到底有没有拉下来）")
    for cmd, label in ((["git", "log", "-1", "--format=%h %ci %s"], "当前 HEAD"),
                       (["git", "status", "--porcelain"], "未提交改动")):
        try:
            out = subprocess.run(cmd, cwd=ROOT, capture_output=True,
                                 text=True, timeout=10).stdout.strip()
            print(f"  {label}: {out or '（无）'}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {label}: 读取失败 {exc}")

    # 这几个文件只存在于最近的修复里，可以当版本探针用
    probes = {
        "account/positions_xlsx.py": "股票 sheet 解析（xlsx 同步股票）",
        "account/nav_continuity.py": "净值曲线缺口检测",
        "scripts/recover_broker_downloads.py": "下载文件恢复工具",
    }
    for rel, desc in probes.items():
        print(f"  {'✓' if (ROOT / rel).exists() else '✗'} {rel:<38} {desc}")

    monitor = (ROOT / "account_monitor.py").read_text(encoding="utf-8", errors="ignore")
    for marker, desc in (("_open_login_tab", "自动打开登录页"),
                         ("_install_log_file_handler", "日志落盘"),
                         ("_sync_stocks_from_xlsx", "xlsx 股票入库")):
        print(f"  {'✓' if marker in monitor else '✗'} account_monitor.{marker:<26} {desc}")


def check_clones() -> None:
    """Other checkouts of this repository on the same machine.

    Each clone carries its own data/energrex.db, so editing one while the app
    serves another produces fixes that appear to do nothing. On 2026-09-20
    three directories were in play at once: the running app, a second clone on
    an older commit, and an unrelated repository where `git pull` was being
    typed. Nothing on screen distinguished them.
    """
    section("0. 本机的其他克隆（多份副本是最隐蔽的坑）")
    print(f"  本次运行自: {ROOT}")
    home = pathlib.Path.home()
    found: list[pathlib.Path] = []
    for marker in home.glob("**/war_room.py"):
        try:
            candidate = marker.parent.resolve()
        except OSError:
            continue
        if candidate != ROOT.resolve() and (candidate / ".git").exists():
            found.append(candidate)
        if len(found) >= 8:
            break

    if not found:
        print("  ✓ 未发现其他克隆")
        return
    print(f"  ⚠️ 发现 {len(found)} 个其他克隆——确认应用跑的是哪一个：")
    for path in found:
        head = branch = "?"
        try:
            head = subprocess.run(["git", "log", "-1", "--format=%h"], cwd=path,
                                  capture_output=True, text=True, timeout=5).stdout.strip()
            branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path,
                                    capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            pass
        db = path / "data" / "energrex.db"
        when = (dt.datetime.fromtimestamp(db.stat().st_mtime).strftime("%m-%d %H:%M")
                if db.exists() else "无 DB")
        print(f"    {path}")
        print(f"      分支 {branch}  HEAD {head}  DB最后写入 {when}")
    print("  → DB 写入时间最新的那个，才是应用实际在用的副本；其余建议改名归档")


def check_env() -> None:
    section("2. 凭据配置（决定掉线能否自愈；不会打印密码）")
    env_path = ROOT / ".env"
    print(f"  .env 文件: {'存在' if env_path.exists() else '不存在'}  {env_path}")
    values: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    for key in ("FIRSTRADE_USER_1", "FIRSTRADE_PASS_1",
                "MARKETDATA_API_KEY", "FRED_API_KEY"):
        present = bool(values.get(key) or os.environ.get(key))
        print(f"  {'✓ 已配置' if present else '✗ 未配置'}  {key}")
    if not (values.get("FIRSTRADE_USER_1") and values.get("FIRSTRADE_PASS_1")):
        print("  → 未配置则 _do_ft_login() 直接放弃，session 过期后无法自动恢复")


def check_chrome() -> None:
    section("3. Chrome CDP 与 Firstrade 登录态")
    try:
        with urllib.request.urlopen(f"http://{CDP_ADDR}/json/version", timeout=3) as resp:
            version = json.loads(resp.read())
        print(f"  ✓ CDP 可达  {version.get('Browser', '?')}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ CDP 不可达 ({exc}) — Chrome 没以调试模式启动，同步一定失败")
        print("    → 先跑 start_chrome.bat，或让同步自己启动它")
        return

    try:
        with urllib.request.urlopen(f"http://{CDP_ADDR}/json", timeout=3) as resp:
            tabs = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  标签页列表读取失败: {exc}")
        return

    pages = [t for t in tabs if t.get("type") == "page"]
    print(f"  打开的标签页 {len(pages)} 个：")
    logged_in = False
    for tab in pages[:12]:
        url = tab.get("url", "")
        mark = ""
        if "invest.firstrade.com/app" in url:
            logged_in = True
            mark = "  ← 登录态判定依据"
        elif "firstrade" in url:
            mark = "  ← Firstrade 但不在 /app（可能停在登录页）"
        print(f"    {url[:90]}{mark}")
    print(f"  登录判定: {'✓ 已登录' if logged_in else '✗ 未登录 → _ensure_chrome 会返回 needs_login 并中止同步'}")


def check_db() -> None:
    section("4. 数据库实际内容（数据到底新不新）")
    db_path = ROOT / "data" / "energrex.db"
    if not db_path.exists() or db_path.stat().st_size == 0:
        print(f"  ✗ {db_path} 不存在或为空")
        return
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    def one(sql: str, *params):
        try:
            return conn.execute(sql, params).fetchone()
        except sqlite3.Error as exc:
            print(f"    查询失败: {exc}")
            return None

    bal = one("SELECT sync_time, total_equity, cash_balance FROM account_balance "
              "WHERE account_id=? ORDER BY sync_time DESC LIMIT 1", ACCOUNT)
    if bal:
        print(f"  余额最后同步: {_ago(bal['sync_time'])}")
        print(f"    净值 ${bal['total_equity'] or 0:,.2f}   现金 ${bal['cash_balance'] or 0:,.2f}")
    else:
        print("  余额: 无记录")

    stocks = one(
        "SELECT COUNT(*) n, MAX(sync_time) t FROM positions p1 "
        "WHERE p1.account_id=? AND p1.position_type='stock' "
        "AND IFNULL(p1.quantity,0)!=0 AND p1.sync_time=("
        "  SELECT MAX(p2.sync_time) FROM positions p2 "
        "  WHERE p2.symbol=p1.symbol AND p2.account_id=p1.account_id)", ACCOUNT)
    if stocks:
        print(f"  股票持仓: {stocks['n']} 只   最后写入 {_ago(stocks['t'])}")

    opts = one("SELECT COUNT(*) n, MAX(last_updated) t FROM options_positions "
               "WHERE account_id=?", ACCOUNT)
    if opts:
        print(f"  期权持仓: {opts['n']} 笔   最后写入 {_ago(opts['t'])}")

    nav = one("SELECT COUNT(*) n, MIN(date) a, MAX(date) b FROM daily_nav "
              "WHERE account_id=?", ACCOUNT)
    if nav and nav["n"]:
        print(f"  净值曲线: {nav['n']} 个点   {nav['a']} ~ {nav['b']}")
        try:
            from account.nav_continuity import analyse, load_nav_dates
            cont = analyse(load_nav_dates(conn, ACCOUNT))
            print(f"    覆盖率 {cont.coverage_pct}%  缺 {cont.missing} 个交易日")
            for gap in cont.gaps[-5:]:
                print(f"      缺口 {gap.label()}")
        except Exception as exc:  # noqa: BLE001
            print(f"    缺口检测不可用: {exc}")

    txn = one("SELECT COUNT(*) n, MAX(trade_date) t FROM transactions WHERE account_id=?", ACCOUNT)
    if txn:
        print(f"  成交记录: {txn['n']} 条   最新 {txn['t'] or '（无）'}")

    # positions 是追加式快照表，风险快照靠"每只票取 MAX(sync_time) 那条"去重。
    # 同一只票若有多行共享同一个 sync_time，这个写法会全部命中——敞口照样被
    # 重复计算，BD 会随之虚高（2026-09-19 的修复处理的是不同 sync_time 那种，
    # 这里查的是它管不到的那种）。
    dupes = conn.execute(
        """
        SELECT symbol, sync_time, COUNT(*) n FROM positions
        WHERE account_id=? AND position_type='stock' AND IFNULL(quantity,0)!=0
        GROUP BY symbol, sync_time HAVING COUNT(*) > 1
        ORDER BY n DESC LIMIT 10
        """,
        (ACCOUNT,),
    ).fetchall()
    if dupes:
        print(f"  ⚠️ 同一 sync_time 下的重复股票行（会导致敞口/BD 重复计算）：")
        for row in dupes:
            print(f"      {row['symbol']:<6} {row['sync_time']}  ×{row['n']}")
    else:
        print("  ✓ 无同 sync_time 重复股票行")

    snaps = one("SELECT COUNT(DISTINCT sync_time) n FROM positions "
                "WHERE account_id=? AND position_type='stock'", ACCOUNT)
    if snaps:
        print(f"  股票快照批次: {snaps['n']} 批（每次同步追加一批，属正常累积）")
    conn.close()


def check_downloads() -> None:
    section("5. 下载目录实况")
    home = pathlib.Path.home()
    dirs = [home / "Downloads"]
    if (home / "Downloads").exists():
        dirs += sorted(p for p in (home / "Downloads").glob("energrex_*") if p.is_dir())
    for directory in dirs:
        if not directory.exists():
            print(f"  ✗ {directory}（不存在）")
            continue
        files = sorted(
            [p for p in directory.glob("*.xlsx") if not p.name.startswith("~$")]
            + list(directory.glob("export*.csv")),
            key=lambda p: p.stat().st_mtime, reverse=True)[:8]
        print(f"  {directory}  —  {len(files)} 个相关文件（最多列 8 个）")
        for path in files:
            built = ""
            if path.suffix == ".xlsx":
                try:
                    with zipfile.ZipFile(path) as zf:
                        stamps = [i.date_time for i in zf.infolist() if i.date_time[0] > 1980]
                    if stamps:
                        built = f"  导出于 {dt.datetime(*max(stamps)):%Y-%m-%d %H:%M}"
                except Exception:  # noqa: BLE001
                    pass
            mtime = dt.datetime.fromtimestamp(path.stat().st_mtime)
            print(f"    {mtime:%Y-%m-%d %H:%M}  {path.stat().st_size:>8,}B  {path.name}{built}")


def check_logs() -> None:
    section("6. 同步日志尾部（最关键：失败的真实原因在这里）")
    log_path = ROOT / "data" / "logs" / "account_monitor.log"
    if not log_path.exists():
        print(f"  ✗ {log_path} 不存在")
        print("    → 说明还在跑旧代码（日志落盘是新加的），或 Streamlit 从未启动过")
        print("    → 先 git pull 并【重启 Streamlit】，再点一次同步，然后重跑本脚本")
        return
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    print(f"  {log_path}  共 {len(lines)} 行")
    keys = ("[pos-dl]", "[pos-xl]", "[chrome]", "[login]", "[sync]", "[pos]",
            "ERROR", "WARNING")
    hits = [ln for ln in lines if any(k in ln for k in keys)]
    print(f"  与同步相关的行 {len(hits)} 条，显示最后 40 条：\n")
    for line in hits[-40:]:
        print(f"    {line}")


def main() -> int:
    print(f"ENERGREX 同步诊断  ·  {dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"仓库: {ROOT}")
    for step in (check_clones, check_code_version, check_env, check_chrome,
                 check_db, check_downloads, check_logs):
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - 一节失败不该中断整份报告
            print(f"\n  !! 本节执行出错: {type(exc).__name__}: {exc}")
    print("\n" + "=" * 62)
    print("把以上完整输出贴回对话即可（不含任何密码）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
