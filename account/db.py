"""SQLite database boundary for the account monitor.

This module owns filesystem paths, connection creation, and idempotent table
initialization. Higher-level account, option, and UI modules should import
`db()` instead of opening SQLite connections directly.
"""

from __future__ import annotations

import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "energrex.db"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS account_balance (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id       TEXT NOT NULL,
        sync_time        TEXT NOT NULL,
        total_equity     REAL,
        cash_balance     REAL,
        margin_used      REAL,
        margin_available REAL,
        margin_usage_pct REAL,
        day_pnl          REAL
    );
    CREATE TABLE IF NOT EXISTS positions (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id         TEXT NOT NULL,
        sync_time          TEXT NOT NULL,
        symbol             TEXT,
        position_type      TEXT,
        quantity           REAL,
        cost_basis         REAL,
        market_value       REAL,
        unrealized_pnl     REAL,
        unrealized_pnl_pct REAL,
        description        TEXT
    );
    CREATE TABLE IF NOT EXISTS transactions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id      TEXT NOT NULL,
        trade_date      TEXT,
        settlement_date TEXT,
        type            TEXT,
        symbol          TEXT,
        description     TEXT,
        quantity        REAL,
        price           REAL,
        amount          REAL,
        UNIQUE(account_id, trade_date, type, symbol, amount)
    );
    CREATE TABLE IF NOT EXISTS options_positions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id    TEXT NOT NULL,
        symbol        TEXT NOT NULL,
        direction     TEXT,
        strike        REAL,
        expiry        TEXT,
        quantity      INTEGER,
        unit_cost     REAL,
        current_price REAL,
        market_value  REAL,
        day_pnl       REAL,
        total_pnl     REAL,
        last_updated  TEXT,
        UNIQUE(account_id, symbol)
    );
    CREATE TABLE IF NOT EXISTS option_realized_trades (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id     TEXT NOT NULL,
        underlying     TEXT,
        symbol         TEXT NOT NULL,
        strategy_type  TEXT,
        lot_direction  TEXT,
        open_date      TEXT,
        close_date     TEXT,
        holding_days   INTEGER,
        quantity       REAL,
        open_cash      REAL,
        close_cash     REAL,
        realized_pnl   REAL,
        return_on_risk REAL,
        win_loss       TEXT,
        option_type    TEXT,
        expiry         TEXT,
        strike         REAL,
        created_at     TEXT
    );
    CREATE TABLE IF NOT EXISTS iv_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        timestamp  TEXT NOT NULL,
        symbol     TEXT NOT NULL,
        underlying TEXT,
        iv         REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS daily_nav (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        date       TEXT NOT NULL,
        nav        REAL NOT NULL,
        UNIQUE(account_id, date)
    );
    CREATE TABLE IF NOT EXISTS qqq_daily_price (
        date        TEXT PRIMARY KEY,
        close_price REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS pltr_iv_history (
        date TEXT PRIMARY KEY,
        iv   REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS portfolio_greeks_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id  TEXT NOT NULL,
        timestamp   TEXT NOT NULL,
        total_delta REAL, total_gamma REAL,
        total_theta REAL, total_vega  REAL,
        n_contracts INTEGER
    );
    """)
    conn.commit()

    for column in ["realized_pnl REAL", "iv REAL",
                   "delta REAL", "gamma REAL", "theta REAL", "vega REAL"]:
        try:
            conn.execute(f"ALTER TABLE options_positions ADD COLUMN {column}")
        except Exception:
            pass

    for column in ["current_price REAL", "unit_cost REAL"]:
        try:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {column}")
        except Exception:
            pass

    conn.commit()
    conn.close()
