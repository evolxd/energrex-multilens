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
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    # WAL：默认的回滚日志模式下，任何一次写事务（同步账户/CSV导入/FIFO重算/
    # 门⑤纪律记录）都会给整个库文件加排他锁，这段时间内别的连接连读都读不了
    # ——这个系统有好几个入口可能同时碰库（账户同步的同时开着账户监控页面
    # 正好自动刷新一次），WAL模式下读不会被写挡住，只有写和写之间才互斥，
    # 大幅缓解这种"随时有人在同步、随时有人在看页面"的场景。PRAGMA 是持久化
    # 在库文件头里的设置，重复执行没有代价（已经是WAL就直接返回）。
    # busy_timeout（15秒，比 sqlite3 默认的5秒宽松）是给WAL之外剩下的那点
    # 写-写竞争一个宽限，不是解决方案本身——已经在上面 connect(timeout=15.0)
    # 里设置过了，这里不用再重复设一遍。
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
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
        created_at     TEXT,
        combo_id       TEXT,
        combo_strategy TEXT
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
    CREATE TABLE IF NOT EXISTS accounts (
        id         TEXT PRIMARY KEY,
        label      TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS discipline_signals (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id      TEXT NOT NULL,
        dimension       TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        first_seen_date TEXT NOT NULL,
        last_seen_date  TEXT NOT NULL,
        detail          TEXT,
        status          TEXT NOT NULL,
        resolved_date   TEXT,
        resolved_via    TEXT,
        response_days   INTEGER,
        event_score     REAL,
        review_tag      TEXT,
        review_note     TEXT,
        UNIQUE(account_id, dimension, symbol, first_seen_date)
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

    # combo_id/combo_strategy 已经在上面 CREATE TABLE 里了，这两行只是给"表已经
    # 存在但是老版本schema"的情况兜底（同样的模式在这个函数里已经在用）。
    for column in ["combo_id TEXT", "combo_strategy TEXT"]:
        try:
            conn.execute(f"ALTER TABLE option_realized_trades ADD COLUMN {column}")
        except Exception:
            pass

    # discipline_signals 的列已经在上面 CREATE TABLE 里了，这几行是同样的
    # 兜底模式，防的是"表已存在但是更老的schema"这种情况（event_score/
    # review_tag/review_note 是 2026-09-10 纪律架构 v2 加的）。
    for column in ["resolved_via TEXT", "response_days INTEGER",
                   "event_score REAL", "review_tag TEXT", "review_note TEXT"]:
        try:
            conn.execute(f"ALTER TABLE discipline_signals ADD COLUMN {column}")
        except Exception:
            pass

    conn.commit()
    conn.close()
