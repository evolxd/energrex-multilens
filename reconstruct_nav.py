#!/usr/bin/env python3
"""
reconstruct_nav.py
Reconstruct daily NAV history from transaction ledger.

  NAV[t] = cash[t] + stock_market_value[t] + option_book_value[t]

  cash[t]        = starting_cash + Σ amounts up to t
  stock_mv[t]    = Σ (qty × yfinance_close) for open stock positions
  opt_book[t]    = Σ (qty × open_price × 100) for open option positions
                   (realized P&L shows up at close / expiry)

Pre-existing position detection:
  - Stocks: use DAILY-NET quantities to avoid same-day ordering false positives
  - Options: track long_available (from OPEN BUYs) and short_available (from OPEN SELLs)
             separately, detect gaps when CLOSING transactions exceed available supply
"""
import sqlite3, pathlib, re, sys
from datetime import date, timedelta
from collections import defaultdict

import pandas as pd

DB   = pathlib.Path('data/energrex.db')
ACCT = 'account_1'
OCC  = re.compile(r'^([A-Z]{1,6})\d{6}[CP]\d{8}$')

# ── Load ───────────────────────────────────────────────────────────
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

nav_row = conn.execute(
    'SELECT total_equity, cash_balance, sync_time FROM account_balance '
    'WHERE account_id=? ORDER BY sync_time DESC LIMIT 1', (ACCT,)
).fetchone()

TODAY_NAV  = float(nav_row['total_equity'])
TODAY_CASH = float(nav_row['cash_balance'])
TODAY_STR  = str(nav_row['sync_time'])[:10]

txns = [dict(t) for t in conn.execute(
    'SELECT * FROM transactions WHERE account_id=? ORDER BY trade_date, id', (ACCT,)
)]

print(f'Transactions: {len(txns)}  {txns[0]["trade_date"]} → {txns[-1]["trade_date"]}')
print(f'Anchor: NAV=${TODAY_NAV:,.2f}  Cash=${TODAY_CASH:,.2f}  ({TODAY_STR})')

# ── Starting cash ──────────────────────────────────────────────────
net_cash = sum(float(t['amount'] or 0) for t in txns)
starting_cash = TODAY_CASH - net_cash
print(f'Starting cash (before history starts): ${starting_cash:,.2f}')

# ── Collect symbols ───────────────────────────────────────────────
stock_syms: set[str] = set()
opt_syms:   set[str] = set()
for t in txns:
    sym = (t['symbol'] or '').strip().upper()
    if not sym:
        continue
    if OCC.match(sym):
        opt_syms.add(sym)
    else:
        stock_syms.add(sym)

# ── Pre-pass: stock pre-existing positions ─────────────────────────
# Use DAILY NET quantities to avoid same-day ordering false positives.
# e.g. ARM: SELL -18 then BUY +20 on the same date → daily net = +2, never negative.
starting_stock: dict[str, float] = {}

for sym in sorted(stock_syms):
    daily_net: dict[str, float] = defaultdict(float)
    for t in txns:
        if (t['symbol'] or '').strip().upper() == sym:
            daily_net[t['trade_date']] += float(t['quantity'] or 0)

    running = min_q = 0.0
    for d in sorted(daily_net):
        running += daily_net[d]
        if running < min_q:
            min_q = running

    if min_q < -0.001:
        starting_stock[sym] = round(-min_q)
        print(f'  Pre-existing stock : {sym:10s}  qty={starting_stock[sym]:.0f}')

# ── Pre-pass: option pre-existing positions ────────────────────────
# Track longs and shorts separately.
# SELL (CLOSING) → needs pre-existing LONG   (no OPEN BUY preceded it)
# BUY  (CLOSING) → needs pre-existing SHORT  (no OPEN SELL preceded it)
# SELL (OPEN)    → opens new short, valid negative qty
# BUY  (OPEN)    → opens new long, increases long_available
starting_opt: dict[str, dict] = {}

