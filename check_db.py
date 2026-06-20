import sqlite3, pathlib
db = pathlib.Path("data/energrex.db")
if not db.exists():
    print("DB not found - will be created when Streamlit runs first")
else:
    con = sqlite3.connect(db)
    tables = [t[0] for t in con.execute("SELECT name FROM sqlite_master WHERE type=?", ("table",)).fetchall()]
    print("Tables:", tables)
    for t in tables:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n} rows")
        if t == "transactions" and n > 0:
            rows = con.execute("SELECT trade_date, type, symbol, quantity, price, amount FROM transactions LIMIT 5").fetchall()
            for r in rows:
                print("   ", r)
    con.close()
