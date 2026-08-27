"""
classify_combos.py
给 option_realized_trades 新增 combo_strategy + combo_id 字段，
用贪心两两匹配（不做传递合并），按策略级别重新统计并计算 Kelly。
"""
import sqlite3, sys, pathlib
from datetime import datetime
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = pathlib.Path(__file__).parent / "data" / "energrex.db"
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

# ─── 1. 新增字段 ────────────────────────────────────────────────────────────
for col in ["combo_strategy", "combo_id"]:
    try:
        conn.execute(f"ALTER TABLE option_realized_trades ADD COLUMN {col} TEXT")
        conn.commit()
        print(f"  ✅ 新增字段: {col}")
    except Exception as e:
        if "duplicate" in str(e).lower():
            print(f"  ♻️  字段已存在（将覆盖）: {col}")
            conn.execute(f"UPDATE option_realized_trades SET {col}=NULL")
            conn.commit()
        else:
            raise

# ─── 2. 读取全部记录 ─────────────────────────────────────────────────────────
rows = conn.execute("""
    SELECT id, underlying, expiry, close_date, strategy_type,
           strike, quantity, realized_pnl, win_loss
    FROM option_realized_trades
    ORDER BY underlying, expiry, close_date, id
""").fetchall()

records = [dict(r) for r in rows]

def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None

for r in records:
    r["_cd"]       = parse_date(r["close_date"])
    r["_matched"]  = False

print(f"\n  总记录数: {len(records)}")

# ─── 3. 分组 ─────────────────────────────────────────────────────────────────
groups = defaultdict(list)
for r in records:
    groups[(r["underlying"], r["expiry"])].append(r)

combo_ctr = [0]
def new_cid(und, exp):
    combo_ctr[0] += 1
    return f"{und}_{(exp or 'NA').replace('-','')}_{combo_ctr[0]:04d}"

# ─── 4. 分类函数 ──────────────────────────────────────────────────────────────
def classify_pair(r1, r2):
    t1, t2 = r1["strategy_type"], r2["strategy_type"]
    k1 = float(r1["strike"] or 0)
    k2 = float(r2["strike"] or 0)
    pair = tuple(sorted([t1, t2]))

    if pair == ("short_call", "short_put"):
        return "short_straddle" if abs(k1 - k2) < 0.01 else "short_strangle"
    if pair == ("long_call", "long_put"):
        return "long_straddle"  if abs(k1 - k2) < 0.01 else "long_strangle"
    if pair == ("long_call", "short_call"):
        sc = r1 if t1 == "short_call" else r2
        lc = r1 if t1 == "long_call"  else r2
        # 用户定义：short_strike > long_strike → bear_call_spread
        return "bear_call_spread" if sc["strike"] > lc["strike"] else "bull_call_spread"
    if pair == ("long_put", "short_put"):
        sp = r1 if t1 == "short_put" else r2
        lp = r1 if t1 == "long_put"  else r2
        # 用户定义：short_strike < long_strike → bull_put_spread
        return "bull_put_spread" if sp["strike"] < lp["strike"] else "bear_put_spread"
    if pair == ("long_call", "short_put"):
        # short_put K < long_call K → risk reversal bullish（卖低put买高call）
        sc_put = r1 if t1 == "short_put" else r2
        lg_call = r1 if t1 == "long_call" else r2
        return "risk_reversal_bullish" if sc_put["strike"] < lg_call["strike"] else "complex_2leg"
    if pair == ("long_put", "short_call"):
        # short_call K > long_put K → risk reversal bearish（卖高call买低put）
        sc_call = r1 if t1 == "short_call" else r2
        lg_put  = r1 if t1 == "long_put"  else r2
        return "risk_reversal_bearish" if sc_call["strike"] > lg_put["strike"] else "complex_2leg"
    if t1 == t2:
        return f"scaled_{t1}"
    return "complex_2leg"

def classify_single(r):
    t = r["strategy_type"]
    if t == "short_put":   return "naked_short_put"
    if t == "short_call":  return "naked_short_call"
    if t in ("long_call", "long_put"): return "directional"
    return t or "unknown"

# 互补类型优先级（越靠前越优先匹配）
PREFER = {
    "short_call": ["short_put", "long_call"],
    "short_put":  ["short_call", "long_put"],
    "long_call":  ["short_call", "long_put"],
    "long_put":   ["short_put", "long_call"],
}

# ─── 5. 贪心两两匹配 ──────────────────────────────────────────────────────────
WINDOW = 3   # ±3 天

results = {}   # id → (combo_id, combo_strategy)

