"""
exit_strategy_engine.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
从 option_realized_trades (combo_strategy 级别) 计算时间加权 Kelly，
并提供仓位建议（1/3 Kelly × BD约束）。

公开 API:
    weighted_kelly_by_strategy(db_path, acct_id, today=None)
        → dict[combo_strategy, StrategyStats]
    create_strategy_kelly_table(conn)
    save_kelly_to_db(conn, stats, acct_id, today)
    compute_position_sizing(conn, acct_id, kelly_stats, equity,
                            bd_current, bd_limit=3.5, today=None)
        → list[dict]  # 仓位建议行

StrategyStats 字段见 dataclass 定义。
"""
from __future__ import annotations
import sqlite3, datetime, pathlib, math
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Optional

# ── 权重分段 ─────────────────────────────────────────────────────────────────
_WEIGHT_TIERS: list[tuple[int, float]] = [
    (30,  1.0),
    (60,  0.8),
    (90,  0.6),
    (180, 0.4),
]
_WEIGHT_FLOOR = 0.2
_LOW_SAMPLE_THRESHOLD = 5   # 近90天样本 < 此值时标注警告


def _time_weight(close_date_str: Optional[str], today: datetime.date) -> float:
    """根据平仓距今天数返回衰减权重。"""
    if not close_date_str:
        return _WEIGHT_FLOOR
    try:
        cd   = datetime.date.fromisoformat(str(close_date_str)[:10])
        days = (today - cd).days
    except (ValueError, TypeError):
        return _WEIGHT_FLOOR
    for cutoff, w in _WEIGHT_TIERS:
        if days <= cutoff:
            return w
    return _WEIGHT_FLOOR


# ── 数据类 ───────────────────────────────────────────────────────────────────
@dataclass
class StrategyStats:
    combo_strategy: str

    # 样本计数
    n_total: int = 0
    n_30d:   int = 0
    n_90d:   int = 0

    # 原始（等权）
    raw_win_rate:  float = 0.0
    raw_avg_win:   float = 0.0
    raw_avg_loss:  float = 0.0
    raw_kelly:     Optional[float] = None

    # 时间加权
    w_win_rate:  float = 0.0
    w_avg_win:   float = 0.0
    w_avg_loss:  float = 0.0
    w_kelly:     Optional[float] = None
    w_half_kelly: Optional[float] = None

    low_sample_warning: bool = False

    # 原始数据供调用方使用
    combos: list = field(default_factory=list, repr=False)


def _kelly(p: float, avg_win: float, avg_loss: float) -> Optional[float]:
    """Kelly% = p - (1-p)/b，b = avg_win/avg_loss。返回 None 表示无法计算。"""
    if avg_win <= 0 or avg_loss <= 0:
        return None
    b = avg_win / avg_loss
    k = p - (1 - p) / b
    return round(k * 100, 2)