for sym in sorted(opt_syms):
    long_avail  = 0.0
    short_avail = 0.0
    pre_long    = 0.0
    pre_short   = 0.0
    ref_price   = None

    # Sort: within each day, OPEN before CLOSING — eliminates same-day false positives
    # e.g. SELL CLOSE processed before BUY OPEN of same day → false pre-existing detection
    sym_txns = sorted(
        [t for t in txns
         if (t['symbol'] or '').strip().upper() == sym
         and 'EXPIR' not in (t['type'] or '').upper()],
        key=lambda t: (
            t['trade_date'],
            1 if 'CLOSING' in str(t.get('description', '')).upper() else 0
        )
    )

    for t in sym_txns:
        if (t['symbol'] or '').strip().upper() != sym:
            continue
        typ  = (t['type'] or '').upper()
        desc = (t['description'] or '').upper()
        qty  = float(t['quantity'] or 0)
        price= float(t['price'] or 0)

        if 'EXPIR' in typ:
            continue

        if price > 0 and ref_price is None:
            ref_price = price

        is_closing = 'CLOSING' in desc
        is_open    = 'OPEN' in desc and 'CLOSING' not in desc

        if qty > 0:        # BUY (either opening long or closing short)
            if is_closing:
                # BUY TO CLOSE: need short_avail + pre_short to cover qty
                available = short_avail + pre_short
                if available < qty - 0.001:
                    pre_short += qty - available
                    short_avail = 0.0
                else:
                    short_avail = max(0.0, short_avail - qty)
            else:
                # BUY OPEN: new long
                long_avail += qty
        elif qty < 0:      # SELL (either closing long or opening short)
            if is_closing:
                # SELL TO CLOSE: consuming long_avail
                abs_qty = abs(qty)
                deficit = abs_qty - (long_avail + pre_long)
                if deficit > 0:
                    pre_long += deficit
                long_avail = max(0.0, long_avail - abs_qty)
            else:
                # SELL OPEN: new short
                short_avail += abs(qty)

    if pre_long > 0.001 or pre_short > 0.001:
        net_qty = round(pre_long - pre_short)
        if abs(net_qty) > 0.001:
            starting_opt[sym] = {'qty': net_qty, 'price': ref_price or 1.0}
            kind = 'LONG' if net_qty > 0 else 'SHORT'
            print(f'  Pre-existing opt {kind}: {sym:30s} qty={net_qty}  px={ref_price}')

print(f'\nPre-existing: {len(starting_stock)} stocks, {len(starting_opt)} options')

# ── yfinance prices ───────────────────────────────────────────────
import yfinance as yf

first_date = txns[0]['trade_date']
end_date   = (date.fromisoformat(TODAY_STR) + timedelta(days=1)).strftime('%Y-%m-%d')

print(f'\nFetching yfinance prices ({len(stock_syms)} symbols)...')
price_data: dict[str, pd.Series] = {}

for sym in sorted(stock_syms):
    try:
        hist = yf.Ticker(sym).history(start=first_date, end=end_date, auto_adjust=True)
        if not hist.empty:
            hist.index = pd.to_datetime(hist.index).tz_localize(None).normalize()
            price_data[sym] = hist['Close'].ffill()
        else:
            print(f'  {sym}: no data')
    except Exception as e:
        print(f'  {sym}: {e}')

print(f'  Got prices for {len(price_data)}/{len(stock_syms)} symbols')

# ── Group transactions by date ─────────────────────────────────────
txns_by_date: dict[str, list] = defaultdict(list)
for t in txns:
    txns_by_date[t['trade_date']].append(t)

# ── Forward simulation ─────────────────────────────────────────────
start_d = date.fromisoformat(first_date)
end_d   = date.fromisoformat(TODAY_STR)

cash    = starting_cash
stk_pos = defaultdict(float, starting_stock)
opt_pos: dict[str, dict] = {s: dict(v) for s, v in starting_opt.items()}

daily = []
cur   = start_d

