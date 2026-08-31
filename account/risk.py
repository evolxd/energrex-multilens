from __future__ import annotations

import json
import logging
import math
import datetime
from pathlib import Path

from scipy.stats import norm

from account.options import parse_occ


RF_RATE = 0.045
OPTION_MULTIPLIER = 100
DELTA_DRIFT_THRESHOLD = 0.10
VIX_SPIKE_THRESHOLD_PCT = 15.0
DEFAULT_OPTIONS_COST_RATIO_LIMIT = 0.50

# account_monitor.py's _RISK_LIMITS -- duplicated here as the default so
# compute_stress_status()/compute_drawdown_status() are callable standalone
# in tests. account_monitor.py remains the source of truth it should pass in.
DEFAULT_RISK_LIMITS = {
    "max_leverage":          4.0,
    "max_beta_delta_ratio":  3.5,
    "stress_warning":        0.08,
    "stress_de_risk":        0.12,
    "stress_hard_stop":      0.15,
    "drawdown_freeze":       0.20,
    "drawdown_de_risk":      0.30,
}

_log = logging.getLogger("energrex.account.risk")

ZERO_GREEKS = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}


def bs_greeks(
    spot: float,
    strike: float,
    time_to_expiry: float,
    sigma: float,
    option_type: str,
    risk_free_rate: float = RF_RATE,
) -> dict:
    """
    Black-Scholes per-share Greeks.

    `time_to_expiry` is in years and `sigma` is decimal IV, for example 0.48.
    Theta is returned per calendar day. Vega is returned per 1 volatility point.
    """
    if time_to_expiry <= 1e-6 or sigma <= 1e-6 or spot <= 0 or strike <= 0:
        return dict(ZERO_GREEKS)
    try:
        sqrt_t = math.sqrt(time_to_expiry)
        d1 = (
            math.log(spot / strike)
            + (risk_free_rate + 0.5 * sigma**2) * time_to_expiry
        ) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        nd1 = norm.cdf(d1)
        npd1 = norm.pdf(d1)
        is_call = option_type.lower() == "call"

        delta = nd1 if is_call else nd1 - 1.0
        gamma = npd1 / (spot * sigma * sqrt_t)
        nd2_signed = norm.cdf(d2) if is_call else norm.cdf(-d2)
        theta = (
            -(spot * npd1 * sigma) / (2 * sqrt_t)
            + (-1 if is_call else 1)
            * risk_free_rate
            * strike
            * math.exp(-risk_free_rate * time_to_expiry)
            * nd2_signed
        ) / 365
        vega = spot * npd1 * sqrt_t / 100
        return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}
    except Exception:
        return dict(ZERO_GREEKS)


def calculate_option_position_greeks(
    *,
    symbol: str,
    underlying: str,
    option_type: str,
    quantity: int,
    strike: float,
    expiry: str,
    spot_price: float | None,
    current_price: float | None,
    iv: float,
    iv_source: str,
    today: datetime.date | None = None,
) -> dict | None:
    """Calculate one option position's display row and portfolio-level Greeks contribution."""
    today = today or datetime.date.today()
    try:
        expiry_date = datetime.date.fromisoformat(str(expiry))
    except Exception:
        return None

    dte = (expiry_date - today).days
    time_to_expiry = max(dte / 365.0, 1e-6)
    spot = float(spot_price or current_price or strike)
    greeks = bs_greeks(spot, strike, time_to_expiry, iv, option_type)

    pos_delta = greeks["delta"] * quantity
    pos_gamma = greeks["gamma"] * quantity
    pos_theta = greeks["theta"] * quantity * OPTION_MULTIPLIER
    pos_vega = greeks["vega"] * quantity * OPTION_MULTIPLIER

    return {
        "symbol": symbol,
        "underlying": underlying,
        "opt_type": option_type,
        "qty": quantity,
        "strike": strike,
        "expiry": str(expiry),
        "dte": dte,
        "spot": round(spot, 2),
        "iv_pct": round(iv * 100, 1),
        "iv_src": iv_source,
        "bs_delta": round(greeks["delta"], 4),
        "pos_delta": round(pos_delta, 4),
        "pos_gamma": round(pos_gamma, 6),
        "pos_theta": round(pos_theta, 2),
        "pos_vega": round(pos_vega, 2),
        "high_gamma": dte < 21,
        "_raw": {
            "delta": pos_delta,
            "gamma": pos_gamma,
            "theta": pos_theta,
            "vega": pos_vega,
            "bs_delta": greeks["delta"],
        },
    }


