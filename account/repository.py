"""Database repository helpers for account monitor state."""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

import pandas as pd

from account.db import db

ET = ZoneInfo("America/New_York")


def compute_margin_usage_pct(margin_used: float | None, total_equity: float | None) -> float | None:
    """融资余额占账户净值的比例。margin_used 为 None 代表"这次没读到"，不是"是0"——
    这两种情况不能都映射成 0.0，否则图表上"真的没借钱"和"这次没抓到这个字段"会
    长得一模一样，没法区分。真的读到了 0（Firstrade 页面明确显示 $0.00）时正常返回
    0.0。total_equity 缺失或为 0 时同样返回 None（分母不成立，不是"用了0%"）。
    """
    if margin_used is None or not total_equity:
        return None
    return margin_used / total_equity * 100


def record_daily_nav(acct_id: str, nav: float, date: str | None = None) -> None:
    """Store or replace the daily NAV snapshot for an account."""
    if date is None:
        date = _dt.datetime.now(ET).strftime("%Y-%m-%d")
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO daily_nav (account_id, date, nav) VALUES (?,?,?)",
        (acct_id, date, nav),
    )
    conn.commit()
    conn.close()


def cash_reading_looks_like_buying_power(
    cash_balance: float | None,
    margin_available: float | None,
) -> bool:
    """True when the scraped "cash" is really a buying-power figure.

    Firstrade shows Cash BP and Margin BP next to the cash balance, and the
    page-text scraper matches labels by substring -- "Cash BP" contains "Cash".
    When that mismatch happens the two fields come back holding the same
    number, which is the cheapest possible tell.

    Worth catching because the failure is silent and points the wrong way: on
    2026-09-20 the account's Cash BP was $22,160.55 against $10,034.48 of
    actual cash, which would have reported a 41.6% cash ratio against a 20%
    floor -- the constraint that exists to stop you would have shown green.
    """
    if cash_balance is None or margin_available is None:
        return False
    return abs(float(cash_balance) - float(margin_available)) < 0.01


def save_balance(acct_id: str, data: dict) -> None:
    """Persist an account balance snapshot and update daily NAV when present."""
    conn = db()
    conn.execute(
        """
        INSERT INTO account_balance
        (account_id,sync_time,total_equity,cash_balance,
         margin_used,margin_available,margin_usage_pct,day_pnl)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            acct_id,
            _dt.datetime.now(ET).isoformat(),
            data.get("total_equity"),
            data.get("cash_balance"),
            data.get("margin_used"),
            data.get("margin_available"),
            data.get("margin_usage_pct"),
            data.get("day_pnl"),
        ),
    )
    conn.commit()
    conn.close()
    if data.get("total_equity"):
        record_daily_nav(acct_id, data["total_equity"])


def dedupe_rows_by_symbol(rows: list[dict]) -> list[dict]:
    """One row per symbol, last write wins.

    Every reader of `positions` picks "the row with MAX(sync_time) for this
    symbol". That subquery matches *all* rows tied at the maximum, so two rows
    for one symbol written in the same batch are both counted -- the holding is
    doubled in every exposure and Beta-Delta figure downstream.

    The 2026-09-19 fix addressed duplicates spread across different sync_times
    (BD climbed 224% -> 263% -> 320% -> 378% as snapshots accumulated). It
    cannot help with duplicates *inside* one batch, because they share a
    timestamp. Writing them is what has to stop.
    """
    unique: dict[str, dict] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol:
            unique[symbol] = row
    return list(unique.values())


def remove_duplicate_positions(acct_id: str) -> int:
    """Delete rows that share (symbol, sync_time), keeping the first. Returns count.

    For databases that already accumulated duplicates before the write path
    started deduping.
    """
    conn = db()
    cursor = conn.execute(
        """
        DELETE FROM positions WHERE rowid NOT IN (
            SELECT MIN(rowid) FROM positions WHERE account_id = ?
            GROUP BY symbol, sync_time, position_type
        ) AND account_id = ?
        """,
        (acct_id, acct_id),
    )
    removed = cursor.rowcount or 0
    conn.commit()
    conn.close()
    return removed


def save_positions(acct_id: str, rows: list[dict]) -> None:
    if not rows:
        return
    rows = dedupe_rows_by_symbol(rows)
    conn = db()
    sync_time = _dt.datetime.now(ET).isoformat()
    conn.executemany(
        """
        INSERT INTO positions
        (account_id,sync_time,symbol,position_type,quantity,
         cost_basis,market_value,unrealized_pnl,unrealized_pnl_pct,description)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                acct_id,
                sync_time,
                row.get("symbol"),
                row.get("position_type"),
                row.get("quantity"),
                row.get("cost_basis"),
                row.get("market_value"),
                row.get("unrealized_pnl"),
                row.get("unrealized_pnl_pct"),
                row.get("description"),
            )
            for row in rows
        ],
    )
    conn.commit()
    conn.close()


def save_transactions(acct_id: str, rows: list[dict]) -> None:
    conn = db()
    for row in rows:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO transactions
                (account_id,trade_date,settlement_date,type,symbol,
                 description,quantity,price,amount)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    acct_id,
                    row.get("trade_date"),
                    row.get("settlement_date"),
                    row.get("type"),
                    row.get("symbol"),
                    row.get("description"),
                    row.get("quantity"),
                    row.get("price"),
                    row.get("amount"),
                ),
            )
        except Exception:
            pass
    conn.commit()
    conn.close()


def load_balance_history(days: int = 30) -> pd.DataFrame:
    conn = db()
    df = pd.read_sql_query(
        "SELECT * FROM account_balance WHERE sync_time >= datetime('now',?) ORDER BY sync_time",
        conn,
        params=(f"-{days} days",),
    )
    conn.close()
    return df


def load_latest_balance(acct_id: str) -> dict:
    conn = db()
    row = conn.execute(
        "SELECT * FROM account_balance WHERE account_id=? ORDER BY sync_time DESC LIMIT 1",
        (acct_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def load_positions(acct_id: str) -> pd.DataFrame:
    """Each symbol's own latest row -- not "every row sharing the single
    latest sync_time" (2026-08-27 bug fix). _refresh_stock_prices() used to
    stamp each stock with its own datetime.now() inside a per-symbol loop,
    so a global exact-match on sync_time only ever returned whichever stock
    happened to update last; every other holding silently disappeared from
    this and everything downstream of it. That write bug is fixed too, but
    this read is now robust to it regardless -- it never again requires
    every row to share one exact timestamp for a holding to be found.
    """
    conn = db()
    df = pd.read_sql_query(
        """
        SELECT * FROM positions p1
        WHERE p1.account_id = ?
          AND p1.sync_time = (
              SELECT MAX(p2.sync_time) FROM positions p2
              WHERE p2.account_id = p1.account_id AND p2.symbol = p1.symbol
          )
        ORDER BY market_value DESC
        """,
        conn,
        params=(acct_id,),
    )
    conn.close()
    return df


def load_transactions(acct_id: str, since: str = "2026-06-01") -> pd.DataFrame:
    conn = db()
    df = pd.read_sql_query(
        "SELECT * FROM transactions WHERE account_id=? AND trade_date>=? ORDER BY trade_date DESC",
        conn,
        params=(acct_id, since),
    )
    conn.close()
    return df
