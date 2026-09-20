#!/usr/bin/env python3
"""Pull configuration out of another clone's database into this one.

Each checkout of this repository carries its own `data/energrex.db`, so work
done in one copy is invisible to the other. That is how ten configured accounts
ended up stranded: they were created in `~/ai_valuation`, while the app now
serves from a different clone whose database still holds only the two seeded
accounts.

    python scripts/extract_from_other_db.py "C:/Users/evolx/ai_valuation"
    python scripts/extract_from_other_db.py "C:/..." --import-accounts

Read-only unless an --import flag is passed. Accounts are matched by id: an id
this database already has is left alone, so importing twice changes nothing and
a label edited here is never overwritten by the old copy.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Table -> the column that says when each row is about, used to compare the two
# copies by period rather than by row count alone. Two databases holding 300
# rows each are a very different problem depending on whether they cover the
# same dates or different ones.
_SURVEY_TABLES: dict[str, str | None] = {
    "accounts": None,
    "account_balance": "sync_time",
    "positions": "sync_time",
    "options_positions": "last_updated",
    "transactions": "trade_date",
    "daily_nav": "date",
    "option_realized_trades": "close_date",
    "daily_briefing": "date",
    "discipline_signals": "first_seen_date",
}

# What each table is, and what moving it would actually buy. Written out because
# "transactions: 1,240 rows" does not tell you whether it is worth the risk.
_TABLE_NOTES = {
    "accounts": "账户名册。搬过来才有那 10 个账户位",
    "account_balance": "每次同步的净值/现金快照。净值曲线的原料",
    "positions": "股票持仓历史快照",
    "options_positions": "期权当前持仓（每次同步全量替换，只有当下这一份）",
    "transactions": "成交流水。FIFO 配对和已实现盈亏的唯一来源，缺了算不准胜率",
    "daily_nav": "每日净值点。收益曲线直接读这张表，缺口补不回来",
    "option_realized_trades": "已平仓期权的配对结果。Kelly 统计和绩效的基础",
    "daily_briefing": "每日简报快照。过期数据，重新生成即可，不值得搬",
    "discipline_signals": "纪律信号记录。门⑤的响应率统计靠它",
}


def _resolve_db(target: str) -> pathlib.Path:
    """Accept either a clone directory or a direct path to the .db file."""
    path = pathlib.Path(target).expanduser()
    if path.is_dir():
        path = path / "data" / "energrex.db"
    return path


def _open(path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, table: str) -> int | None:
    if not _table_exists(conn, table):
        return None
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _span(conn: sqlite3.Connection, table: str, column: str | None) -> str:
    """Earliest..latest value of the table's date column, for period comparison."""
    if not column or not _table_exists(conn, table):
        return ""
    try:
        row = conn.execute(
            f"SELECT MIN({column}), MAX({column}) FROM {table} "
            f"WHERE {column} IS NOT NULL AND {column} != ''"
        ).fetchone()
    except sqlite3.Error:
        return ""
    if not row or not row[0]:
        return ""
    return f"{str(row[0])[:10]}~{str(row[1])[:10]}"


def _verdict(src_n: int | None, dst_n: int | None, table: str,
             src_span: str, dst_span: str) -> str:
    """Whether this table is worth moving, stated plainly.

    Row counts alone cannot answer it: 300 rows in each copy means something
    different depending on whether the two cover the same dates or different
    ones. Overlap needs a human; disjoint history is usually just missing data.
    """
    if table == "daily_briefing":
        return "✗ 不必搬（过期快照，重新生成即可）"
    if table == "accounts":
        # 没有时间列，而且导入按 id 匹配、已存在的一律跳过，不存在重叠风险。
        if not src_n:
            return "— 源库没有账户"
        extra = src_n - (dst_n or 0)
        return (f"✓ 本脚本 --import-accounts 可直接导（源库多 {extra} 个）"
                if extra > 0 else "— 本库已不少于源库，无需导入")
    if not src_n:
        return "— 源库没有数据"
    if not dst_n:
        return f"✓ 值得搬（本库为空，源库有 {src_n} 行）"
    if src_span and dst_span and src_span.split("~")[1] < dst_span.split("~")[0]:
        return "✓ 值得搬（源库是更早的历史，与本库不重叠）"
    if src_span and dst_span and src_span.split("~")[0] > dst_span.split("~")[1]:
        return "⚠ 源库更新于本库——先确认哪边才是最新的"
    return "⚠ 两边都有且时间重叠，需人工判断（不要盲目合并）"


