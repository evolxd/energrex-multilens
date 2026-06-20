"""Database repository helpers for account monitor state."""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

import pandas as pd

from account.db import db

ET = ZoneInfo("America/New_York")


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


def save_positions(acct_id: str, rows: list[dict]) -> None:
    if not rows:
        return
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
    conn = db()
    latest = conn.execute(
        "SELECT sync_time FROM positions WHERE account_id=? ORDER BY sync_time DESC LIMIT 1",
        (acct_id,),
    ).fetchone()
    if not latest:
        conn.close()
        return pd.DataFrame()
    df = pd.read_sql_query(
        "SELECT * FROM positions WHERE account_id=? AND sync_time=? ORDER BY market_value DESC",
        conn,
        params=(acct_id, latest[0]),
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
