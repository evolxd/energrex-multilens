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

# Tables worth reporting on when deciding what else is stranded in the old copy.
_SURVEY_TABLES = (
    "accounts", "account_balance", "positions", "options_positions",
    "transactions", "daily_nav", "option_realized_trades", "daily_briefing",
    "discipline_signals",
)


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


def survey(conn: sqlite3.Connection, label: str) -> None:
    print(f"\n── {label} ──")
    for table in _SURVEY_TABLES:
        if not _table_exists(conn, table):
            print(f"  {table:<24} （无此表）")
            continue
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<24} {count:>7} 行")


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
        survey(source, "源库内容")

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
