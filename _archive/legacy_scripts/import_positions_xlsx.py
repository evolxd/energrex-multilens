"""
Import Firstrade positions xlsx → positions table.
Picks the newest 91224907-positions*.xlsx from ~/Downloads.
"""
import sqlite3, pathlib, datetime, re
import pandas as pd
import pytz

ROOT    = pathlib.Path(__file__).parent
DB      = ROOT / "data" / "energrex.db"
DL      = pathlib.Path.home() / "Downloads"
ACCT    = "account_1"
ET      = pytz.timezone("America/New_York")
OCC_RE  = re.compile(r'^([A-Z]{1,6})(\d{6})([CP])(\d{8})$')

ZH_MAP = {
    "代号": "symbol", "数量": "quantity", "详细说明": "description",
    "价格": "price", "市值": "market_value", "当日益损$": "day_pnl",
    "单位成本": "unit_cost", "成本": "cost_basis",
    "益损 $": "unrealized_pnl", "益损 %": "unrealized_pnl_pct",
}

def parse_num(v):
    if pd.isna(v): return None
    s = str(v).replace(",", "").replace("%", "").strip()
    try: return float(s)
    except ValueError: return None


files = sorted(DL.glob("91224907-positions*.xlsx"), key=lambda f: f.stat().st_mtime, reverse=True)
if not files:
    print("No positions xlsx found in ~/Downloads"); raise SystemExit(1)

src = files[0]
print(f"Loading: {src.name}  ({src.stat().st_size:,} B)")

df = pd.read_excel(src)
df.rename(columns=ZH_MAP, inplace=True)

now = datetime.datetime.now(ET).isoformat()
con = sqlite3.connect(str(DB))

# Clear existing positions for this account before re-importing
con.execute("DELETE FROM positions WHERE account_id=?", (ACCT,))

inserted = 0
for _, row in df.iterrows():
    sym = str(row.get("symbol", "")).strip().upper()
    if not sym or sym == "NAN":
        continue

    pos_type = "option" if OCC_RE.match(sym) else "stock"
    qty      = parse_num(row.get("quantity"))
    cost     = parse_num(row.get("cost_basis"))
    mv       = parse_num(row.get("market_value"))
    upnl     = parse_num(row.get("unrealized_pnl"))
    upnl_pct = parse_num(row.get("unrealized_pnl_pct"))
    desc     = str(row.get("description", "")).strip()

    con.execute("""
        INSERT INTO positions
          (account_id, sync_time, symbol, position_type, quantity,
           cost_basis, market_value, unrealized_pnl, unrealized_pnl_pct, description)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (ACCT, now, sym, pos_type, qty, cost, mv, upnl, upnl_pct, desc))
    inserted += 1
    print(f"  {sym:<8} {pos_type:<6}  qty={qty}  mv={mv}  upnl={upnl}")

con.commit()
total = con.execute("SELECT COUNT(*) FROM positions WHERE account_id=?", (ACCT,)).fetchone()[0]
con.close()
print(f"\n✅ 导入 {inserted} 条 / positions 表合计 {total} 条")
