"""账户注册表：编号是补零显示，可编辑名字，内部 id 永远是 account_N。

2026-09-10 新增：之前只有硬编码的两个账户（account_1/account_2）。id 沿用
不变，是因为 positions/transactions/options_positions/discipline_signals
等所有表的 account_id 列里已经有大量历史数据用的就是这个字符串——换掉 id
格式等于要迁移全部历史行。"编号"（001/002/003...）只是从 id 里的数字派生
出来给人看的，新增账户不影响任何已有数据。

2026-09-11 新增：archive/unarchive（软删除）。"减少账户"不做真删除——
所有表的 account_id 只是个普通字符串，没有外键约束，真删了 accounts 表里
这一行不会级联删掉别处的历史成交/持仓/纪律记录，只会让这些行变成孤儿数据
（还在库里，但再也没有账户名可以对应）。is_active=0 只是从选择器里隐藏，
历史数据原样保留，需要的话随时 unarchive 找回来。
"""
from __future__ import annotations

import datetime
import pathlib
import re

from account.db import db as _db

_ID_RE = re.compile(r"^account_(\d+)$")


def _ensure_table(conn) -> None:
    # account_monitor.py's module top assigns ACCT_CFG = list_accounts() before
    # its own init_db() call runs later in the same file (see account_db.init_db
    # for the canonical CREATE TABLE) -- this module needs to be able to create
    # its own table on first touch rather than assume init_db() ran first.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id         TEXT PRIMARY KEY,
            label      TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_active  INTEGER NOT NULL DEFAULT 1
        )
    """)
    for column in ["is_active INTEGER NOT NULL DEFAULT 1"]:
        try:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN {column}")
        except Exception:
            pass


def _seed_if_empty(conn) -> None:
    _ensure_table(conn)
    row = conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()
    if row["n"]:
        return
    now = datetime.datetime.now().isoformat()
    for acct_id, label in (("account_1", "账户一"), ("account_2", "账户二")):
        conn.execute(
            "INSERT INTO accounts (id, label, created_at, is_active) VALUES (?,?,?,1)",
            (acct_id, label, now),
        )
    conn.commit()


def _numbered(rows) -> list[dict]:
    out = []
    for r in rows:
        m = _ID_RE.match(r["id"])
        n = int(m.group(1)) if m else 0
        out.append({"id": r["id"], "number": f"{n:03d}", "label": r["label"], "_n": n})
    out.sort(key=lambda x: x["_n"])
    for o in out:
        del o["_n"]
    return out


def list_accounts(include_archived: bool = False) -> list[dict]:
    """按编号顺序返回 [{id, number, label}, ...]。默认只返回未归档的账户。"""
    conn = _db()
    try:
        _seed_if_empty(conn)
        if include_archived:
            rows = conn.execute("SELECT id, label FROM accounts").fetchall()
        else:
            rows = conn.execute(
                "SELECT id, label FROM accounts WHERE is_active=1").fetchall()
    finally:
        conn.close()
    return _numbered(rows)


def add_account(label: str | None = None) -> dict:
    """分配下一个可用编号并插入新账户，返回 {id, number, label}。"""
    conn = _db()
    try:
        _seed_if_empty(conn)
        rows = conn.execute("SELECT id FROM accounts").fetchall()
        max_n = 0
        for r in rows:
            m = _ID_RE.match(r["id"])
            if m:
                max_n = max(max_n, int(m.group(1)))
        n = max_n + 1
        acct_id = f"account_{n}"
        acct_label = (label or "").strip() or f"账户{n}"
        conn.execute(
            "INSERT INTO accounts (id, label, created_at, is_active) VALUES (?,?,?,1)",
            (acct_id, acct_label, datetime.datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": acct_id, "number": f"{n:03d}", "label": acct_label}


def rename_account(acct_id: str, new_label: str) -> None:
    new_label = new_label.strip()
    if not new_label:
        raise ValueError("账户名不能为空")
    conn = _db()
    try:
        # _seed_if_empty，不是 _ensure_table：这三个函数都是"改一行"，如果
        # 表还没种子数据就先 UPDATE，会在一张空表上无声地改0行——种子数据
        # 之后才插入进来，会带着默认值把刚才这次改动悄悄盖掉。
        _seed_if_empty(conn)
        conn.execute("UPDATE accounts SET label=? WHERE id=?", (new_label, acct_id))
        conn.commit()
    finally:
        conn.close()


def archive_account(acct_id: str) -> None:
    """从选择器里隐藏（软删除）。历史数据一个字节不动。"""
    conn = _db()
    try:
        _seed_if_empty(conn)
        conn.execute("UPDATE accounts SET is_active=0 WHERE id=?", (acct_id,))
        conn.commit()
    finally:
        conn.close()


def unarchive_account(acct_id: str) -> None:
    """把归档的账户找回来。"""
    conn = _db()
    try:
        _seed_if_empty(conn)
        conn.execute("UPDATE accounts SET is_active=1 WHERE id=?", (acct_id,))
        conn.commit()
    finally:
        conn.close()


def account_download_dir(acct_id: str) -> pathlib.Path:
    """这个账户专属的下载子文件夹（~/Downloads/energrex_<编号>/），不存在就建。"""
    m = _ID_RE.match(acct_id)
    n = int(m.group(1)) if m else 0
    d = pathlib.Path.home() / "Downloads" / f"energrex_{n:03d}"
    d.mkdir(parents=True, exist_ok=True)
    return d
