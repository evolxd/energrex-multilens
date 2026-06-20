import sqlite3, pathlib

con = sqlite3.connect("data/energrex.db")
con.executescript("""
CREATE TABLE IF NOT EXISTS options_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT, strike REAL, expiry TEXT, quantity INTEGER,
    unit_cost REAL, current_price REAL, market_value REAL,
    day_pnl REAL, total_pnl REAL, last_updated TEXT,
    UNIQUE(account_id, symbol)
);
CREATE TABLE IF NOT EXISTS cash_flow (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    date       TEXT NOT NULL,
    type       TEXT NOT NULL,
    amount     REAL NOT NULL,
    note       TEXT,
    source     TEXT DEFAULT 'manual',
    created_at TEXT
);
""")
con.commit()
tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)
con.close()
print("Done.")