def summarize_portfolio_greeks(rows: list[dict]) -> dict:
    """Aggregate position-level Greeks into portfolio totals and helper groupings."""
    sorted_rows = sorted(rows, key=lambda row: (not row["high_gamma"], row["dte"]))
    total_delta = total_gamma = total_theta = total_vega = 0.0
    n_contracts = 0
    by_underlying: dict[str, float] = {}
    iv_src_counts: dict[str, int] = {}

    for row in sorted_rows:
        raw = row.get("_raw") or {}
        total_delta += float(raw.get("delta", row.get("pos_delta", 0)) or 0)
        total_gamma += float(raw.get("gamma", row.get("pos_gamma", 0)) or 0)
        total_theta += float(raw.get("theta", row.get("pos_theta", 0)) or 0)
        total_vega += float(raw.get("vega", row.get("pos_vega", 0)) or 0)
        qty = int(row.get("qty") or 0)
        n_contracts += abs(qty)

        underlying = str(row.get("underlying") or "")
        by_underlying[underlying] = by_underlying.get(underlying, 0.0) + float(
            row.get("pos_delta") or 0
        )

        iv_src = str(row.get("iv_src") or "unknown")
        iv_src_counts[iv_src] = iv_src_counts.get(iv_src, 0) + 1

    avg_delta = total_delta / n_contracts if n_contracts else 0.0
    top_long = (
        max(by_underlying, key=lambda underlying: by_underlying[underlying])
        if by_underlying
        else None
    )
    top_short = (
        min(by_underlying, key=lambda underlying: by_underlying[underlying])
        if by_underlying
        else None
    )

    public_rows = []
    for row in sorted_rows:
        clean = dict(row)
        clean.pop("_raw", None)
        public_rows.append(clean)

    return {
        "rows": public_rows,
        "raw_totals": {
            "delta": total_delta,
            "gamma": total_gamma,
            "theta": total_theta,
            "vega": total_vega,
        },
        "totals": {
            "delta": round(total_delta, 4),
            "gamma": round(total_gamma, 6),
            "theta": round(total_theta, 2),
            "vega": round(total_vega, 2),
            "avg_delta": round(avg_delta, 4),
        },
        "avg_delta": avg_delta,
        "n_contracts": n_contracts,
        "by_und": by_underlying,
        "top_long": top_long,
        "top_short": top_short,
        "iv_src_counts": iv_src_counts,
    }


def delta_drift_trigger(
    previous_total_delta: float | None,
    previous_contracts: int | None,
    current_avg_delta: float,
    threshold: float = DELTA_DRIFT_THRESHOLD,
) -> dict | None:
    """Return a Delta drift trigger when average Delta changes beyond threshold."""
    if not previous_contracts:
        return None
    try:
        prev_avg = float(previous_total_delta or 0) / int(previous_contracts)
    except Exception:
        return None

    drift = current_avg_delta - prev_avg
    if abs(drift) <= threshold:
        return None

    return {
        "level": "HIGH",
        "msg": (
            f"Delta drift {drift:+.3f}/contract (threshold ±{threshold}) "
            f"- avg Delta changed from {prev_avg:+.3f} to {current_avg_delta:+.3f}"
        ),
        "drift": drift,
        "previous_avg_delta": prev_avg,
        "current_avg_delta": current_avg_delta,
        "threshold": threshold,
    }


