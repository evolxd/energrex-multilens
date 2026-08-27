"""从 transactions 推算未平仓期权，写入 options_positions 表（已有记录不覆盖）。"""
import sqlite3, re, datetime, pathlib, pandas as pd
import pytz

_OCC_RE = re.compile(r'^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$')
_ET = pytz.timezone("America/New_York")
DB  = pathlib.Path("data/energrex.db")
ACCT = "account_1"

def parse_occ(sym):
    m = _OCC_RE.match(sym.strip().upper())
    if not m:
        return None
    root, yy, mm, dd, cp, sr = m.groups()
    return {
        "direction": "Call" if cp == "C" else "Put",
        "strike":    int(sr) / 1000,
        "expiry":    f"20{yy}-{mm}-{dd}",
    }

con = sqlite3.connect(DB)
df = pd.read_sql_query(
    "SELECT symbol, SUM(quantity) as net FROM transactions WHERE account_id=? GROUP BY symbol",
    con, params=(ACCT,))

existing = {r[0] for r in con.execute(
    "SELECT symbol FROM options_positions WHERE account_id=?", (ACCT,)).fetchall()}

now = datetime.datetime.now(_ET).isoformat()
inserted = 0
for _, row in df.iterrows():
    sym = str(row["symbol"]).strip()
    net = row["net"] or 0
    if abs(net) < 0.001 or sym in existing:
        continue
    info = parse_occ(sym)
    if not info:
        continue
    con.execute("""
        INSERT OR IGNORE INTO options_positions
        (account_id, symbol, direction, strike, expiry, quantity, last_updated)
        VALUES (?,?,?,?,?,?,?)
    """, (ACCT, sym, info["direction"], info["strike"], info["expiry"], int(round(net)), now))
    print(f"  {sym:<32} qty={int(round(net)):+d}  exp={info['expiry']}  {info['direction']}")
    inserted += 1

con.commit()
n_total = con.execute("SELECT COUNT(*) FROM options_positions WHERE account_id=?", (ACCT,)).fetchone()[0]
con.close()
print(f"\n新增 {inserted} 条 / 表中合计 {n_total} 条")
