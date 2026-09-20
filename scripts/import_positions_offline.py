#!/usr/bin/env python3
"""Load a Firstrade positions export into the database without any automation.

The CDP download chain has a lot of ways to fail silently -- Chrome not in
debugging mode, an expired session, a download landing in the wrong folder, a
changed button selector -- and every one of them leaves the account data
frozen. None of that is needed to read a spreadsheet. Save the export by hand
from whatever browser you are already logged into, point this at it, done.

    python scripts/import_positions_offline.py                        # newest file found
    python scripts/import_positions_offline.py path/to/positions.xlsx
    python scripts/import_positions_offline.py --equity 49374 --cash 4979
    python scripts/import_positions_offline.py old.xlsx --date 2026-09-14

Positions come from the file. Equity and cash do not -- the export contains
neither -- so `--equity/--cash` are how the NAV curve gets its point for the
day. Read both off the Firstrade balance page; it takes a few seconds and it is
the only way to keep the curve continuous while the automated sync is down.

`--date` writes the NAV point under a past date, for backfilling a day whose
export you still have. Positions are always written as the current snapshot.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from account.positions_xlsx import (  # noqa: E402
    parse_options_xlsx,
    parse_stocks_xlsx,
    stock_snapshot_rows,
)


def newest_export() -> pathlib.Path | None:
    """Most recently modified positions xlsx across the usual download spots."""
    home = pathlib.Path.home()
    roots = [home / "Downloads", home / "Desktop"]
    downloads = home / "Downloads"
    if downloads.exists():
        roots.extend(p for p in downloads.glob("energrex_*") if p.is_dir())

    candidates: list[pathlib.Path] = []
    for directory in roots:
        if directory.exists():
            candidates.extend(
                p for p in directory.glob("*.xlsx") if not p.name.startswith("~$")
            )
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def current_stock_symbols(conn, account_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT symbol FROM positions p1
        WHERE p1.account_id = ?
          AND p1.position_type = 'stock'
          AND IFNULL(p1.quantity, 0) != 0
          AND p1.sync_time = (
              SELECT MAX(p2.sync_time) FROM positions p2
              WHERE p2.symbol = p1.symbol AND p2.account_id = p1.account_id
          )
        """,
        (account_id,),
    ).fetchall()
    return [r[0] for r in rows]


def write_options(conn, account_id: str, rows: list[dict]) -> None:
    """Full replace, matching what the XLSX branch of the live sync does."""
    now = dt.datetime.now().isoformat()
    conn.execute("DELETE FROM options_positions WHERE account_id=?", (account_id,))
    for row in rows:
        conn.execute(
            """
            INSERT INTO options_positions
              (account_id, symbol, direction, strike, expiry, quantity,
               unit_cost, current_price, market_value, day_pnl, total_pnl,
               last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (account_id, row["symbol"], row["direction"], row["strike"],
             row["expiry"], row["quantity"], row["unit_cost"],
             row["current_price"], row["market_value"], row["day_pnl"],
             row["total_pnl"], now),
        )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("xlsx", nargs="?", help="导出文件路径（省略则自动找最新的）")
    parser.add_argument("--account", default="account_1")
    parser.add_argument("--equity", type=float,
                        help="账户总净值（xlsx 里没有，需从 Firstrade 余额页抄）")
    parser.add_argument("--cash", type=float, help="现金余额（同上）")
    parser.add_argument("--date", help="净值记到这一天（YYYY-MM-DD），用于补历史")
    parser.add_argument("--dry-run", action="store_true", help="只解析不写库")
    parser.add_argument("--dedupe", action="store_true",
                        help="清理库里同 (symbol, sync_time) 的重复行（会让敞口/BD 重复计算）")
    args = parser.parse_args()

    if args.dedupe:
        from account.db import init_db as _init
        from account.repository import remove_duplicate_positions
        _init()
        removed = remove_duplicate_positions(args.account)
        print(f"清理重复持仓行：删除 {removed} 行")
        if not args.xlsx:
            return 0

    path = pathlib.Path(args.xlsx).expanduser() if args.xlsx else newest_export()
    if path is None:
        print("找不到任何 xlsx。手动从 Firstrade 持仓页导出后，把路径作为参数传进来。")
        return 1
    if not path.exists():
        print(f"文件不存在: {path}")
        return 1

    print(f"读取 {path}")
    stocks = parse_stocks_xlsx(path)
    options = parse_options_xlsx(path)
    stock_mv = sum(r["market_value"] or 0 for r in stocks)
    option_mv = sum(r["market_value"] or 0 for r in options)
    print(f"  股票 {len(stocks):>3} 只   市值 ${stock_mv:>12,.2f}")
    print(f"  期权 {len(options):>3} 笔   市值 ${option_mv:>12,.2f}")
    print(f"  持仓合计        ${stock_mv + option_mv:>12,.2f}")

    if not stocks and not options:
        print("没有解析到任何持仓，不写库。")
        return 1

    if args.equity:
        implied_cash = args.equity - (stock_mv + option_mv)
        print(f"  净值 ${args.equity:,.2f} → 倒推现金 ${implied_cash:,.2f}")
        if args.cash is not None and abs(implied_cash - args.cash) > max(50.0, args.equity * 0.01):
            # 对不上通常说明净值/现金抄错了，或者这个 xlsx 不是同一时点导出的
            print(f"  ⚠️ 与填写的现金 ${args.cash:,.2f} 相差 "
                  f"${abs(implied_cash - args.cash):,.2f}，请核对是否同一时点的数据")

    if args.dry_run:
        print("\n--dry-run：未写入数据库。")
        return 0

    from account.db import db, init_db
    from account.repository import save_balance, save_positions

    # 表可能还不存在：这个脚本的价值就在于不依赖 Streamlit 那条链路，所以
    # 不能假设 account_monitor 已经跑过并建好了表。init_db 是幂等的。
    init_db()

    conn = db()
    try:
        previous = current_stock_symbols(conn, args.account)
    finally:
        conn.close()

    if stocks:
        rows = stock_snapshot_rows(stocks, previous)
        closed = [r["symbol"] for r in rows if r["quantity"] == 0]
        save_positions(args.account, rows)
        print(f"\n✓ 股票写入 {len(stocks)} 只"
              + (f"，标记清仓 {len(closed)} 只: {closed}" if closed else ""))

    if options:
        conn = db()
        try:
            write_options(conn, args.account, options)
        finally:
            conn.close()
        print(f"✓ 期权写入 {len(options)} 笔（全量替换）")

    if args.equity:
        cash = args.cash if args.cash is not None else args.equity - (stock_mv + option_mv)
        save_balance(args.account, {"total_equity": args.equity, "cash_balance": cash})
        print(f"✓ 余额写入：净值 ${args.equity:,.2f}  现金 ${cash:,.2f}")

        if args.date:
            # save_balance() 已经按今天记过一次 NAV；指定日期时再补写那一天。
            from account.repository import record_daily_nav
            record_daily_nav(args.account, args.equity, args.date)
            print(f"✓ 净值曲线补点：{args.date} = ${args.equity:,.2f}")
    else:
        print("\n未提供 --equity：持仓已更新，但净值曲线今天仍然没有点。")
        print("  想让曲线连上，从 Firstrade 余额页抄两个数再跑一次：")
        print(f"  python scripts/import_positions_offline.py \"{path}\" --equity <净值> --cash <现金>")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