while cur <= end_d:
    ds = cur.strftime('%Y-%m-%d')

    for t in txns_by_date.get(ds, []):
        sym   = (t['symbol'] or '').strip().upper()
        qty   = float(t['quantity'] or 0)
        price = float(t['price'] or 0)
        amt   = float(t['amount'] or 0)
        typ   = (t['type'] or '').upper()

        cash += amt

        if not sym:
            continue

        if OCC.match(sym):
            if 'EXPIR' in typ:
                opt_pos.pop(sym, None)
            elif qty != 0:
                if sym in opt_pos:
                    opt_pos[sym]['qty'] += qty
                    if abs(opt_pos[sym]['qty']) < 1e-6:
                        del opt_pos[sym]
                else:
                    opt_pos[sym] = {'qty': qty, 'price': price if price > 0 else 0.01}
        else:
            stk_pos[sym] += qty

    # Stock market value
    pd_cur = pd.Timestamp(cur)
    stk_mv = 0.0
    for sym, qty in stk_pos.items():
        if abs(qty) < 1e-6 or sym not in price_data:
            continue
        s = price_data[sym]
        avail = s[s.index <= pd_cur]
        if not avail.empty:
            stk_mv += qty * float(avail.iloc[-1])

    # Option book value: signed (long=+, short=-)
    opt_book = sum(v['qty'] * v['price'] * 100 for v in opt_pos.values())

    nav = cash + stk_mv + opt_book
    daily.append({
        'date': ds,
        'nav': round(nav, 2),
        'cash': round(cash, 2),
        'stk_mv': round(stk_mv, 2),
        'opt_book': round(opt_book, 2),
    })

    cur += timedelta(days=1)

# ── Final position audit ───────────────────────────────────────────
print('\nFinal stock positions (non-zero at simulation end):')
for sym in sorted(stk_pos):
    q = stk_pos[sym]
    if abs(q) > 0.001:
        price = float(price_data[sym].iloc[-1]) if sym in price_data else 0
        print(f'  {sym:10s}  qty={q:.1f}  px={price:.2f}  val=${q*price:,.0f}')

print('\nFinal option positions (open at simulation end):')
for sym in sorted(opt_pos):
    v = opt_pos[sym]
    print(f'  {sym:35s}  qty={v["qty"]:.0f}  px={v["price"]:.2f}  book=${v["qty"]*v["price"]*100:,.0f}')

# ── Summary table ──────────────────────────────────────────────────
print(f'\n{"Date":12}  {"NAV":>12}  {"Cash":>10}  {"StocksMV":>10}  {"OptBook":>10}')
print('-' * 65)
milestones = [0, len(daily)//6, len(daily)//3, len(daily)//2,
              2*len(daily)//3, 5*len(daily)//6, -1]
for i in milestones:
    r = daily[i]
    print(f"{r['date']:12}  ${r['nav']:>11,.0f}  ${r['cash']:>9,.0f}"
          f"  ${r['stk_mv']:>9,.0f}  ${r['opt_book']:>9,.0f}")

last = daily[-1]
print(f'\nComputed last NAV  : ${last["nav"]:,.2f}')
print(f'Actual anchor NAV  : ${TODAY_NAV:,.2f}')
print(f'Residual           : ${last["nav"] - TODAY_NAV:+,.2f}  '
      f'({(last["nav"] - TODAY_NAV) / TODAY_NAV * 100:+.1f}%)')
print(f'\nResidual breakdown:')
print(f'  Stocks (sim)     : ${last["stk_mv"]:,.2f}')
print(f'  Opt book (sim)   : ${last["opt_book"]:,.2f}')
print(f'  Cash (sim)       : ${last["cash"]:,.2f}')
actual_opt = TODAY_NAV - TODAY_CASH - sum(
    (stk_pos[sym] * float(price_data[sym].iloc[-1]))
    for sym in stk_pos if abs(stk_pos[sym]) > 0.001 and sym in price_data
)
print(f'  Actual option val: ~${actual_opt:,.2f}  (= NAV - cash - stock_mv)')

# ── Write to DB ───────────────────────────────────────────────────
existing = set(
    str(r[0])[:10]
    for r in conn.execute('SELECT sync_time FROM account_balance WHERE account_id=?', (ACCT,))
)

rows = [(r['date'], r['nav']) for r in daily if r['date'] not in existing]
print(f'\nRows to insert: {len(rows)}  (skipping {len(daily)-len(rows)} existing)')

if '--dry-run' in sys.argv:
    print('Dry run — nothing written.')
    sys.exit(0)

for ds, nav in rows:
    conn.execute(
        'INSERT INTO account_balance (account_id, sync_time, total_equity) VALUES (?, ?, ?)',
        (ACCT, ds + 'T16:00:00-04:00', nav)
    )
conn.commit()
conn.close()
print(f'✅ Inserted {len(rows)} rows into account_balance.')
