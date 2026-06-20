"""
scripts/decision_engine_candidates.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
完整仓位计算流程（4步）：
  Step 1 — 计算时间加权 Kelly 并固化到 strategy_kelly 表
  Step 2 — 读取最新净值 + BD
  Step 3 — 输出 1/3 Kelly 仓位建议表（含 BD 约束）
  Step 4 — Kelly 原始 vs 加权对比表

用法：
    python scripts/decision_engine_candidates.py [acct_id]
"""
import sys, pathlib, datetime, sqlite3, json

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from exit_strategy_engine import (
    weighted_kelly_by_strategy, save_kelly_to_db,
    compute_position_sizing, format_comparison_table,
)

DB      = ROOT / "data" / "energrex.db"
ACCT_ID = sys.argv[1] if len(sys.argv) > 1 else "account_1"
TODAY   = datetime.date.today()

SEP = "=" * 100

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: 计算 Kelly 并写入 DB
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print(f"  ENERGREX 仓位决策引擎   账户:{ACCT_ID}   {TODAY}")
print(SEP)

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

stats = weighted_kelly_by_strategy(DB, ACCT_ID, TODAY)
if not stats:
    print("  ⚠️  无 combo_strategy 数据，请先运行 classify_combos.py")
    sys.exit(1)

n_saved = save_kelly_to_db(conn, stats, ACCT_ID, TODAY)
print(f"\n  ✅ Step 1 完成 — Kelly 已固化到 strategy_kelly 表 ({n_saved} 策略)")

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: 读取净值 + BD
# ─────────────────────────────────────────────────────────────────────────────
bal = conn.execute(
    "SELECT total_equity FROM account_balance ORDER BY rowid DESC LIMIT 1"
).fetchone()
equity = float(bal["total_equity"]) if bal else 0.0

snap_row = conn.execute(
    "SELECT snap_json FROM daily_briefing ORDER BY id DESC LIMIT 1"
).fetchone()
bd_current = 0.0
if snap_row:
    snap = json.loads(snap_row["snap_json"] or "{}")
    bd_current = float(snap.get("beta_delta_ratio") or 0)

BD_LIMIT   = 3.5
bd_pct     = bd_current * 100
bd_lim_pct = BD_LIMIT * 100
headroom   = (BD_LIMIT - bd_current) * 100

print(f"\n  ✅ Step 2 完成 — 账户净值: ${equity:,.2f}")
print(f"              BD: {bd_pct:.1f}%  上限: {bd_lim_pct:.0f}%  余量: {headroom:+.1f}%  "
      + ("✅ 正常" if headroom > 10 else "⚠️ 偏紧"))

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: 仓位建议表
# ─────────────────────────────────────────────────────────────────────────────
sizing = compute_position_sizing(
    conn, ACCT_ID, stats, equity, bd_current, BD_LIMIT, TODAY
)

print(f"\n{'─'*100}")
print("  Step 3 — 1/3 Kelly 仓位建议")
print(f"{'─'*100}")

def cw(s: str, w: int) -> str:
    vis = sum(2 if ord(c) > 127 else 1 for c in s)
    return s + " " * max(0, w - vis)

HDR = [("策略", 24), ("加权Kelly", 10), ("1/3 Kelly", 10),
       ("最大风险($)", 12), ("保证金/张($)", 12), ("建议张数", 10),
       ("BD影响", 10)]

print("  " + "  ".join(cw(h, w) for h, w in HDR))
print("  " + "─" * 96)

positives, negatives = [], []
for r in sizing:
    is_neg = r["建议张数"] == "不建议"
    (negatives if is_neg else positives).append(r)

for r in positives + [None] + negatives:
    if r is None:
        print("  " + "·" * 96)
        continue
    cells = [
        r["策略"], r["加权Kelly"], r["1/3 Kelly"],
        r.get("最大风险($)", "—"), r.get("保证金/张($)", "—"), r["建议张数"],
        r["BD影响"],
    ]
    line = "  " + "  ".join(cw(cells[i], HDR[i][1]) for i in range(len(HDR)))
    warn = r.get("警告", "")
    if warn:
        line += f"  {warn}"
    print(line)

print(f"  {'─'*96}")
print(f"  注：1/3 Kelly = 加权Kelly ÷ 3  |  保证金估算基于 Reg T（20%×行权价×100 或历史亏损）")
print(f"      BD余量 {headroom:+.1f}%  |  ↑增加BD 的策略受余量约束，↓降低BD 的策略不受BD限制")

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Kelly 原始 vs 加权对比
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'─'*100}")
print("  Step 4 — 原始 Kelly vs 时间加权 Kelly 对比")
print(f"{'─'*100}")

HDR2 = [("策略", 24), ("原始胜率", 8), ("加权胜率", 8),
        ("原始Kelly", 10), ("加权Kelly", 10), ("半Kelly", 10),
        ("近30天", 6), ("近90天", 6), ("全量", 6)]

print("  " + "  ".join(cw(h, w) for h, w in HDR2))
print("  " + "─" * 96)

for r in format_comparison_table(stats):
    cells = [
        r["策略"], r["原始胜率"], r["加权胜率"],
        r["原始Kelly"], r["加权Kelly"], r["半Kelly(建议)"],
        str(r["近30天"]), str(r["近90天"]), str(r["全量"]),
    ]
    line = "  " + "  ".join(cw(cells[i], HDR2[i][1]) for i in range(len(HDR2)))
    if r["警告"]:
        line += "  ⚠️"
    print(line)

# 摘要
print(f"\n  ✅ 加权Kelly > 0（可用策略）:")
for r in sizing:
    if r["建议张数"] != "不建议":
        warn = f"  {r['警告']}" if r.get("警告") else ""
        print(f"     {cw(r['策略'],24)}  Kelly={r['加权Kelly']:>7}  建议={r['建议张数']:>5}  {r['BD影响']}{warn}")

print(f"\n  ❌ 加权Kelly ≤ 0（不建议）:")
for r in sizing:
    if r["建议张数"] == "不建议":
        print(f"     {cw(r['策略'],24)}  Kelly={r['加权Kelly']:>7}")

conn.close()
print(f"\n{SEP}\n")
