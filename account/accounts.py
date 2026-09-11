"""账户注册表：编号是补零显示，可编辑名字，内部 id 永远是 account_N。

2026-09-10 新增：之前只有硬编码的两个账户（account_1/account_2）。id 沿用
不变，是因为 positions/transactions/options_positions/discipline_signals
等所有表的 account_id 列里已经有大量历史数据用的就是这个字符串——换掉 id
格式等于要迁移全部历史行。"编号"（001/002/003...）只是从 id 里的数字派生
出来给人看的，新增账户不影响任何已有数据。
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
            created_at TEXT NOT NULL
        )
    """)


def _seed_if_empty(conn) -> None:
    _ensure_table(conn)
    row = conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()
    if row["n"]:
        return
    now = datetime.datetime.now().isoformat()
    for acct_id, label in (("account_1", "账户一"), ("account_2", "账户二")):
        conn.execute(
            "INSERT INTO accounts (id, label, created_at) VALUES (?,?,?)",
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


def list_accounts() -> list[dict]:
    """按编号顺序返回 [{id, number, label}, ...]。"""
    conn = _db()
    try:
        _seed_if_empty(conn)
        rows = conn.execute("SELECT id, label FROM accounts").fetchall()
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
            "INSERT INTO accounts (id, label, created_at) VALUES (?,?,?)",
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
        _ensure_table(conn)
        conn.execute("UPDATE accounts SET label=? WHERE id=?", (new_label, acct_id))
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