# ── 核心函数 ─────────────────────────────────────────────────────────────────
def weighted_kelly_by_strategy(
    db_path: str | pathlib.Path,
    acct_id: str = "account_1",
    today: Optional[datetime.date] = None,
) -> dict[str, StrategyStats]:
    """
    读取 option_realized_trades，按 combo_id 聚合到策略级别，
    计算原始 Kelly 和时间加权 Kelly。

    返回 dict[combo_strategy → StrategyStats]。
    """
    if today is None:
        today = datetime.date.today()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT
            combo_id,
            combo_strategy,
            MAX(close_date) AS last_close,   -- 多腿取最晚平仓日
            SUM(realized_pnl) AS total_pnl,
            COUNT(*) AS legs
        FROM option_realized_trades
        WHERE account_id = ?
          AND combo_id IS NOT NULL
          AND combo_strategy IS NOT NULL
        GROUP BY combo_id
        ORDER BY last_close
    """, (acct_id,)).fetchall()
    conn.close()

    # ── 分组 ──────────────────────────────────────────────────────────────
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        pnl = float(r["total_pnl"] or 0)
        cd  = r["last_close"]
        w   = _time_weight(cd, today)
        days = (today - datetime.date.fromisoformat(str(cd)[:10])).days if cd else 9999

        groups[r["combo_strategy"]].append({
            "pnl":   pnl,
            "win":   pnl > 0,
            "w":     w,
            "days":  days,
            "cd":    cd,
            "legs":  r["legs"],
        })

    result: dict[str, StrategyStats] = {}

    for cs, combos in groups.items():
        s = StrategyStats(combo_strategy=cs, combos=combos)
        s.n_total = len(combos)
        s.n_30d   = sum(1 for c in combos if c["days"] <= 30)
        s.n_90d   = sum(1 for c in combos if c["days"] <= 90)
        s.low_sample_warning = s.n_90d < _LOW_SAMPLE_THRESHOLD

        wins   = [c for c in combos if c["win"]]
        losses = [c for c in combos if not c["win"]]

        # ── 原始（等权）────────────────────────────────────────────────
        s.raw_win_rate = len(wins) / s.n_total if s.n_total else 0.0
        s.raw_avg_win  = sum(c["pnl"] for c in wins)   / len(wins)   if wins   else 0.0
        s.raw_avg_loss = sum(abs(c["pnl"]) for c in losses) / len(losses) if losses else 0.0
        s.raw_kelly    = _kelly(s.raw_win_rate, s.raw_avg_win, s.raw_avg_loss)

        # ── 时间加权 ────────────────────────────────────────────────────
        w_win_sum  = sum(c["w"] for c in wins)
        w_loss_sum = sum(c["w"] for c in losses)
        w_total    = sum(c["w"] for c in combos)

        s.w_win_rate = w_win_sum / w_total if w_total else 0.0
        s.w_avg_win  = (sum(c["pnl"] * c["w"]        for c in wins)   / w_win_sum)  if w_win_sum  else 0.0
        s.w_avg_loss = (sum(abs(c["pnl"]) * c["w"]   for c in losses) / w_loss_sum) if w_loss_sum else 0.0
        s.w_kelly    = _kelly(s.w_win_rate, s.w_avg_win, s.w_avg_loss)
        s.w_half_kelly = round(s.w_kelly / 2, 2) if s.w_kelly is not None else None

        result[cs] = s

    return result


# ── Step 1: 固化 Kelly 到数据库 ─────────────────────────────────────────────

_CREATE_KELLY_TABLE = """
CREATE TABLE IF NOT EXISTS strategy_kelly (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    combo_strategy   TEXT NOT NULL,
    sample_total     INTEGER,
    sample_90d       INTEGER,
    sample_30d       INTEGER,
    win_rate_raw     REAL,
    win_rate_weighted REAL,
    kelly_raw        REAL,
    kelly_weighted   REAL,
    quarter_kelly    REAL,
    avg_win          REAL,
    avg_loss         REAL,
    total_pnl        REAL,
    last_updated     TEXT,
    UNIQUE(combo_strategy)
);
"""


def create_strategy_kelly_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_KELLY_TABLE)
    conn.commit()


def save_kelly_to_db(
    conn: sqlite3.Connection,
    stats: dict[str, "StrategyStats"],
    acct_id: str = "account_1",
    today: Optional[datetime.date] = None,
) -> int:
    """将 Kelly 结果 UPSERT 到 strategy_kelly 表，返回写入行数。"""
    create_strategy_kelly_table(conn)
    if today is None:
        today = datetime.date.today()

    rows_written = 0
    for cs, s in stats.items():
        total_pnl = sum(c["pnl"] for c in s.combos)
        quarter_kelly = round(s.w_kelly / 3, 4) if s.w_kelly is not None else None
        conn.execute("""
            INSERT INTO strategy_kelly
                (combo_strategy, sample_total, sample_90d, sample_30d,
                 win_rate_raw, win_rate_weighted, kelly_raw, kelly_weighted,
                 quarter_kelly, avg_win, avg_loss, total_pnl, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(combo_strategy) DO UPDATE SET
                sample_total     = excluded.sample_total,
                sample_90d       = excluded.sample_90d,
                sample_30d       = excluded.sample_30d,
                win_rate_raw     = excluded.win_rate_raw,
                win_rate_weighted= excluded.win_rate_weighted,
                kelly_raw        = excluded.kelly_raw,
                kelly_weighted   = excluded.kelly_weighted,
                quarter_kelly    = excluded.quarter_kelly,
                avg_win          = excluded.avg_win,
                avg_loss         = excluded.avg_loss,
                total_pnl        = excluded.total_pnl,
                last_updated     = excluded.last_updated
        """, (
            cs, s.n_total, s.n_90d, s.n_30d,
            round(s.raw_win_rate, 4), round(s.w_win_rate, 4),
            s.raw_kelly, s.w_kelly,
            quarter_kelly,
            round(s.w_avg_win, 2), round(s.w_avg_loss, 2),
            round(total_pnl, 2), today.isoformat(),
        ))
        rows_written += 1
    conn.commit()
    return rows_written


# ── Step 2: 保证金估算 ────────────────────────────────────────────────────────

# delta 方向：正=增加BD，负=减少BD，0=中性
_BD_DELTA_DIR: dict[str, float] = {
    "naked_short_call":      -1.0,   # short call → 负delta → 降低正BD
    "naked_short_put":       +1.0,   # short put  → 正delta → 增加BD
    "short_strangle":         0.0,   # call/put 对冲，近似中性
    "short_straddle":         0.0,
    "bear_put_spread":       -0.3,
    "bull_put_spread":       +0.3,
    "bear_call_spread":      +0.3,
    "bull_call_spread":      +0.5,
    "directional":           +1.0,   # long call → 增加BD
    "scaled_long_call":      +1.0,
    "scaled_long_put":       -1.0,
    "scaled_short_call":     -1.0,
    "scaled_short_put":      +1.0,
    "long_strangle":          0.0,
    "risk_reversal_bullish": +0.7,
    "risk_reversal_bearish": -0.7,
}

_NAKED_SHORT  = {"naked_short_call", "naked_short_put",
                 "scaled_short_call", "scaled_short_put"}
_SPREAD_TYPES = {"bear_put_spread", "bull_put_spread",
                 "bear_call_spread", "bull_call_spread",
                 "risk_reversal_bullish", "risk_reversal_bearish"}
_STRANGLE     = {"short_strangle", "short_straddle", "long_strangle"}
_LONG_ONLY    = {"directional", "scaled_long_call", "scaled_long_put"}

_AVG_DELTA    = 0.30   # 假设典型 OTM delta
_AVG_BETA     = 1.25   # 组合标的平均 beta


def _margin_per_contract(
    strategy: str,
    avg_premium: float,   # 每合约平均权利金（绝对值）
    avg_strike: float,    # 历史平均行权价
    avg_loss: float,      # 历史平均亏损（每个 combo，正值）
) -> float:
    """估算单张合约的保证金/最大风险金额（美元）。"""
    if strategy in _NAKED_SHORT:
        # Reg T: max(20%×strike×100, 10%×strike×100 + premium×100)
        reg_t = max(avg_strike * 0.20 * 100,
                    avg_strike * 0.10 * 100 + avg_premium * 100)
        return max(reg_t, avg_loss) if avg_loss > 0 else reg_t

    if strategy in _SPREAD_TYPES:
        # 有限风险：用历史平均亏损作为最大亏损代理
        return max(avg_loss, avg_premium * 100 * 2) if avg_loss > 0 \
               else avg_premium * 100 * 2

    if strategy in _STRANGLE:
        reg_t = avg_strike * 0.20 * 100 * 1.5   # 两腿，取1.5×
        return max(reg_t, avg_loss) if avg_loss > 0 else reg_t

    if strategy in _LONG_ONLY:
        # 最大亏损 = 权利金
        return max(avg_premium * 100, avg_loss) if avg_loss > 0 \
               else avg_premium * 100

    # fallback
    return max(avg_loss, avg_premium * 200) if avg_loss > 0 else avg_premium * 200


# ── Step 2 + 3 + 4: 仓位计算 ─────────────────────────────────────────────────

def compute_position_sizing(
    conn: sqlite3.Connection,
    acct_id: str,
    kelly_stats: dict[str, "StrategyStats"],
    equity: float,
    bd_current: float,
    bd_limit: float = 3.5,
    today: Optional[datetime.date] = None,
    max_contracts: int = 10,
    min_contracts: int = 1,
) -> list[dict]:
    """
    为每个正期望策略计算 1/3 Kelly 建议张数，并加入 BD 约束。

    返回行列表，每行是一个策略的仓位建议 dict。
    """
    if today is None:
        today = datetime.date.today()

    # 查历史平均权利金 + 行权价（按腿级别）
    raw = conn.execute("""
        SELECT combo_strategy,
               AVG(ABS(COALESCE(open_cash,  0) / NULLIF(ABS(quantity), 0))) AS avg_prem,
               AVG(ABS(strike))                                              AS avg_strike
        FROM option_realized_trades
        WHERE account_id = ?
          AND combo_strategy IS NOT NULL
          AND quantity != 0
        GROUP BY combo_strategy
    """, (acct_id,)).fetchall()
    prem_map   = {r["combo_strategy"]: float(r["avg_prem"]   or 0) for r in raw}
    strike_map = {r["combo_strategy"]: float(r["avg_strike"] or 0) for r in raw}

    bd_headroom = bd_limit - bd_current   # 余量（ratio，e.g. 0.3295）

    rows = []
    for cs, s in sorted(kelly_stats.items(),
                        key=lambda x: (x[1].w_kelly or -9999), reverse=True):
        qk = s.w_kelly / 3 if s.w_kelly is not None else None

        # 负期望 / 无法计算
        if qk is None or qk <= 0:
            rows.append({
                "策略": cs,
                "加权Kelly": _fmt_pct(s.w_kelly),
                "1/3 Kelly": "—",
                "净值占比": "—",
                "最大风险($)": "—",
                "建议张数": "不建议",
                "BD影响": _bd_dir_label(cs),
                "警告": "⚠️ 近期样本不足" if s.low_sample_warning else "",
            })
            continue

        avg_prem   = prem_map.get(cs, 0)
        avg_strike = strike_map.get(cs, 100)
        avg_loss   = s.w_avg_loss

        margin     = _margin_per_contract(cs, avg_prem, avg_strike, avg_loss)
        max_risk   = equity * qk / 100   # qk 是百分比数值，需换算
        kelly_cts  = max(min_contracts,
                         min(max_contracts, math.floor(max_risk / margin))) \
                     if margin > 0 else min_contracts

        # BD 约束
        bd_dir     = _BD_DELTA_DIR.get(cs, 0.0)
        bd_impact  = abs(bd_dir) * _AVG_DELTA * avg_strike * 100 * _AVG_BETA / equity
        bd_warn    = ""
        final_cts  = kelly_cts
        if bd_dir > 0 and bd_impact > 0:          # 此策略会增加 BD
            bd_max = math.floor(bd_headroom / bd_impact) if bd_impact > 0 else max_contracts
            bd_max = max(0, min(max_contracts, bd_max))
            if bd_max < kelly_cts:
                final_cts = bd_max
                bd_warn   = f"⚠️ BD受限，最多{bd_max}张"

        low_warn = "⚠️ 近期样本不足，Kelly仅供参考" if s.low_sample_warning else ""
        warn_combined = "  ".join(filter(None, [bd_warn, low_warn]))

        rows.append({
            "策略":       cs,
            "加权Kelly":  _fmt_pct(s.w_kelly),
            "1/3 Kelly":  _fmt_pct(qk),
            "净值占比":    f"{qk*100:.2f}%",
            "最大风险($)": f"${max_risk:,.0f}",
            "建议张数":    f"{final_cts}张",
            "保证金/张($)": f"${margin:,.0f}",
            "BD影响":     _bd_dir_label(cs),
            "警告":       warn_combined,
        })

    return rows


def _fmt_pct(v: Optional[float]) -> str:
    return f"{v:+.1f}%" if v is not None else "N/A"


def _bd_dir_label(cs: str) -> str:
    d = _BD_DELTA_DIR.get(cs, 0.0)
    if d > 0:   return "↑增加BD"
    if d < 0:   return "↓降低BD"
    return "≈中性"


# ── 格式化输出（供 CLI / Streamlit 复用）────────────────────────────────────
def format_comparison_table(
    stats: dict[str, StrategyStats],
    sort_by: str = "w_kelly",   # "w_kelly" | "raw_kelly" | "n_total"
) -> list[dict]:
    """
    返回排序后的列表，每项是可直接传给 st.dataframe 或 tabulate 的 dict。
    """
    def sort_key(s: StrategyStats):
        v = getattr(s, sort_by, None)
        return v if v is not None else -9999

    rows = []
    for s in sorted(stats.values(), key=sort_key, reverse=True):
        rk = f"{s.raw_kelly:+.1f}%" if s.raw_kelly is not None else "N/A"
        wk = f"{s.w_kelly:+.1f}%"  if s.w_kelly  is not None else "N/A"
        hk = f"{s.w_half_kelly:+.1f}%" if s.w_half_kelly is not None else "N/A"
        rows.append({
            "策略":          s.combo_strategy,
            "原始胜率":       f"{s.raw_win_rate*100:.0f}%",
            "加权胜率":       f"{s.w_win_rate*100:.0f}%",
            "原始Kelly":     rk,
            "加权Kelly":     wk,
            "半Kelly(建议)": hk,
            "近30天":        s.n_30d,
            "近90天":        s.n_90d,
            "全量":          s.n_total,
            "警告":          "⚠️ 近期样本不足，Kelly仅供参考" if s.low_sample_warning else "",
        })
    return rows
