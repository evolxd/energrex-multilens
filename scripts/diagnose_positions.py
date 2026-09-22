#!/usr/bin/env python3
"""把 DB 里的持仓行原样摊开，逐行体检——不联网，几秒出结果。

为什么要有这个：2026-09-21 的截图上，Beta-Delta 是**净多头 +356%**，同一块
面板里压力测试说大盘跌 20% 账户**赚 $98,640（+182% 净值）**。这两个数出自
同一个函数、同一批持仓，不可能同时对。旁边还有一个 $103,600 的权利金，而
账户净值一共 $54,158。

这类事故每一步代码都跑通了，没有异常、没有报错——错的是喂进去的那批行。
页面上看不出来，只能把行摊开看。

用法：

    python scripts/diagnose_positions.py                 ← 所有账户
    python scripts/diagnose_positions.py account_1       ← 指定账户

只读，不改任何东西。股票表的重复行用 scripts/check_dupes.py 查（那张表是
只增不改的追加表，重复的判定方式不一样）。

退出码 0 = 没查出 error 级的问题；1 = 有。
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from account.options import parse_occ, signed_quantity  # noqa: E402
from account.position_audit import (  # noqa: E402
    audit_option_rows,
    sign_flip_impact,
)

DB = ROOT / "data" / "energrex.db"

_SEV_MARK = {"error": "✗", "warn": "⚠", "info": "·"}


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def _equity(conn: sqlite3.Connection, acct: str) -> tuple[float, str]:
    row = conn.execute(
        "SELECT total_equity, sync_time FROM account_balance "
        "WHERE account_id=? ORDER BY sync_time DESC LIMIT 1", (acct,)).fetchone()
    if not row:
        return 0.0, ""
    return float(row["total_equity"] or 0), str(row["sync_time"] or "")


def _option_rows(conn: sqlite3.Connection, acct: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT symbol, quantity, direction, strike, expiry, unit_cost, "
        "current_price, market_value, last_updated "
        "FROM options_positions WHERE account_id=? ORDER BY symbol", (acct,))]


def _stock_rows(conn: sqlite3.Connection, acct: str) -> list[dict]:
    # 跟 account.repository.load_positions 同口径：每只票只取最新一次同步。
    return [dict(r) for r in conn.execute(
        "SELECT symbol, quantity, market_value, sync_time FROM positions p1 "
        "WHERE p1.account_id=? AND p1.position_type='stock' "
        "AND p1.sync_time = (SELECT MAX(p2.sync_time) FROM positions p2 "
        "WHERE p2.account_id=p1.account_id AND p2.symbol=p1.symbol) "
        "ORDER BY symbol", (acct,))]


def _print_options(rows: list[dict]) -> None:
    if not rows:
        print("  （没有期权持仓）")
        return
    print(f"  {'代号':<24}{'库存张数':>9}{'direction':>11}{'归一后':>8}"
          f"{'单位成本':>10}{'现价':>9}{'市值':>12}")
    print("  " + "─" * 84)
    for r in rows:
        qty = signed_quantity(r.get("quantity"), r.get("direction"))
        raw = r.get("quantity")
        mark = " ←翻" if (raw or 0) > 0 and qty < 0 else ""
        uc = r.get("unit_cost")
        cp = r.get("current_price")
        mv = r.get("market_value")
        uc_s = f"${uc:,.2f}" if uc is not None else "—"
        cp_s = f"${cp:,.2f}" if cp is not None else "—"
        mv_s = f"${mv:,.0f}" if mv is not None else "—"
        raw_s = f"{raw:g}" if raw is not None else "—"
        print(f"  {str(r['symbol']):<24}{raw_s:>9}"
              f"{str(r.get('direction') or '—'):>11}{qty:>+8.0f}"
              f"{uc_s:>10}{cp_s:>9}{mv_s:>12}{mark}")


def _print_stocks(rows: list[dict], equity: float) -> None:
    if not rows:
        print("  （没有股票持仓）")
        return
    total = sum(float(r.get("market_value") or 0) for r in rows)
    print(f"  {len(rows)} 只股票，市值合计 ${total:,.0f}"
          + (f"（净值的 {total / equity * 100:.0f}%）" if equity > 0 else ""))
    syncs = {str(r.get("sync_time") or "") for r in rows}
    if len(syncs) > 1:
        print(f"  ⚠ 这些行来自 {len(syncs)} 个不同的同步时刻——正常情况下"
              f"每只票取的都是最新那次，出现多个时刻说明有票没同步上：")
        for s in sorted(syncs):
            n = sum(1 for r in rows if str(r.get("sync_time") or "") == s)
            print(f"      {s}  {n} 只")


def diagnose(acct: str) -> int:
    conn = _conn()
    equity, sync_time = _equity(conn, acct)
    opts = _option_rows(conn, acct)
    stks = _stock_rows(conn, acct)
    conn.close()

    print(f"\n{'═' * 88}\n账户 {acct}")
    print(f"净值 ${equity:,.0f}" + (f"   余额同步于 {sync_time}" if sync_time else ""))
    if equity <= 0:
        print("⚠ 没有净值记录，所有跟净值比的检查会跳过——同步一次余额再跑。")

    print(f"\n期权持仓（{len(opts)} 行）")
    _print_options(opts)

    print(f"\n股票持仓")
    _print_stocks(stks, equity)

    impact = sign_flip_impact(opts)
    print(f"\n符号归一的影响")
    if impact["rows"] == 0:
        print("  没有一行被翻。")
        print("  → 如果 BD 和压力测试仍然互相矛盾（净多头 + 崩盘赚钱），"
              "原因不在张数符号上，别把这一项当成结论。")
    else:
        print(f"  {impact['rows']} 行 / {impact['contracts']:.0f} 张 "
              f"/ 行权名义 ${impact['strike_notional']:,.0f} 的方向被读反过。")
        print(f"  {', '.join(impact['symbols'])}")
        print("  → 这些行以前在风险快照、组合 Greeks、对冲宽度检查里都算作买入。"
              "卖出的 put 被当成买入的 put，崩盘就会显示成赚钱。")

    findings = audit_option_rows(opts, equity=equity)
    n_err = sum(1 for f in findings if f.severity == "error")
    print(f"\n逐行体检（{len(findings)} 条，其中 error {n_err} 条）")
    if not findings:
        print("  没查出问题。")
    for f in findings:
        print(f"  {_SEV_MARK[f.severity]} [{f.code}] {f.symbol}")
        print(f"      {f.detail}")

    return 1 if n_err else 0


def main(argv: list[str]) -> int:
    if not DB.exists():
        print(f"找不到数据库：{DB}")
        return 2
    if len(argv) > 1:
        accts = argv[1:]
    else:
        conn = _conn()
        accts = [r[0] for r in conn.execute(
            "SELECT DISTINCT account_id FROM options_positions "
            "UNION SELECT DISTINCT account_id FROM account_balance")]
        conn.close()
    if not accts:
        print("库里没有任何账户。")
        return 2

    worst = 0
    for acct in accts:
        worst = max(worst, diagnose(acct))
    print()
    return worst


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
