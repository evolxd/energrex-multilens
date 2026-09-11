"""
account/risk_signals.py — 门④/门⑤ 纪律架构 v2 的信号扫描函数

设计见 docs/DISCIPLINE_AND_REVIEW_ARCHITECTURE.md。这里的每个函数都保持
"纯"——不自己拉数据，输入是调用方（_cascade.py）算好/查好传进来的。输出
统一是 {"symbol", "dimension", "detail"} 的列表，直接喂
account/discipline.py::record_and_resolve_signals。

v1 已知妥协（都在函数 docstring 里标了）：
  - "开仓恶化breach" / "无case交易" 只按 BUY 当"加仓/开仓"处理——没有持仓
    状态历史，分不清一笔 SELL 是平仓还是开空
  - "偏离Kelly" v1 只查"负收缩Kelly的策略类型还去交易"，不查"超配多少张"
    （Kelly 给的是资金比例，换算成张数要 max_loss_per_spread，暂时没有干净
    的来源）
"""
from __future__ import annotations

import datetime as _dt
import re as _re

_OCC_RE = _re.compile(r"^([A-Z]{1,6})\d{6}[CP]\d{8}$")


def underlying_of(symbol: str) -> str:
    """期权 OCC 代码 → 标的；普通股票/ETF 代码原样返回。"""
    s = (symbol or "").strip().upper()
    m = _OCC_RE.match(s)
    return m.group(1) if m else s


# ── 硬约束类 breach ────────────────────────────────────────────
_LIMIT_KEY_TO_DIMENSION = {
    "single_stock_max":  "单票超限",
    "chain_max":         "集中度超限",
    "cash_floor_min":    "现金底线",
    "liquidity_days_max": "流动性天数",
}


def hard_constraint_signals(breaches_list: list[dict]) -> list[dict]:
    """scoring.position_exposure.breaches() 的输出 → 信号列表。

    breaches_list 每项：{key, label, limit, reading, overshoot, detail}
    （detail 是超配的标的/产业链名，cash_floor 没有 detail）。
    """
    out: list[dict] = []
    for b in breaches_list or []:
        dim = _LIMIT_KEY_TO_DIMENSION.get(b.get("key"))
        if dim is None:
            continue
        sym = (b.get("detail") or "").strip().upper() or "PORTFOLIO"
        out.append({
            "symbol": sym, "dimension": dim,
            "detail": f"{b.get('label','')}：当前 {b.get('reading')}，"
                      f"限额 {b.get('limit')}，超出 {b.get('overshoot')}",
        })
    return out


# ── 风险快照类（BD/杠杆/压力测试/强制去风险） ──────────────────
def risk_snapshot_signals(
    snap: dict | None,
    *,
    max_bd: float = 3.5,
    max_leverage: float = 4.0,
    stress_redline: float = 0.15,
) -> list[dict]:
    """account_monitor._compute_risk_snapshot() 的返回值 → 信号列表。

    snap 关键字段（都是小数口径）：beta_delta_ratio、leverage、
    leverage_delta、stress_10_ratio、stress_20_ratio、drawdown_status。
    """
    if not snap:
        return []
    out: list[dict] = []

    bd = snap.get("beta_delta_ratio")
    if bd is not None and abs(bd) > max_bd:
        out.append({"symbol": "PORTFOLIO", "dimension": "杠杆超限",
                    "detail": f"Beta-Delta 比率 {bd*100:.0f}% 超硬上限 {max_bd*100:.0f}%"})
    else:
        lev = snap.get("leverage_delta") or snap.get("leverage")
        if lev is not None and abs(lev) > max_leverage:
            out.append({"symbol": "PORTFOLIO", "dimension": "杠杆超限",
                        "detail": f"Delta 杠杆 {lev:.1f}x 超硬上限 {max_leverage:.1f}x"})

    # 压力测试：-20% 情景损失超过红线（占净值）
    s20 = snap.get("stress_20_ratio")
    if s20 is not None and abs(s20) >= stress_redline:
        out.append({"symbol": "PORTFOLIO", "dimension": "压力测试超红线",
                    "detail": f"市场跌 20% 情景损失 {abs(s20)*100:.1f}% 净值，超红线 {stress_redline*100:.0f}%"})

    if snap.get("drawdown_status") == "RED_MANDATORY_DE_RISK":
        out.append({"symbol": "PORTFOLIO", "dimension": "强制去风险",
                    "detail": "账户回撤已超阈值，drawdown_status = RED_MANDATORY_DE_RISK"})

    return out


