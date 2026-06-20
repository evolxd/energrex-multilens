"""
Standalone: 计算期权成本价 + 拉取现价 → 更新 DB
不依赖 Streamlit，可直接 python run_cost_and_price.py 运行
"""
import os, re, sqlite3, datetime, pathlib, urllib.request, json
import pandas as pd
import pytz

ROOT    = pathlib.Path(__file__).parent
DB_PATH = ROOT / "data" / "energrex.db"
ACCT    = "account_1"
ET      = pytz.timezone("America/New_York")

# Load .env
for _line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        k, v = _line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

MD_KEY  = os.environ.get("MARKETDATA_API_KEY", "")
MD_BASE = "https://api.marketdata.app/v1"
OCC_RE  = re.compile(r'^([A-Z]{1,6})(\d{6})([CP])(\d{8})$')


def db():
    c = sqlite3.connect(str(DB_PATH)); c.row_factory = sqlite3.Row; return c


def load_options(acct_id):
    con = db()
    rows = con.execute("SELECT * FROM options_positions WHERE account_id=?", (acct_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def md_quote(sym):
    if not MD_KEY: return None
    try:
        url = f"{MD_BASE}/options/quotes/{sym}/?token={MD_KEY}"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        if data.get("s") != "ok": return None
        def first(k): v = data.get(k); return v[0] if isinstance(v, list) and v else v
        return {"mid": first("mid"), "last": first("last"), "change": first("change")}
    except Exception as e:
        print(f"  ⚠ {sym}: {e}"); return None


# ── Step 1: 计算单位成本 ───────────────────────────────
print("Step 1: 计算单位成本（加权平均）")
con = db()
df_txn = pd.read_sql_query(
    "SELECT symbol, quantity, price FROM transactions WHERE account_id=?", con, params=(ACCT,))
con.close()
df_txn["quantity"] = pd.to_numeric(df_txn["quantity"], errors="coerce")
df_txn["price"]    = pd.to_numeric(df_txn["price"], errors="coerce")
df_txn["symbol"]   = df_txn["symbol"].astype(str).str.strip().str.upper()

positions = load_options(ACCT)
now = datetime.datetime.now(ET).isoformat()
cost_updated = 0

for row in positions:
    sym     = row["symbol"].strip().upper()
    net_qty = int(row.get("quantity") or 0)
    if net_qty == 0: continue

    txn = df_txn[df_txn["symbol"] == sym]
    if txn.empty: continue

    opens = txn[txn["quantity"] < 0] if net_qty < 0 else txn[txn["quantity"] > 0]
    valid = opens.dropna(subset=["price"])
    if valid.empty: continue

    qty_abs = valid["quantity"].abs()
    avg = (valid["price"] * qty_abs).sum() / qty_abs.sum()

    cur = row.get("current_price")
    tpnl = None
    if cur is not None:
        tpnl = (float(cur) - avg) * net_qty * 100

    con = db()
    con.execute("UPDATE options_positions SET unit_cost=?, total_pnl=?, last_updated=? "
                "WHERE account_id=? AND symbol=?",
                (round(avg, 4), round(tpnl, 2) if tpnl is not None else None, now, ACCT, sym))
    con.commit(); con.close()
    cost_updated += 1
    print(f"  {sym:<32}  avg_cost=${avg:.4f}"
          + (f"  tpnl=${tpnl:.2f}" if tpnl is not None else ""))

print(f"  → 更新 {cost_updated} 个合约的单位成本\n")


# ── Step 2: 拉取现价 + 更新盈亏 ──────────────────────────
print("Step 2: 拉取现价（MarketData.app）")
today    = datetime.date.today()
positions = load_options(ACCT)  # 重新加载（含 unit_cost）
price_updated = deleted = 0
no_price = []

for row in positions:
    sym    = row["symbol"].strip().upper()
    expiry = row.get("expiry", "")

    # 删除已到期
    try:
        if expiry and datetime.date.fromisoformat(expiry) < today:
            con = db()
            con.execute("DELETE FROM options_positions WHERE account_id=? AND symbol=?", (ACCT, sym))
            con.commit(); con.close()
            deleted += 1
            print(f"  🗑 删除已到期：{sym} ({expiry})")
            continue
    except Exception:
        pass

    quote = md_quote(sym)
    if not quote:
        no_price.append(sym); continue
    price = quote.get("mid") or quote.get("last")
    if price is None:
        no_price.append(sym); continue

    qty       = int(row.get("quantity") or 0)
    unit_cost = row.get("unit_cost")
    mv        = abs(qty) * price * 100
    change    = quote.get("change") or 0
    day_pnl   = change * qty * 100
    total_pnl = None
    if unit_cost is not None:
        total_pnl = (price - unit_cost) * qty * 100

    con = db()
    con.execute("""UPDATE options_positions
        SET current_price=?, market_value=?, day_pnl=?, total_pnl=?, last_updated=?
        WHERE account_id=? AND symbol=?""",
        (round(price, 4), round(mv, 2),
         round(day_pnl, 2), round(total_pnl, 2) if total_pnl is not None else None,
         now, ACCT, sym))
    con.commit(); con.close()
    price_updated += 1
    tstr = f"  tpnl=${total_pnl:.2f}" if total_pnl is not None else ""
    print(f"  {sym:<32}  price=${price:.3f}  mv=${mv:.0f}{tstr}")

print(f"\n  → 报价更新 {price_updated} / 删除到期 {deleted} / 无报价 {len(no_price)}")
if no_price:
    for s in no_price: print(f"    ❓ 无报价：{s}")


# ── 汇总 ──────────────────────────────────────────────
print("\n最终汇总：")
positions = load_options(ACCT)
total_mv = sum((r["market_value"] or 0) for r in positions)
total_pnl = sum((r["total_pnl"] or 0) for r in positions)
total_dpnl = sum((r["day_pnl"] or 0) for r in positions)
n_priced = sum(1 for r in positions if r["current_price"] is not None)
print(f"  合约数：{len(positions)} （{n_priced} 有报价）")
print(f"  总市值：${total_mv:,.2f}")
print(f"  总盈亏：${total_pnl:,.2f}")
print(f"  当日盈亏：${total_dpnl:,.2f}")
