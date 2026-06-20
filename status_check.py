import pandas as pd, pathlib, sqlite3, datetime, os

ROOT = pathlib.Path(".")

# 1. results_validated.csv
csv = ROOT / "data" / "results_validated.csv"
if csv.exists():
    df = pd.read_csv(csv)
    print(f"results_validated.csv: {len(df)} tickers, {len(df.columns)} cols")
    if "last_refreshed" in df.columns:
        print(f"  last_refreshed: {df['last_refreshed'].dropna().iloc[0] if df['last_refreshed'].notna().any() else 'NONE'}")
    if "raw_pe" in df.columns:
        has_yf = df["raw_pe"].astype(str).str.contains("[yf]", regex=False).sum()
        print(f"  yfinance data (raw_pe): {has_yf}/{len(df)} tickers")
    if "final_score" in df.columns:
        scored = df["final_score"].notna().sum()
        print(f"  scored tickers: {scored}/{len(df)}")
        print(f"  score range: {df['final_score'].min():.1f} ~ {df['final_score'].max():.1f}")
else:
    print("results_validated.csv: NOT FOUND")

print()

# 2. DB
db = ROOT / "data" / "energrex.db"
if db.exists():
    con = sqlite3.connect(db)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print(f"energrex.db tables: {tables}")
    for t in tables:
        if t == "sqlite_sequence":
            continue
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n} rows")
    # transactions date range
    r = con.execute("SELECT MIN(trade_date), MAX(trade_date) FROM transactions").fetchone()
    print(f"  transactions range: {r[0]} ~ {r[1]}")
    con.close()
else:
    print("energrex.db: NOT FOUND")

print()

# 3. Key files
key_files = [
    "home.py", "app.py", "options_module.py", "account_monitor.py",
    "refresh_scores.py", "import_csv.py", "seed_options.py", "migrate_db.py",
    "start_chrome.bat",
    "scoring/quant_engine.py", "scoring/quant_data.py", "scoring/yfinance_fetcher.py",
    "pages/1_📊_AI_估值评分.py", "pages/2_📈_期权分析.py", "pages/3_🏦_账户监控.py",
    "backend/main.py", "backend/scheduler.py",
    ".env", ".env.example", "requirements.txt",
]
print("Key files:")
for f in key_files:
    p = ROOT / f
    if p.exists():
        size = p.stat().st_size
        mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
        print(f"  ✓ {f:<50} {size:>7,} B  {mtime}")
    else:
        print(f"  ✗ {f}")