for (und, exp), grp in groups.items():
    pool = [r for r in grp]
    pool.sort(key=lambda r: (r["_cd"] or datetime.min.date(), r["strategy_type"]))

    while pool:
        r1   = pool.pop(0)
        if r1["_matched"]:
            continue

        preferred = PREFER.get(r1["strategy_type"], [])
        best, best_score = None, 99999

        for r2 in pool:
            if r2["_matched"]:
                continue
            if r1["_cd"] and r2["_cd"]:
                diff = abs((r1["_cd"] - r2["_cd"]).days)
            else:
                diff = 9999
            if diff > WINDOW:
                continue
            # 评分：天数差越小越好，互补类型优先
            type_bonus = preferred.index(r2["strategy_type"]) if r2["strategy_type"] in preferred else 5
            score = diff * 10 + type_bonus
            if score < best_score:
                best_score, best = score, r2

        if best is not None:
            cid = new_cid(und, exp)
            cs  = classify_pair(r1, best)
            results[r1["id"]] = (cid, cs)
            results[best["id"]] = (cid, cs)
            r1["_matched"] = best["_matched"] = True
            pool = [r for r in pool if not r["_matched"]]
        else:
            cid = new_cid(und, exp)
            cs  = classify_single(r1)
            results[r1["id"]] = (cid, cs)
            r1["_matched"] = True

# ─── 6. 写回数据库 ────────────────────────────────────────────────────────────
for rid, (cid, cs) in results.items():
    conn.execute(
        "UPDATE option_realized_trades SET combo_strategy=?, combo_id=? WHERE id=?",
        (cs, cid, rid)
    )
conn.commit()
print(f"  写回 {len(results)} 条记录\n")

# ─── 7. 策略级别汇总（按 combo_id 聚合后再分组）────────────────────────────
# 先算每个 combo 的总 PnL
combo_pnl_q = conn.execute("""
    SELECT combo_id, combo_strategy,
           SUM(realized_pnl) AS total_pnl,
           COUNT(*) AS legs
    FROM option_realized_trades
    GROUP BY combo_id
""").fetchall()

from collections import defaultdict as dd2
strat_stats = dd2(lambda: {"trades":0,"wins":0,"losses":0,"win_pnl":[],"loss_pnl":[]})

for row in combo_pnl_q:
    cs  = row["combo_strategy"] or "unknown"
    pnl = row["total_pnl"] or 0
    s   = strat_stats[cs]
    s["trades"] += 1
    if pnl > 0:
        s["wins"] += 1
        s["win_pnl"].append(pnl)
    else:
        s["losses"] += 1
        s["loss_pnl"].append(abs(pnl))

# Kelly 公式: f* = p - (1-p)/b   where b = avg_win/avg_loss
def kelly(p, avg_w, avg_l):
    if avg_l == 0 or avg_w == 0:
        return None
    b = avg_w / avg_l
    return round((p - (1 - p) / b) * 100, 1)

print("=" * 90)
print(f"{'策略':28s}  {'组合数':>6}  {'胜率':>6}  {'平均盈':>8}  {'平均亏':>8}  "
      f"{'总盈亏':>10}  {'Kelly%':>7}")
print("-" * 90)

total_trades = 0
by_pnl = sorted(strat_stats.items(), key=lambda x: -sum(x[1]["win_pnl"]+[-v for v in x[1]["loss_pnl"]]))

for cs, s in by_pnl:
    n     = s["trades"]
    p     = s["wins"] / n if n else 0
    aw    = sum(s["win_pnl"])  / len(s["win_pnl"])  if s["win_pnl"]  else 0
    al    = sum(s["loss_pnl"]) / len(s["loss_pnl"]) if s["loss_pnl"] else 0
    tpnl  = sum(s["win_pnl"]) - sum(s["loss_pnl"])
    kv    = kelly(p, aw, al)
    kstr  = f"{kv:+.1f}%" if kv is not None else "  N/A"
    wr    = f"{p*100:.0f}%"
    total_trades += n
    print(f"  {cs:26s}  {n:6d}  {wr:>6}  {aw:8.0f}  {al:8.0f}  {tpnl:10.0f}  {kstr:>7}")

print("-" * 90)
print(f"  {'合计':26s}  {total_trades:6d}")

# ─── 8. 腿级别 vs 策略级别对比 ───────────────────────────────────────────────
print("\n=== 腿数 vs 策略组合数 ===")
leg_counts = conn.execute("""
    SELECT combo_strategy, COUNT(*) AS legs, COUNT(DISTINCT combo_id) AS combos
    FROM option_realized_trades
    GROUP BY combo_strategy ORDER BY combos DESC
""").fetchall()
for r in leg_counts:
    avg_legs = r["legs"] / r["combos"] if r["combos"] else 0
    print(f"  {(r['combo_strategy'] or 'unknown'):28s}  腿:{r['legs']:4d}  组合:{r['combos']:4d}  平均腿/组合:{avg_legs:.1f}")

conn.close()
print("\n✅ 完成")