def compare(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    """Per-table: how much is on each side, covering what period, worth moving?"""
    print(f"\n{'表名':<24}{'源库':>8}{'本库':>8}   {'源库时间跨度':<24}判断")
    print("─" * 110)
    for table, date_col in _SURVEY_TABLES.items():
        src_n, dst_n = _count(source, table), _count(target, table)
        src_span = _span(source, table, date_col)
        dst_span = _span(target, table, date_col)
        src_txt = "无表" if src_n is None else f"{src_n:,}"
        dst_txt = "无表" if dst_n is None else f"{dst_n:,}"
        print(f"{table:<24}{src_txt:>8}{dst_txt:>8}   {src_span or '—':<24}"
              f"{_verdict(src_n, dst_n, table, src_span, dst_span)}")
    print("\n各表是什么、搬了有什么用：")
    for table, note in _TABLE_NOTES.items():
        print(f"  {table:<24} {note}")


def list_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not _table_exists(conn, "accounts"):
        return []
    return conn.execute(
        "SELECT id, label, created_at, IFNULL(is_active,1) AS is_active "
        "FROM accounts"
    ).fetchall()


def import_accounts(source: sqlite3.Connection, dry_run: bool) -> int:
    """Copy accounts this database does not already have. Returns count added."""
    # 必须走 accounts.list_accounts() 而不是只 init_db()：init_db 建的
    # accounts 表没有 is_active 列，那一列是 accounts._ensure_table() 事后
    # 用 ALTER TABLE 补的。只调 init_db 会在插入时报
    # "table accounts has no column named is_active"。顺带它也会补种子数据，
    # 这样下面的"已存在就跳过"比较的是真实状态。
    from account.accounts import list_accounts as _ensure_schema_and_seed
    from account.db import db, init_db

    init_db()
    _ensure_schema_and_seed()

    incoming = list_accounts(source)
    if not incoming:
        print("  源库里没有 accounts 表或没有记录，无可导入")
        return 0

    target = db()
    try:
        existing = {r[0] for r in target.execute("SELECT id FROM accounts").fetchall()}
        added = 0
        for row in incoming:
            if row["id"] in existing:
                print(f"    跳过 {row['id']:<12} {row['label']}（本库已存在，不覆盖）")
                continue
            print(f"    {'将导入' if dry_run else '已导入'} "
                  f"{row['id']:<12} {row['label']}"
                  + ("" if row["is_active"] else "（已归档状态）"))
            if not dry_run:
                target.execute(
                    "INSERT INTO accounts (id, label, created_at, is_active) "
                    "VALUES (?,?,?,?)",
                    (row["id"], row["label"],
                     row["created_at"] or dt.datetime.now().isoformat(),
                     row["is_active"]),
                )
            added += 1
        if not dry_run:
            target.commit()
    finally:
        target.close()
    return added


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="另一份克隆的目录，或直接给 .db 文件路径")
    parser.add_argument("--import-accounts", action="store_true",
                        help="把源库里本库没有的账户导进来（按 id 匹配，不覆盖同 id）")
    args = parser.parse_args()

    source_path = _resolve_db(args.source)
    if not source_path.exists():
        print(f"源数据库不存在: {source_path}")
        return 1

    print(f"源库  : {source_path}")
    print(f"本库  : {ROOT / 'data' / 'energrex.db'}")

    source = _open(source_path)
    try:
        from account.db import DB_PATH, init_db
        init_db()
        target_ro = _open(pathlib.Path(DB_PATH))
        try:
            compare(source, target_ro)
        finally:
            target_ro.close()

        accounts = list_accounts(source)
        print(f"\n── 源库里的账户（{len(accounts)} 个）──")
        for row in accounts:
            state = "" if row["is_active"] else "  [已归档]"
            print(f"  {row['id']:<12} {row['label']}{state}")
        if not accounts:
            print("  （无）")

        print("\n── 导入 ──")
        added = import_accounts(source, dry_run=not args.import_accounts)
        if not args.import_accounts:
            print(f"\n  以上为预演，未写入。确认无误后加 --import-accounts 执行。")
        else:
            print(f"\n  完成：新增 {added} 个账户。重启 Streamlit 后出现在账户选择器里。")
    finally:
        source.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