# ── 门④：事后从 transactions 检测的三类违规 ──────────────────────
def traded_signals(
    new_trades: list[dict],
    *,
    cases_on_file: set[str],
    circuit_symbols: set[str],
    hard_breach_dates: set[_dt.date],
    negative_kelly_strategies: set[str],
    strategy_of: "callable | None" = None,
) -> list[dict]:
    """门④ #12/#13/#14 + 熔断票交易。

    new_trades：自上次同步以来 transactions 里的新行，每项至少
        {"symbol", "trade_date"(date), "type"(BUY/SELL/...), "quantity"}。
    cases_on_file：在"误价研究"页建过 case 的标的集合。
    circuit_symbols：评分被熔断压制、系统标"需人工复核"的标的集合。
    hard_breach_dates：这些日期上有任意硬约束在超限（调用方从
        discipline_signals 的 open 区间算出）。
    negative_kelly_strategies：收缩 Kelly ≤ 0 的 combo_strategy 集合。
    strategy_of：可选，symbol → combo_strategy 的映射函数（判 #13 用）。

    v1 妥协：只把 BUY 当"开仓/加仓"。返回的信号 first_seen 由调用方按
    trade_date 记（这里只出 symbol/dimension/detail，record 那边用 today
    ——所以调用方要把 today 传成 trade_date，或事后修正。见 Stage 3 接法）。
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for t in new_trades or []:
        raw_sym = str(t.get("symbol") or "").strip().upper()
        if not raw_sym:
            continue
        und = underlying_of(raw_sym)
        is_buy = str(t.get("type") or "").upper() == "BUY"
        td = t.get("trade_date")
        td_s = td.isoformat() if hasattr(td, "isoformat") else str(td or "")

        def _add(dim: str, detail: str, key_sym: str = und):
            k = (dim, key_sym)
            if k not in seen:
                seen.add(k)
                out.append({"symbol": key_sym, "dimension": dim, "detail": detail})

        if is_buy and und not in cases_on_file:
            _add("无case交易", f"{td_s} 买入 {und}，误价研究页没有它的 case")

        if is_buy and und in circuit_symbols:
            _add("熔断票交易", f"{td_s} 买入 {und}，该票评分被熔断压制、系统标需人工复核")

        if is_buy and td in hard_breach_dates:
            _add("开仓恶化breach", f"{td_s} 买入 {und}，当日已有硬约束在超限")

        if is_buy and strategy_of is not None:
            strat = strategy_of(raw_sym)
            if strat and strat in negative_kelly_strategies:
                _add("偏离Kelly", f"{td_s} 在 {strat}（收缩Kelly≤0）上开仓")

    return out


# ── 回调模块（MAJOR/WATCH） ────────────────────────────────────
def _atr14(bars) -> float | None:
    """bars: 有 high/low/close 列的 DataFrame（按日期升序）。返回 ATR14。"""
    if bars is None or len(bars) < 15:
        return None
    h, l, c = bars["high"], bars["low"], bars["close"]
    pc = c.shift(1)
    tr = (h - l).abs()
    tr = tr.combine((h - pc).abs(), max).combine((l - pc).abs(), max)
    return float(tr.tail(14).mean())


def pullback_signals(
    bars_by_symbol: dict,
    *,
    atr_mult_drawdown: float = 2.0,
    atr_mult_fastdrop: float = 1.5,
    lookback: int = 20,
    major_min_checks: int = 3,
) -> dict[str, dict]:
    """每个持仓标的的日线 → 回调分档。

    bars_by_symbol：{标的: DataFrame(open/high/low/close/volume, 按日升序)}。
    返回 {标的: {"tier": "MAJOR"|"WATCH"|None, "checks": [命中的检查名]}}。

    5 个检查（设计文档 §7）：距20日高点回撤≥2×ATR / 收盘跌破20DMA /
    10日ROC由正转负 / 3日跌≥1.5×ATR / （放量——v1 不做）。
    MAJOR = 命中 ≥3 项，或"下跌速度"单独触发。
    """
    out: dict[str, dict] = {}
    for sym, bars in (bars_by_symbol or {}).items():
        if bars is None or len(bars) < max(lookback + 1, 51):
            out[sym] = {"tier": None, "checks": []}
            continue
        close = bars["close"]
        px = float(close.iloc[-1])
        atr = _atr14(bars)
        checks: list[str] = []
        fastdrop = False

        recent_high = float(close.tail(lookback).max())
        if atr and (recent_high - px) >= atr_mult_drawdown * atr:
            checks.append("距20日高点回撤")

        # 跌破20日均线：今天收盘在 MA20 下方，昨天收盘还在 MA20 上方（新破位）
        ma20_now  = float(close.tail(20).mean())
        ma20_prev = float(close.iloc[-21:-1].mean())
        if px < ma20_now and float(close.iloc[-2]) >= ma20_prev:
            checks.append("跌破20日均线")

        # 10日动能：今天的10日ROC为负，前一根还是非负（刚翻负）
        roc10_now  = px / float(close.iloc[-11]) - 1
        roc10_prev = float(close.iloc[-2]) / float(close.iloc[-12]) - 1
        if roc10_now < 0 <= roc10_prev:
            checks.append("10日动能翻负")

        # 下跌速度：3个交易日内跌幅 ≥ 1.5×ATR
        drop3 = float(close.iloc[-4]) - px
        if atr and drop3 >= atr_mult_fastdrop * atr:
            checks.append("3日快速下跌")
            fastdrop = True

        if len(checks) >= major_min_checks or fastdrop:
            tier = "MAJOR"
        elif checks:
            tier = "WATCH"
        else:
            tier = None
        out[sym] = {"tier": tier, "checks": checks}
    return out


def compound_zhiying_pullback_signals(
    pnl_dte_signals: list[dict],
    pullback_result: dict[str, dict],
) -> list[dict]:
    """止盈信号 × MAJOR回调 → "止盈回调复合"（高权重，设计文档 §1 #15）。

    pnl_dte_signals：discipline.scan_pnl_dte_signals() 的输出（含"止盈纪律"项）。
    pullback_result：pullback_signals() 的输出。
    """
    majors = {s for s, v in (pullback_result or {}).items() if v.get("tier") == "MAJOR"}
    out: list[dict] = []
    for sig in pnl_dte_signals or []:
        if sig.get("dimension") != "止盈纪律":
            continue
        und = underlying_of(sig.get("symbol", ""))
        if und in majors:
            out.append({
                "symbol": sig["symbol"], "dimension": "止盈回调复合",
                "detail": f"{sig.get('detail','浮盈达标')}，且 {und} 出重大回调——必须处理",
            })
    return out