def vix_spike_trigger(vix_snapshot: dict, threshold_pct: float = VIX_SPIKE_THRESHOLD_PCT) -> dict | None:
    """Return a trigger when VIX daily percentage change exceeds threshold."""
    try:
        change_pct = vix_snapshot.get("change_pct")
        if change_pct is None or float(change_pct) <= threshold_pct:
            return None
        change_pct = float(change_pct)
    except Exception:
        return None

    return {
        "level": "CRITICAL",
        "msg": f"VIX daily move {change_pct:+.1f}% exceeds {threshold_pct:.1f}%",
        "change_pct": change_pct,
        "threshold_pct": threshold_pct,
        "vix": vix_snapshot.get("vix"),
    }


def load_options_cost_ratio_limit(
    config_path: Path | str,
    *,
    default: float = DEFAULT_OPTIONS_COST_RATIO_LIMIT,
) -> float:
    """Read `options_cost_ratio_limit` from a JSON override file.

    A missing file is the normal case (no override configured) and returns
    `default` silently. A file that exists but fails to parse is a real
    configuration error, not a missing-override situation -- it's logged
    rather than swallowed, so a broken override doesn't silently masquerade
    as "no override, using default."
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return float(cfg.get("options_cost_ratio_limit", default))
    except FileNotFoundError:
        return default
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "portfolio_config at %s exists but failed to load (%s); "
            "falling back to default limit=%s", config_path, exc, default,
        )
        return default


def compute_twr_drawdown(
    nav_by_date: dict[str, float],
    cashflow_by_date: dict[str, float] | None = None,
) -> float:
    """Time-weighted-return max drawdown magnitude, excluding cash flows.

    Deposits/withdrawals on a given date are backed out of that day's return
    so a large deposit doesn't register as a gain (and a withdrawal doesn't
    register as a loss). Returns a positive magnitude (0.20 means -20%), or
    0.0 when fewer than two NAV observations exist.
    """
    cashflow_by_date = cashflow_by_date or {}
    dates = sorted(nav_by_date)
    drawdown = 0.0
    if len(dates) < 2:
        return drawdown

    twr = peak = 1.0
    prev = float(nav_by_date[dates[0]])
    for d in dates[1:]:
        nav = float(nav_by_date[d])
        cf = cashflow_by_date.get(d, 0.0)
        r = (nav - prev - cf) / prev if prev > 0 else 0.0
        twr *= (1.0 + r)
        if twr > peak:
            peak = twr
        dd = twr / peak - 1
        if dd < -drawdown:
            drawdown = abs(dd)
        prev = nav
    return drawdown


def compute_portfolio_stress_test(
    stocks: list,
    options: list,
    *,
    underlying_prices: dict[str, float],
    iv_map: dict[str, dict],
    beta_map: dict[str, float],
    today: datetime.date | None = None,
) -> dict:
    """-10%/-20% underlying-shock stress test across stock + option positions.

    `stocks` rows need symbol/quantity/market_value; `options` rows need
    symbol/quantity/current_price/market_value/strike/expiry (dict or
    sqlite3.Row -- both support `row["field"]`). Options use Black-Scholes
    Greeks (spot from `underlying_prices`, IV from `iv_map`, falling back to
    30% IV when unknown) rather than any Greeks stored on the row, so this
    never depends on a possibly-NULL DB column. Spread legs on the same
    underlying naturally net out in delta-adjusted notional exposure.

    stress_pnl = qty * multiplier * (delta*dS + 0.5*gamma*dS^2) + qty * multiplier * vega * iv_shock_pts
    -10% shock: dS = -10%*S, iv_shock = +8 vol points. -20%: dS = -20%*S, +16 points.
    """
    today = today or datetime.date.today()
    gross = delta_notional = beta_delta = 0.0
    theta_per_day = vega_per_pt = gamma_total = 0.0
    stress_10 = stress_20 = 0.0
    nearest_expiry_date: datetime.date | None = None
    nearest_expiry_sym = ""

    for s in stocks:
        sym = str(s["symbol"] or "").upper()
        q = float(s["quantity"] or 0)
        mv = float(s["market_value"] or 0)
        s_price = mv / q if q else 0.0
        b = beta_map.get(sym, 1.0)
        gross += abs(mv)
        delta_notional += abs(mv)
        beta_delta += q * s_price * 1.0 * b
        stress_10 += q * (-0.10 * s_price)
        stress_20 += q * (-0.20 * s_price)

    for o in options:
        sym = str(o["symbol"] or "").upper()
        parsed = parse_occ(sym)
        und = parsed["root"] if parsed else sym
        b = beta_map.get(und, 1.0)
        q = float(o["quantity"] or 0)
        mult = 100.0
        price = float(o["current_price"] or 0)
        mv = float(o["market_value"] or 0)
        S = underlying_prices.get(und, 0.0)

        d = g = th = vg = 0.0
        if parsed and S > 0:
            K = float(o["strike"] or 0) or parsed["strike"]
            opt_type = parsed["option_type"]
            iv_entry = iv_map.get(und)
            iv = iv_entry["iv"] if iv_entry else 0.30
            try:
                exp_date = datetime.date.fromisoformat(str(o["expiry"]))
            except Exception:
                exp_date = datetime.date.fromisoformat(parsed["expiry"])
            dte = max(0, (exp_date - today).days)
            if dte > 0 and K > 0:
                greeks = bs_greeks(S, K, dte / 365.0, iv, opt_type)
                d, g, th, vg = greeks["delta"], greeks["gamma"], greeks["theta"], greeks["vega"]

        gross += abs(q * mult * S) if S > 0 else abs(q * mult * price)
        if S > 0 and abs(d) > 0.001:
            delta_notional += abs(q * mult * d * S)
        else:
            # Delta didn't resolve (deep OTM / expired) -- use market value
            # rather than overstate exposure.
            delta_notional += abs(mv) if abs(mv) > 0 else abs(q * mult * price)
        theta_per_day += q * mult * th
        vega_per_pt += q * mult * vg
        gamma_total += abs(q) * g * mult

        if parsed:
            try:
                exp = datetime.date.fromisoformat(parsed["expiry"])
                if nearest_expiry_date is None or exp < nearest_expiry_date:
                    nearest_expiry_date = exp
                    nearest_expiry_sym = und
            except ValueError:
                pass

        if S > 0:
            beta_delta += q * mult * d * S * b
            ds10, ds20 = -0.10 * S, -0.20 * S
            stress_10 += q * mult * (d * ds10 + 0.5 * g * ds10 * ds10) + q * mult * vg * 8
            stress_20 += q * mult * (d * ds20 + 0.5 * g * ds20 * ds20) + q * mult * vg * 16

    return {
        "gross_notional": gross,
        "delta_notional": delta_notional,
        "beta_delta": beta_delta,
        "theta_per_day": theta_per_day,
        "vega_per_pt": vega_per_pt,
        "gamma_total": gamma_total,
        "stress_10": stress_10,
        "stress_20": stress_20,
        "nearest_expiry_date": nearest_expiry_date,
        "nearest_expiry_sym": nearest_expiry_sym,
    }


def classify_stress_status(
    stress_10_ratio: float | None, limits: dict | None = None
) -> str:
    """GREEN / YELLOW_WARNING / ORANGE_DE_RISK / RED_HARD_STOP from -10% stress ratio."""
    limits = limits or DEFAULT_RISK_LIMITS
    magnitude = abs(stress_10_ratio) if stress_10_ratio else 0.0
    if magnitude >= limits["stress_hard_stop"]:
        return "RED_HARD_STOP"
    if magnitude >= limits["stress_de_risk"]:
        return "ORANGE_DE_RISK"
    if magnitude >= limits["stress_warning"]:
        return "YELLOW_WARNING"
    return "GREEN"


def classify_drawdown_status(drawdown: float, limits: dict | None = None) -> str:
    """GREEN / ORANGE_FREEZE_NEW_RISK / RED_MANDATORY_DE_RISK from TWR drawdown magnitude."""
    limits = limits or DEFAULT_RISK_LIMITS
    if drawdown >= limits["drawdown_de_risk"]:
        return "RED_MANDATORY_DE_RISK"
    if drawdown >= limits["drawdown_freeze"]:
        return "ORANGE_FREEZE_NEW_RISK"
    return "GREEN"


def score_label(score: float | None) -> str:
    """Format an AI score (0-100) with a quality-band emoji, or '' when unknown."""
    if score is None:
        return ""
    if score >= 80:
        return f"{score:.0f} ⭐"
    if score >= 65:
        return f"{score:.0f} ✅"
    if score >= 50:
        return f"{score:.0f} 🟡"
    if score >= 35:
        return f"{score:.0f} ⚠️"
    return f"{score:.0f} 🔴"


_RISK_PRIORITY_LABEL = {
    "CRITICAL": "🔴 紧急",
    "HIGH":     "🟠 高",
    "MEDIUM":   "🟡 中",
    "LOW":      "🟢 低",
}


def build_recommendations(
    *,
    portfolios: list[dict],
    risk_snapshot: dict,
    iv_regime: dict,
    ai_scores: dict,
    risk_limits: dict | None = None,
) -> list[dict]:
    """Turn already-identified spread portfolios + account state into the
    recommendations table (one row per spread, plus a macro-hedge suggestion
    and up to 3 new-opportunity candidates).

    Pure given its inputs -- `portfolios`/`risk_snapshot`/`iv_regime`/
    `ai_scores` are each produced by DB reads and market-data calls upstream
    (account_monitor.py's `_build_spread_portfolios`/`_compute_risk_snapshot`/
    `_compute_iv_regime`/`_load_ai_scores`); this function only decides what
    to recommend given those results.
    """
    limits = risk_limits or DEFAULT_RISK_LIMITS
    iv_status = iv_regime.get("status", "NO_DATA")
    stress10 = risk_snapshot.get("stress_10_ratio") or 0.0
    leverage = risk_snapshot.get("leverage_delta") or risk_snapshot.get("leverage") or 0.0
    held_underlyings = {p["underlying"] for p in portfolios}

    recs: list[dict] = []
    idx = 1

    # 1. Portfolio-level recommendations
    for p in portfolios:
        und = p["underlying"]
        rl = p["risk_level"]
        priority = _RISK_PRIORITY_LABEL.get(rl, "🟡 中")
        score = ai_scores.get(und)
        ptype = p["type"]
        pnl = p.get("current_pnl") or 0
        dte_v = p.get("dte")
        pnl_pct = p.get("pnl_pct")

        action_parts: list[str] = [p["recommendation"]]

        if score is not None:
            action_parts.append(f"AI评分 {score_label(score)}")

        is_short_vega = "Credit" in ptype or "Bear Call" in ptype or "Bull Put" in ptype
        if is_short_vega and iv_status in ("HIGH_IV", "EXTREME_IV"):
            action_parts.append(f"IV Regime={iv_status}，卖权环境有利，可持有至 80% 利润后平仓")
        elif "Debit" in ptype or "Bull Call" in ptype or "Bear Put" in ptype:
            if iv_status == "LOW_IV":
                action_parts.append("低 IV 环境，买权价差成本低，有利于持有")

        if abs(stress10) >= limits["stress_hard_stop"]:
            action_parts.append("⚠️ 组合压力超限(≥15%)，优先减仓")

        if rl == "LOW" and pnl_pct is not None:
            if pnl_pct >= 50:
                action_parts.append("已盈利50%+，可考虑提前平仓锁利")
            else:
                action_parts.append("建议继续持有至75%利润或到期2周前评估")
        elif rl == "MEDIUM" and "Diagonal" in ptype:
            action_parts.append("关注近月腿到期节点，提前15天制定展期方案")

        trigger_parts = [f"组合类型={ptype}", f"风险={rl}"]
        if dte_v is not None:
            trigger_parts.append(f"DTE={dte_v}天")
        if pnl != 0:
            trigger_parts.append(f"盈亏=${pnl:+,.0f}")

        recs.append({
            "序号":   idx,
            "优先级": priority,
            "标的":   und,
            "组合":   ptype,
            "手数":   p.get("spread_qty", 0),
            "到期":   p.get("expiry", "—"),
            "DTE":    dte_v if dte_v is not None else "—",
            "AI评分": score_label(score) if score else "—",
            "行动建议": " ；".join(action_parts),
            "触发原因": " | ".join(trigger_parts),
            "最大盈利": f"${p['max_profit']:,.0f}" if p.get("max_profit") is not None else "—",
            "最大亏损": f"${p['max_loss']:,.0f}"  if p.get("max_loss")   is not None else "无限",
            "当前盈亏": f"${pnl:+,.0f}",
            "_sim_action": {
                "type": "close_underlying", "underlying": und,
                "label": f"关闭 {und} 全部期权持仓",
            },
        })
        idx += 1

    # 2. Portfolio stress hedge suggestion
    if abs(stress10) >= limits["stress_de_risk"]:
        recs.append({
            "序号":   idx, "优先级": "🟠 高", "标的": "QQQ",
            "组合":   "宏观对冲（建议）", "手数": 0, "到期": "—", "DTE": "—",
            "AI评分": score_label(ai_scores.get("QQQ")) if ai_scores.get("QQQ") else "—",
            "行动建议": "建议买入 QQQ Put Debit Spread 作宏观对冲，最大亏损 = 净权利金",
            "触发原因": f"-10% 压力损失 {stress10*100:+.1f}% ≥ {limits['stress_de_risk']*100:.0f}% 阈值",
            "最大盈利": "—", "最大亏损": "= 权利金", "当前盈亏": "—",
            "_sim_action": {"type": "qqq_hedge", "label": "执行 QQQ Put Spread 对冲（方案A）"},
        })
        idx += 1

    # 3. New opportunity candidates: high AI score + IV regime fit + risk headroom
    if not risk_snapshot.get("error") and leverage < limits["max_leverage"] * 0.75:
        candidates = [(t, s) for t, s in ai_scores.items()
                      if s >= 70 and t not in held_underlyings]
        for t, s in sorted(candidates, key=lambda x: -x[1])[:3]:
            if iv_status in ("HIGH_IV", "EXTREME_IV"):
                strat = "卖出 Put Credit Spread（高 IV 收权利金，限定风险）"
                reason = f"IV Regime={iv_status} 适合卖权；{t} AI评分={s:.0f}"
            elif iv_status == "LOW_IV":
                strat = "买入 Call Debit Spread（低 IV 低成本买权）"
                reason = f"IV Regime=LOW_IV 适合买权；{t} AI评分={s:.0f}"
            else:
                strat = "观望或小仓 Bull Call Spread（中性 IV）"
                reason = f"{t} AI评分={s:.0f}，IV 正常区间"
            recs.append({
                "序号":   idx, "优先级": "🟢 机会", "标的": t,
                "组合":   "新开仓候选", "手数": 0, "到期": "—", "DTE": "—",
                "AI评分": score_label(s),
                "行动建议": strat,
                "触发原因": reason,
                "最大盈利": "—", "最大亏损": "= 权利金", "当前盈亏": "—",
                "_sim_action": {"type": "no_sim",
                                "label": f"新开仓 {t}（需指定具体参数，暂不支持模拟）"},
            })
            idx += 1

    return recs


def compute_exit_analysis(
    portfolios: list[dict],
    *,
    net_equity: float,
    underlying_prices: dict[str, float],
    today: datetime.date | None = None,
) -> dict:
    """Layer risk/time/direction analysis onto each already-identified spread
    portfolio and rank by urgency, plus a portfolio-level summary.

    Pure given `portfolios` (from account_monitor.py's
    `_build_spread_portfolios`), `net_equity` (from the latest account
    balance), and `underlying_prices` (from a market-data fetch) -- this
    function only decides urgency/action/thesis-broken status from what it's
    handed, it does not fetch anything itself.
    """
    if not portfolios:
        return {"portfolios": [], "summary": {}}

    enriched_list = []

    for port in portfolios:
        und = port["underlying"]
        und_price = underlying_prices.get(und)

        cost_basis = abs(port.get("max_loss") or port.get("net_total") or 0)
        equity_pct = round(cost_basis / net_equity * 100, 1) if net_equity > 0 else 0.0

        pnl_pct = port.get("pnl_pct")
        if pnl_pct is None and cost_basis > 0.01:
            pnl_pct = round(port["current_pnl"] / cost_basis * 100, 1)

        min_dte = port["dte"]
        short_legs = [l for l in port["legs"] if (l.get("qty") or 0) < 0]
        short_dtes = [l["dte"] for l in short_legs]
        min_short_dte = min(short_dtes) if short_dtes else None
        has_short = bool(short_legs)

        # ── Thesis broken detection ──────────────────────────────
        thesis_broken = False
        thesis_note = ""
        if und_price:
            ptype = port.get("type", "")
            high_k = port.get("high_strike") or 0
            low_k = port.get("low_strike") or 0
            if "Bear Put" in ptype and high_k and und_price > high_k:
                thesis_broken = True
                otm = (und_price - high_k) / high_k * 100
                thesis_note = (f"{und} 现价 ${und_price:.2f} 高于价差上沿 "
                               f"${high_k:.0f}（超出 {otm:.1f}%），看跌假设已被推翻")
            elif "Bull Call" in ptype and low_k and und_price < low_k:
                thesis_broken = True
                otm = (low_k - und_price) / low_k * 100
                thesis_note = (f"{und} 现价 ${und_price:.2f} 低于价差下沿 "
                               f"${low_k:.0f}（偏离 {otm:.1f}%），看涨假设受挫")
            elif "Bear Call" in ptype and high_k and und_price > high_k:
                thesis_broken = True
                otm = (und_price - high_k) / high_k * 100
                thesis_note = (f"{und} 现价 ${und_price:.2f} 高于上沿 "
                               f"${high_k:.0f}（超出 {otm:.1f}%），空头承压")
            elif "Bull Put" in ptype and low_k and und_price < low_k:
                thesis_broken = True
                otm = (low_k - und_price) / low_k * 100
                thesis_note = (f"{und} 现价 ${und_price:.2f} 低于下沿 "
                               f"${low_k:.0f}（偏离 {otm:.1f}%），多头压力加大")
            elif "Naked Long Put" in ptype:
                strike_k = port["legs"][0].get("strike") or 0
                if strike_k and und_price > strike_k * 1.1:
                    otm = (und_price - strike_k) / strike_k * 100
                    thesis_broken = True
                    thesis_note = (f"{und} 现价 ${und_price:.2f} 高于行权价 "
                                   f"${strike_k:.0f}（{otm:.0f}% OTM），看跌假设未兑现")
            elif "Naked Long Call" in ptype:
                strike_k = port["legs"][0].get("strike") or 0
                if strike_k and und_price < strike_k * 0.9:
                    otm = (strike_k - und_price) / strike_k * 100
                    thesis_broken = True
                    thesis_note = (f"{und} 现价 ${und_price:.2f} 低于行权价 "
                                   f"${strike_k:.0f}（{otm:.0f}% OTM），看涨动能不足")

        # ── Urgency score (for sorting) ──────────────────────────
        urgency = 0
        if min_dte is not None:
            if min_dte <= 7:
                urgency += 5
            elif min_dte <= 14:
                urgency += 3
            elif min_dte <= 21:
                urgency += 1
        if pnl_pct is not None:
            if pnl_pct <= -60:
                urgency += 6
            elif pnl_pct <= -45:
                urgency += 4
            elif pnl_pct <= -30:
                urgency += 2
            elif pnl_pct >= 45:
                urgency += 2
            elif pnl_pct >= 30:
                urgency += 1
        if equity_pct > 30:
            urgency += 3
        elif equity_pct > 20:
            urgency += 1
        if thesis_broken:
            urgency += 4

        # ── Action label & color ─────────────────────────────────
        _p = pnl_pct or 0
        if min_dte is not None and min_dte <= 7:
            action, action_color = "🚨 立即处理", "#FF4B4B"
        elif _p <= -60:
            action, action_color = "🛑 止损", "#FF4B4B"
        elif thesis_broken and _p <= -20:
            action, action_color = "📉 重新评估", "#FF4B4B"
        elif pnl_pct is not None and pnl_pct >= 50:
            action, action_color = "⚡ 止盈", "#00C853"
        elif pnl_pct is not None and pnl_pct <= -50 and (min_dte or 999) < 30:
            action, action_color = "🛑 止损", "#FF4B4B"
        elif has_short and min_short_dte is not None and min_short_dte <= 21:
            action, action_color = "🔄 滚仓", "#FFB700"
        elif thesis_broken:
            action, action_color = "⚠️ 方向反转", "#FFB700"
        elif pnl_pct is not None and pnl_pct <= -40:
            action, action_color = "👀 关注", "#FFB700"
        else:
            action, action_color = "✅ 持有", "#6B6B6B"

        # ── Why text (layered explanation) ───────────────────────
        why_parts = []

        if thesis_broken and thesis_note:
            why_parts.append(f"【方向】{thesis_note}")

        if pnl_pct is not None:
            dist_stop = pnl_pct - (-50)
            dist_tp = 50 - pnl_pct
            if pnl_pct >= 50:
                why_parts.append("【盈亏】已触发止盈线 +50%，建议锁利或展期")
            elif pnl_pct <= -50:
                why_parts.append(f"【盈亏】已触发止损线，亏损 {abs(pnl_pct):.0f}%")
            elif dist_stop < 15:
                why_parts.append(f"【盈亏】亏损 {pnl_pct:+.0f}%，距止损线 -50% 仅剩 {dist_stop:.0f}%，需密切关注")
            elif dist_tp < 12:
                why_parts.append(f"【盈亏】盈利 {pnl_pct:+.0f}%，距止盈线 +50% 还差 {dist_tp:.0f}%")
            else:
                why_parts.append(f"【盈亏】{pnl_pct:+.0f}%（止盈 +50% / 止损 -50%，当前安全区间）")

        if equity_pct > 25:
            why_parts.append(f"【风险】持仓成本占净值 {equity_pct:.0f}%，集中度偏高（建议单仓 ≤25%净值）")
        elif equity_pct > 15:
            why_parts.append(f"【风险】持仓成本占净值 {equity_pct:.0f}%")

        if min_dte is not None:
            if min_dte <= 7:
                why_parts.append(f"【时间】DTE={min_dte}天，时间价值极速衰减，立即决策")
            elif min_dte <= 14:
                why_parts.append(f"【时间】DTE={min_dte}天，Theta加速衰减，建议本周决策")
            elif min_dte <= 21:
                why_parts.append(f"【时间】DTE={min_dte}天，建议2周内决策")

        if not why_parts:
            why_parts.append("各项指标正常，无需立即行动")

        enriched_list.append({
            **port,
            "cost_basis":    cost_basis,
            "equity_pct":    equity_pct,
            "pnl_pct":       pnl_pct,
            "min_dte":       min_dte,
            "min_short_dte": min_short_dte,
            "has_short":     has_short,
            "und_price":     und_price,
            "thesis_broken": thesis_broken,
            "thesis_note":   thesis_note,
            "urgency":       urgency,
            "action":        action,
            "action_color":  action_color,
            "why":           " ；".join(why_parts),
        })

    enriched_list.sort(key=lambda x: -x["urgency"])

    total_cost = sum(p["cost_basis"] for p in enriched_list)
    cost_pct = round(total_cost / net_equity * 100, 1) if net_equity > 0 else 0.0

    by_und: dict[str, float] = {}
    for p in enriched_list:
        by_und[p["underlying"]] = by_und.get(p["underlying"], 0) + p["cost_basis"]
    top_unds = sorted(by_und.items(), key=lambda x: -x[1])[:3]

    return {
        "portfolios": enriched_list,
        "summary": {
            "total_cost": round(total_cost, 2),
            "net_equity": net_equity,
            "cost_pct":   cost_pct,
            "top_unds":   top_unds,
            "n_broken":   sum(1 for p in enriched_list if p["thesis_broken"]),
        },
    }
