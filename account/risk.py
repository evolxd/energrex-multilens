from __future__ import annotations

import json
import logging
import math
import datetime
from pathlib import Path
from typing import Literal, Protocol, TypedDict

from scipy.stats import norm

from account.hedge_governance import evaluate_protective_put_hedges
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
    "stress_20_hard_stop":   0.25,
    "drawdown_freeze":       0.20,
    "drawdown_de_risk":      0.30,
}

_log = logging.getLogger("energrex.account.risk")

ZERO_GREEKS = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

#: 压力测试默认的大盘冲击阶梯。-10/-20 是两条限额盯的档位，-5/-15 是为了
#: 看出损失曲线的形状——保护腿挂得远的组合在这两个点上会露出"前段有洞"。
STRESS_SHOCKS: tuple[float, ...] = (-0.05, -0.10, -0.15, -0.20)

#: 每 10% 大盘跌幅对应的隐含波动率上升（个 vol 点）。沿用此前写死的经验值
#: （跌10%→+8、跌20%→+16），没有重新校准。
VOL_SHOCK_PTS_PER_10PCT = 8.0


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


def bs_price(
    spot: float,
    strike: float,
    time_to_expiry: float,
    sigma: float,
    option_type: str,
    risk_free_rate: float = RF_RATE,
) -> float:
    """Black-Scholes European option price (a $ price per share, not Greeks).

    2026-09-10 审计 F-01：压力测试原来用 delta+0.5*gamma*dS² 的泰勒展开去
    近似大幅冲击（-10%/-20%）下的期权盈亏——二阶展开只在冲击幅度小的时候
    准，在这个量级上跟真实定价会有明显误差，尤其是深度虚值期权（gamma
    在冲击后剧烈变化，泰勒展开完全跟不上）。full BS repricing 直接算冲击
    前后的模型价格差，不管冲击多大都准（只要 BS 假设本身成立）。

    退化到 intrinsic value 的口径跟 bs_greeks 的 ZERO_GREEKS 兜底、
    _bs_put_price 的兜底一致——没有时间价值可算的时候（T/sigma/S 非正）
    直接给内在价值，不是 0。
    """
    is_call = option_type.lower() == "call"
    if time_to_expiry <= 1e-6 or sigma <= 1e-6 or spot <= 0 or strike <= 0:
        return max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)
    try:
        sqrt_t = math.sqrt(time_to_expiry)
        d1 = (
            math.log(spot / strike)
            + (risk_free_rate + 0.5 * sigma**2) * time_to_expiry
        ) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        disc_k = strike * math.exp(-risk_free_rate * time_to_expiry)
        if is_call:
            return spot * norm.cdf(d1) - disc_k * norm.cdf(d2)
        return disc_k * norm.cdf(-d2) - spot * norm.cdf(-d1)
    except Exception:
        return max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)


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
    shocks: tuple[float, ...] | None = None,
) -> dict:
    """一组大盘（指数）冲击情景下的组合损益，默认 -5%/-10%/-15%/-20%。

    `stocks` 需要 symbol/quantity/market_value；`options` 需要
    symbol/quantity/current_price/market_value/strike/expiry（dict 或
    sqlite3.Row 都支持 `row["field"]`）。期权用 Black-Scholes（现价来自
    `underlying_prices`，IV 来自 `iv_map`，缺失时按 30% 兜底），不依赖行上
    可能是 NULL 的 Greeks 列。同一标的的价差两腿在 delta 名义敞口里自然
    net 掉。

    2026-09-10 审计 F-01 修复了两个方向性错误：
    1. 冲击情景以前是"每个标的自己跌10%/20%"，不管它的 beta 是多少——一个
       beta 加权后账户杠杆 3.3x 的组合，"大盘跌10%"这个情景以前显示的是
       每个标的自己跌10%（相当于大盘只跌了 10%/加权beta 那么多，是个温和
       情景），现在是每个标的按自己的 beta 放大：跌幅 = beta × 大盘跌幅，
       beta 2.0 的标的在"大盘跌10%"情景下自己跌 20%。
    2. 期权盈亏以前用 delta + 0.5*gamma*dS² 的二阶泰勒展开近似——这个近似
       只在冲击幅度小的时候准，在"标的可能跌 20%-59%（取决于beta）"这个
       量级上，尤其对深度虚值期权，泰勒展开会明显偏离真实定价（gamma 本身
       在大幅冲击后跟冲击前差很多，展开用的还是冲击前的 gamma）。现在冲击
       前后都用 bs_price() 完整重新定价，价格差就是这笔期权在这个情景下
       真实的盈亏，不管冲击多大都准（只要 BS 假设本身成立）。

    stress_pnl（每个标的）= qty * multiplier * (bs_price(S×(1+beta×指数冲击),
    K, T, iv+vol_shock, type) - bs_price(S, K, T, iv, type))
    大盘跌10%：vol_shock = +8 个vol点；跌20%：+16 个vol点（市场下跌时隐含
    波动率上升，这两个数字沿用了此前的经验设定，没有重新校准——校准
    这两个数字、以及 8%/12%/15% 三档预警阈值要不要跟着这次改动调整，
    是需要你来定的事，不是这次改动的一部分）。中间档位的 vol_shock 按同一
    条斜率插值（每 1% 跌幅 +0.8 个 vol 点），所以 -10/-20 两档的结果跟
    2026-09-20 加入阶梯之前逐位一致。

    2026-09-20：情景从写死的两档改成可配的阶梯。只有 -10/-20 两个点时，
    看不出损失曲线的**形状**——一个把保护腿挂得很远的组合，头 10% 完全
    敞着、第二个 10% 反而比第一个便宜（长 put 开始起作用）。这个"前段有
    洞"的特征在两点之间是隐形的，而它恰恰决定了该补保护还是该减仓。
    `stress_ladder` 键是整数百分点（-5/-10/-15/-20）。
    """
    today = today or datetime.date.today()
    shocks = tuple(shocks if shocks is not None else STRESS_SHOCKS)
    gross = delta_notional = beta_delta = 0.0
    theta_per_day = vega_per_pt = gamma_total = 0.0
    # 按冲击档位累计的损益，键是整数百分点（-5/-10/-15/-20），不是浮点
    # ——浮点做字典键在跨模块传递时会因为 -0.1 的表示误差取不到值。
    ladder: dict[int, float] = {int(round(s * 100)): 0.0 for s in shocks}
    nearest_expiry_date: datetime.date | None = None
    nearest_expiry_sym = ""
    # 按标的拆开的 Beta-Delta。总数回答"整体要不要对冲"，拆开才回答"该用
    # 哪个指数对冲"——半导体那一半用 SMH 比用 QQQ 基差小得多，而两者都按
    # 全额做就是重复对冲。见 account.hedge_split。
    bd_by_underlying: dict[str, float] = {}

    for s in stocks:
        sym = str(s["symbol"] or "").upper()
        q = float(s["quantity"] or 0)
        mv = float(s["market_value"] or 0)
        s_price = mv / q if q else 0.0
        b = beta_map.get(sym, 1.0)
        gross += abs(mv)
        delta_notional += abs(mv)
        beta_delta += q * s_price * 1.0 * b
        bd_by_underlying[sym] = bd_by_underlying.get(sym, 0.0) + q * s_price * b
        # 股票的"完整重新定价"就是线性的（没有凸性可言），beta 放大后
        # 直接乘新的跌幅即可，不需要单独的 bs_price 路径。
        for shock in shocks:
            ladder[int(round(shock * 100))] += q * (b * shock * s_price)

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
        K = iv = 0.0
        opt_type = ""
        dte = 0
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
            bd_by_underlying[und] = bd_by_underlying.get(und, 0.0) + q * mult * d * S * b
            if dte > 0 and K > 0:
                T = dte / 365.0
                price_now = bs_price(S, K, T, iv, opt_type)
                for shock in shocks:
                    # 波动率冲击按跌幅线性缩放：跌10%→+8个vol点、跌20%→+16，
                    # 就是原来写死的那两个数，中间档位按同一条斜率插出来。
                    vol_shock_pts = abs(shock) * 10.0 * VOL_SHOCK_PTS_PER_10PCT
                    s_new = S * (1 + b * shock)
                    price_new = bs_price(s_new, K, T, iv + vol_shock_pts / 100.0, opt_type)
                    ladder[int(round(shock * 100))] += q * mult * (price_new - price_now)

    return {
        "gross_notional": gross,
        "delta_notional": delta_notional,
        "beta_delta": beta_delta,
        "beta_delta_by_underlying": bd_by_underlying,
        "theta_per_day": theta_per_day,
        "vega_per_pt": vega_per_pt,
        "gamma_total": gamma_total,
        "stress_10": ladder.get(-10, 0.0),
        "stress_20": ladder.get(-20, 0.0),
        # 整条阶梯。只看 -10/-20 两个点看不出损失曲线的形状：保护腿挂得远
        # 的组合，头 10% 是敞着的、第二个 10% 反而更便宜，这件事只有中间
        # 档位摆出来才看得见。
        "stress_ladder": ladder,
        "nearest_expiry_date": nearest_expiry_date,
        "nearest_expiry_sym": nearest_expiry_sym,
    }


def implied_bd_ceiling(
    *,
    current_bd: float,
    stress_loss: float,
    equity: float,
    stress_limit: float,
) -> float | None:
    """压力线反推出来的 Beta-Delta 上限：BD 降到多少，压力测试才回到线内。

    按**等比例减仓**算，这时它是精确的而不是近似：把所有持仓同时缩小到
    k 倍，Beta-Delta 和压力损失都是持仓上的求和，两者同乘 k——非线性只
    存在于"大盘跌多少"那个维度上，不在"仓位多大"这个维度上。

    所以返回值的确切含义是：**如果按当前结构等比例减仓**，BD 降到这个数
    时压力恰好压在限额上。

    它对**选择性减仓不成立**，而且方向可能反过来——先把保护腿平掉，BD 确
    实降了，压力损失反而会涨。用它来定"减多少"，不能用来定"减哪个"。

    这个数存在的理由是两条限额会打架：账户可以在 BD 限额之内还剩很大余量
    的同时，把压力线超掉好几个点（2026-09-20 实测 BD 273.5%／限额 350%，
    压力 -10% 却是 17.5%／限额 15%）。两条线治理的是同一个风险，这个函数
    把它们换算到同一把尺子上，好判断 BD 那条线是不是根本没在起作用。

    equity 或 stress_loss 为 0 时返回 None——没有可换算的东西，返回 0 会
    被读成"BD 上限是 0，全部清仓"。
    """
    loss = abs(stress_loss)
    if equity <= 0 or loss <= 0 or current_bd == 0:
        return None
    return current_bd * (stress_limit * equity) / loss


def stress_curve_shape(stress_ladder: dict[int, float]) -> list[dict]:
    """把损失阶梯拆成每一段的**边际**损失，按跌幅从浅到深。

    只看 -10/-20 两个总数，看不出损失曲线的形状。拆成分段之后才看得见一
    件事：后一段比前一段**便宜**，说明长 put 在深水区才开始起作用，头一段
    是敞着的——保护腿挂得太远。这种组合扛得住崩盘，却在温和回调里流血，
    而温和回调发生的次数多得多。

    每段返回 from_pct/to_pct（负的整数百分点）、marginal（这一段多亏多少，
    负数）、cheaper_than_previous（这一段的边际损失是否小于上一段）。
    """
    if not stress_ladder:
        return []
    # 从浅到深：-5, -10, -15, -20
    levels = sorted(stress_ladder, reverse=True)
    bands: list[dict] = []
    prev_level, prev_total = 0, 0.0
    for level in levels:
        total = stress_ladder[level]
        marginal = total - prev_total
        bands.append({
            "from_pct": prev_level,
            "to_pct": level,
            "cumulative": total,
            "marginal": marginal,
            "cheaper_than_previous": (
                bands and abs(marginal) < abs(bands[-1]["marginal"])
            ) or False,
        })
        prev_level, prev_total = level, total
    return bands


def protection_gap(stress_ladder: dict[int, float]) -> str | None:
    """如果损失曲线是"前段贵、后段便宜"，说出来；否则 None。

    返回的是一句可以直接显示的话，不是布尔值——调用方不需要再把三个数字
    重新拼成一句人话，也就不会两个页面拼出两种说法。
    """
    bands = stress_curve_shape(stress_ladder)
    hits = [b for b in bands if b["cheaper_than_previous"]]
    if not hits:
        return None
    first = hits[0]
    prev = bands[bands.index(first) - 1]
    return (
        f"{prev['from_pct']}%→{prev['to_pct']}% 这一段亏 ${abs(prev['marginal']):,.0f}，"
        f"而更深的 {first['from_pct']}%→{first['to_pct']}% 只多亏 "
        f"${abs(first['marginal']):,.0f}——保护腿在深水区才起作用，"
        f"头一段是敞着的。这种结构扛得住崩盘，却在温和回调里流血，"
        f"而温和回调发生的次数多得多。"
    )


def classify_stress_status(
    stress_10_ratio: float | None, limits: dict | None = None,
    stress_20_ratio: float | None = None,
) -> str:
    """GREEN / YELLOW_WARNING / ORANGE_DE_RISK / RED_HARD_STOP，取 -10%
    情景（warning/de_risk/hard_stop 三档）和 -20% 情景（只有一条独立的
    stress_20_hard_stop 硬止损线，不像 -10% 有两档早期预警）两者里更
    严重的那个。

    2026-09-11 用户确认：-10% 情景只要 0%/hard_stop 两档，中间的 warning/
    de_risk 不必单独关心——不必改这个函数本身去掉两档，把这两个限额的
    值设成跟 hard_stop 相同即可（比较逻辑天然收缩成二档，不引入新代码
    路径）。-20% 情景是新增的独立检查，跟 -10% 情景是两个不同的读数，
    不共用同一条线（之前 account/risk_signals.py 里曾经借用 -10% 的
    stress_hard_stop 当 -20% 的红线，属于历史遗留的巧合，这次一并改成
    各自独立的线，见 stress_20_hard_stop）。stress_20_ratio 不传时
    （旧调用方/测试）完全不检查这条线，保持原行为。
    """
    limits = limits or DEFAULT_RISK_LIMITS
    magnitude_10 = abs(stress_10_ratio) if stress_10_ratio else 0.0
    if magnitude_10 >= limits["stress_hard_stop"]:
        return "RED_HARD_STOP"
    if stress_20_ratio is not None:
        magnitude_20 = abs(stress_20_ratio)
        stress_20_limit = limits.get("stress_20_hard_stop",
                                     DEFAULT_RISK_LIMITS["stress_20_hard_stop"])
        if magnitude_20 >= stress_20_limit:
            return "RED_HARD_STOP"
    if magnitude_10 >= limits["stress_de_risk"]:
        return "ORANGE_DE_RISK"
    if magnitude_10 >= limits["stress_warning"]:
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


# ────────────────────────────────────────────────────────────────
# compute_iv_regime / compute_risk_snapshot -- pure cores extracted from
# account_monitor.py's _compute_iv_regime / _compute_risk_snapshot (see
# docs/architecture/risk_and_iv_architecture.md). Callers own every DB
# query, network call and module-level global read; these two functions
# take the results as plain data and never import streamlit or touch a
# connection/socket themselves.
# ────────────────────────────────────────────────────────────────

IVStatus = Literal["INSUFFICIENT_HISTORY", "EXTREME_IV", "HIGH_IV", "LOW_IV", "NORMAL"]
PortfolioIVStatus = Literal[IVStatus, "NO_DATA"]  # 组合层多一个 NO_DATA


class IVRow(TypedDict):
    """iv_history 或 options_positions 查询结果的单行，两个来源字段一致。"""
    symbol: str
    iv: float


class IVPositionStatus(TypedDict):
    symbol: str
    iv: float
    n: int                      # 样本数（历史 + 当前）
    iv_rank: float | None       # (current-min)/(max-min)，min==max 时为 None
    piv: float | None           # 分位：<=current 的样本占比
    status: IVStatus


class IVRegimeSnapshot(TypedDict):
    status: PortfolioIVStatus
    positions: list[IVPositionStatus]
    max_piv: IVPositionStatus | None


def compute_iv_regime(
    history_rows: list[IVRow],
    current_rows: list[IVRow],
    min_samples: int = 20,
) -> IVRegimeSnapshot:
    """IV Rank / PIV / 组合 IV Regime 状态。逻辑与 update_iv_regime.py 完全
    一致（HIGH_PIV=0.85 / EXTREME_PIV=0.95）。调用方职责：从 iv_history
    （按 timestamp 排序）和 options_positions（iv 非空）两次 SELECT 取出
    history_rows / current_rows 后传入 -- 这里不再自己发 SQL。
    """
    HIGH_PIV    = 0.85
    EXTREME_PIV = 0.95
    LOW_PIV     = 0.30

    history: dict = {}
    for r in history_rows:
        history.setdefault(r["symbol"], []).append(float(r["iv"]))

    results = []
    for cur in current_rows:
        sym = cur["symbol"]
        current_iv = float(cur["iv"])
        sample = list(history.get(sym, []))
        if current_iv not in sample:
            sample.append(current_iv)
        n = len(sample)
        iv_min, iv_max = min(sample), max(sample)
        iv_rank = (current_iv - iv_min) / (iv_max - iv_min) if iv_max > iv_min else None
        piv = sum(1 for v in sample if v <= current_iv) / n if n else None

        if n < min_samples:
            status = "INSUFFICIENT_HISTORY"
        elif piv is not None and piv >= EXTREME_PIV:
            status = "EXTREME_IV"
        elif piv is not None and piv >= HIGH_PIV:
            status = "HIGH_IV"
        elif piv is not None and piv < LOW_PIV:
            status = "LOW_IV"
        else:
            status = "NORMAL"

        results.append({
            "symbol": sym, "iv": current_iv, "n": n,
            "iv_rank": iv_rank, "piv": piv, "status": status,
        })

    if not results:
        return {"status": "NO_DATA", "positions": [], "max_piv": None}

    sufficient = [r for r in results if r["status"] != "INSUFFICIENT_HISTORY"]
    if not sufficient:
        port_status = "INSUFFICIENT_HISTORY"
    elif any(r["status"] == "EXTREME_IV" for r in sufficient):
        port_status = "EXTREME_IV"
    elif any(r["status"] == "HIGH_IV" for r in sufficient):
        port_status = "HIGH_IV"
    elif all(r["piv"] is not None and r["piv"] < LOW_PIV for r in sufficient):
        port_status = "LOW_IV"
    else:
        port_status = "NORMAL"

    max_piv = max((r for r in results if r["piv"] is not None),
                  key=lambda x: x["piv"], default=None)

    return {"status": port_status, "positions": results, "max_piv": max_piv}


class AccountBalance(TypedDict, total=False):
    """account.repository.load_latest_balance() 的返回值（account_balance
    表全部列，dict(row) 得到，缺失同步记录时是空 dict）。"""
    account_id: str
    sync_time: str
    total_equity: float
    cash_balance: float
    margin_used: float
    margin_available: float
    margin_usage_pct: float
    day_pnl: float


class NavRow(TypedDict):
    d: str              # DATE(sync_time)，YYYY-MM-DD
    total_equity: float


class CashflowRow(TypedDict):
    trade_date: str
    cf: float           # SUM(amount)


class OptionPositionRow(TypedDict):
    symbol: str
    quantity: int
    current_price: float | None
    market_value: float | None
    strike: float | None
    expiry: str | None


class StockPositionRow(TypedDict):
    symbol: str
    quantity: int
    market_value: float | None


class RiskSnapshotInputs(TypedDict):
    """_compute_risk_snapshot 现在自己去查的一切，由调用方（薄包装）组装好
    整个传入。`now` 替代函数内部原来的 datetime.datetime.now() 调用 --
    **必须是带时区的 aware datetime**（比如
    datetime.datetime.now(datetime.timezone.utc)），任意时区都行，本函数
    内部用 astimezone() 换算到 sync_time 自己的时区。曾经传过裸的 naive
    now()，在 freezegun 冻结测试下实测会算错 7 小时——naive now() 的返回值
    跟 astimezone() 对 naive 输入的本地时区解读用的不是同一套时区语义，
    交叉验证测试 test_new_risk_snapshot_core_matches_golden_snapshot 因此
    集体失败，已改成要求 aware 输入并修正。"""
    balance: AccountBalance
    nav_rows: list[NavRow]                    # account_balance 历史，已按 drawdown_start_date 过滤
    cashflow_rows: list[CashflowRow]          # transactions 里的出入金，同一时间窗口
    option_positions: list[OptionPositionRow]
    stock_positions: list[StockPositionRow]
    underlying_prices: dict[str, float]       # _fetch_underlying_prices 的结果
    iv_map: dict[str, dict]                   # _get_atm_iv_batch 的结果
    beta_map: dict[str, float]                # 等价于 _BETA_SPY
    risk_limits: dict                         # 等价于 _RISK_LIMITS
    now: datetime.datetime                    # aware datetime，等价于原来函数内部的 datetime.datetime.now()


class RiskSnapshotError(TypedDict):
    error: Literal["no_equity"]


class RiskSnapshot(TypedDict):
    equity: float
    drawdown: float
    drawdown_basis: str
    gross_notional: float
    delta_notional: float
    leverage: float | None
    leverage_delta: float | None
    beta_delta: float
    beta_delta_ratio: float | None
    beta_delta_by_underlying: dict[str, float]
    theta_per_day: float
    vega_per_pt: float
    gamma_total: float
    stress_10: float
    stress_10_ratio: float | None
    stress_20: float
    stress_20_ratio: float | None
    stress_ladder: dict[int, float]
    protection_gap: str | None
    bd_ceiling_from_stress: float | None
    nearest_expiry_date: str | None
    nearest_expiry_sym: str | None
    risk_status: str          # GREEN / YELLOW_WARNING / ORANGE_DE_RISK / RED_HARD_STOP
    drawdown_status: str
    data_synced_at: str | None
    data_age_hours: float | None
    data_stale: bool
    iv_fallback_symbols: list[str]


RiskSnapshotResult = RiskSnapshot | RiskSnapshotError


def compute_risk_snapshot(inputs: RiskSnapshotInputs) -> RiskSnapshotResult:
    """Beta 加权 Delta、杠杆、压力测试损失、TWR 回撤的组合风险快照。
    `inputs["balance"].get("total_equity") <= 0` 时原样返回
    RiskSnapshotError（现有行为，不是新增校验）。其余分支 -- TWR 回撤、
    压力测试、杠杆/BD 比率、风险状态分类 -- 直接调用本模块里已经存在的
    4 个纯函数，逻辑不变，只是不再自己发 SQL/网络请求去攒这些函数的入参。
    """
    bal = inputs["balance"]
    equity = float(bal.get("total_equity") or 0)
    if equity <= 0:
        return {"error": "no_equity"}

    risk_limits = inputs["risk_limits"]

    # ── 数据新鲜度：持仓/余额距上次同步已经过多久 ──────────────────────
    _sync_raw = bal.get("sync_time")
    data_age_hours = None
    try:
        _sync_dt = datetime.datetime.fromisoformat(str(_sync_raw))
        # inputs["now"] 必须是 aware datetime（任意时区都行）。sync_time 有
        # tzinfo 时用 astimezone() 换算到同一时区，两个 aware 值直接相减，
        # 跟原来的 datetime.datetime.now(_sync_dt.tzinfo) 逐位等价（aware
        # 转 aware 是精确的时区换算，不依赖任何一边"当前系统时区"是什么）。
        # sync_time 没有 tzinfo 这条分支在现有写入路径下不会触发（account.
        # repository.save_balance 写入的 sync_time 永远带 ET 时区），保留只
        # 是不改变原有的防御性行为；用 astimezone() 转到系统本地时区后去掉
        # tzinfo，对应原来裸的 datetime.datetime.now()。
        _now_dt = (inputs["now"].astimezone(_sync_dt.tzinfo) if _sync_dt.tzinfo
                   else inputs["now"].astimezone().replace(tzinfo=None))
        data_age_hours = (_now_dt - _sync_dt).total_seconds() / 3600.0
    except Exception:
        pass
    _STALE_HOURS = 24.0
    data_stale = bool(data_age_hours is not None and data_age_hours > _STALE_HOURS)

    # ── TWR-based drawdown（排除出入金）──────────────────────────────
    _dd_start = str(risk_limits.get("drawdown_start_date", "2026-06-01"))
    _nav_by_d = {str(r["d"]): float(r["total_equity"]) for r in inputs["nav_rows"]}
    _cf_map_dd = {r["trade_date"]: float(r["cf"]) for r in inputs["cashflow_rows"]}
    drawdown = compute_twr_drawdown(_nav_by_d, _cf_map_dd)

    opts = inputs["option_positions"]
    stks = inputs["stock_positions"]
    und_prices = inputs["underlying_prices"]
    _iv_map = inputs["iv_map"]
    beta_map = inputs["beta_map"]

    underlyings = set()
    for o in opts:
        parsed = parse_occ(str(o["symbol"] or "").upper())
        if parsed:
            underlyings.add(parsed["root"])

    stress = compute_portfolio_stress_test(
        stks, opts,
        underlying_prices=und_prices, iv_map=_iv_map, beta_map=beta_map,
    )
    gross, delta_notl, beta_delta = (
        stress["gross_notional"], stress["delta_notional"], stress["beta_delta"]
    )
    theta_tot, vega_tot, gamma_tot = (
        stress["theta_per_day"], stress["vega_per_pt"], stress["gamma_total"]
    )
    stress_10, stress_20 = stress["stress_10"], stress["stress_20"]
    nearest_expiry_date, nearest_expiry_sym = (
        stress["nearest_expiry_date"], stress["nearest_expiry_sym"]
    )
    # 走了默认 IV(0.30) 兜底的标的，用于在快照里留痕——跟
    # compute_portfolio_stress_test 内部判断 IV 是否命中同一个条件，
    # 但只需按 underlying 去重检查一次，不用重新走一遍逐 option 循环。
    iv_fallback_syms = {und for und in underlyings if not _iv_map.get(und)}

    leverage          = gross / equity if equity else None
    leverage_delta    = delta_notl / equity if equity else None   # Delta 口径，价差不双计
    beta_delta_ratio  = beta_delta / equity if equity else None
    stress_10_ratio   = stress_10 / equity if equity else None
    stress_20_ratio   = stress_20 / equity if equity else None

    risk_status = classify_stress_status(stress_10_ratio, risk_limits, stress_20_ratio)
    dd_status   = classify_drawdown_status(drawdown, risk_limits)

    return {
        "equity":           equity,
        "drawdown":         drawdown,
        "drawdown_basis":   f"cash_flow_adjusted_since_{_dd_start}",
        "gross_notional":   round(gross, 0),
        "delta_notional":   round(delta_notl, 0),
        "leverage":         round(leverage, 2)      if leverage       is not None else None,
        "leverage_delta":   round(leverage_delta, 2) if leverage_delta is not None else None,
        "beta_delta":       round(beta_delta, 0),
        "beta_delta_ratio": round(beta_delta_ratio, 4) if beta_delta_ratio is not None else None,
        # 逐标的拆分：合计回答"要不要对冲"，拆分回答"该用 QQQ 还是 SMH"。
        "beta_delta_by_underlying": {
            k: round(v, 0) for k, v in stress["beta_delta_by_underlying"].items()
        },
        "theta_per_day":       round(theta_tot, 2),
        "vega_per_pt":         round(vega_tot, 2),
        "gamma_total":         round(gamma_tot, 4),
        "stress_10":           round(stress_10, 0),
        "stress_10_ratio":     round(stress_10_ratio, 4)  if stress_10_ratio  is not None else None,
        "stress_20":           round(stress_20, 0),
        "stress_20_ratio":     round(stress_20_ratio, 4)  if stress_20_ratio  is not None else None,
        # 整条损失阶梯（-5/-10/-15/-20）。只有 -10/-20 两个点时看不出曲线
        # 形状——保护腿挂得远的组合，头一段是敞着的、第二段反而更便宜。
        "stress_ladder":       {k: round(v, 0) for k, v in stress["stress_ladder"].items()},
        "protection_gap":      protection_gap(stress["stress_ladder"]),
        # 压力线反推的 BD 上限：按当前结构等比例减仓，BD 降到这个数压力才
        # 回到线内。跟 max_beta_delta_ratio 那条线摆在一起看，就知道哪条线
        # 在真正起约束作用——两者可以差很远。
        "bd_ceiling_from_stress": implied_bd_ceiling(
            current_bd=beta_delta,
            stress_loss=stress_10,
            equity=equity,
            stress_limit=risk_limits.get("stress_hard_stop",
                                          DEFAULT_RISK_LIMITS["stress_hard_stop"]),
        ),
        "nearest_expiry_date": nearest_expiry_date,
        "nearest_expiry_sym":  nearest_expiry_sym,
        "risk_status":         risk_status,
        "drawdown_status":     dd_status,
        "data_synced_at":      _sync_raw,
        "data_age_hours":      round(data_age_hours, 1) if data_age_hours is not None else None,
        "data_stale":          data_stale,
        "iv_fallback_symbols": sorted(iv_fallback_syms),
    }


# ────────────────────────────────────────────────────────────────
# 跨模块调用契约（Protocol）：调用方按接口类型编程，而不是反射取值
# ────────────────────────────────────────────────────────────────

class IVRegimeEngine(Protocol):
    def __call__(
        self, history_rows: list[IVRow], current_rows: list[IVRow], min_samples: int = 20,
    ) -> IVRegimeSnapshot: ...


class RiskSnapshotEngine(Protocol):
    def __call__(self, inputs: RiskSnapshotInputs) -> RiskSnapshotResult: ...


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
    recs += new_opportunity_candidates(
        risk_snapshot=risk_snapshot, iv_regime=iv_regime, ai_scores=ai_scores,
        held_underlyings=held_underlyings, risk_limits=limits, start_idx=idx,
    )

    return recs


def new_opportunity_candidates(
    *,
    risk_snapshot: dict,
    iv_regime: dict,
    ai_scores: dict,
    held_underlyings: set[str],
    risk_limits: dict | None = None,
    start_idx: int = 1,
) -> list[dict]:
    """高 AI 评分 + IV regime 匹配 + 风险余量允许 → 候选新仓（不含已持仓标的）。

    2026-09-10 从 build_recommendations() 里抽出来——这是门④"下场前验证"
    真正对应的内容（门④之前是空占位页，这块逻辑一直混在作战室"今日操作
    简报"里的第3段，跟现有持仓管理的建议堆在一起，没人特意去看）。
    build_recommendations() 继续调这个函数，作战室的展示不变；
    pre_trade_check.py（门④）直接调它单独展示。

    只是"AI评分≥70 + IV regime 配策略方向"这层很基础的初筛——不看硬约束
    余量（门③仓位管理才有）、不看 Kelly 建议（门③仓位建议才有），是"值得
    进一步看"的候选池，不是可以直接下单的建议。
    """
    limits = risk_limits or DEFAULT_RISK_LIMITS
    iv_status = iv_regime.get("status", "NO_DATA")
    leverage = risk_snapshot.get("leverage_delta") or risk_snapshot.get("leverage") or 0.0

    out: list[dict] = []
    idx = start_idx
    if risk_snapshot.get("error") or leverage >= limits["max_leverage"] * 0.75:
        return out

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
        out.append({
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
    return out


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


def _bs_put_price(S: float, K: float, T: float, sigma: float,
                   risk_free_rate: float = RF_RATE) -> float:
    """Black-Scholes European put price (not per-share Greeks -- a $ price)."""
    if T <= 1e-6 or sigma <= 1e-6 or S <= 0:
        return max(K - S, 0.0)
    try:
        sqrt_t = math.sqrt(T)
        d1 = (math.log(S / K) + (risk_free_rate + 0.5 * sigma**2) * T) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        return K * math.exp(-risk_free_rate * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    except Exception:
        return max(K - S, 0.0)


def compute_index_hedge_plan(
    *,
    underlying: str,
    equity: float,
    current_bd: float,
    current_bdr: float,
    target_bd_ratio: float,
    spot: float,
    iv_pct: float,
    beta: float,
    existing_legs: list[dict],
    existing_bd: float,
    n_existing: int,
    current_option_cost: float,
    plan_dte: int = 90,
    today: datetime.date | None = None,
    vix_spike: bool = False,
    event_risk: bool = False,
    trend_break: bool = False,
    width_narrow: float | None = None,
    width_wide: float | None = None,
    strike_step: float = 5.0,
    bd_to_hedge: float | None = None,
) -> dict:
    """Three put-debit-spread hedge plans on one index ETF (A=narrow,
    B=wide, C=keep existing + top up) that bring beta-weighted Delta down
    toward `target_bd_ratio`.

    Pure given the caller's already-fetched risk snapshot, the ETF's spot
    price + IV, its existing option legs, and the current options-cost-ratio
    dollar amount -- it does none of those fetches itself, and delegates the
    protective-put governance check to account.hedge_governance, which is
    already deterministic/pure.

    `underlying` only labels the output and builds OCC symbols; every number
    comes from `spot`/`iv_pct`/`beta`, so QQQ and SMH go through exactly the
    same arithmetic rather than two drifting copies of it.

    Spread widths default to a share of spot (5.8% / 10%) instead of fixed
    dollars: $35 wide is a sensible QQQ structure at ~$600 and a nonsensical
    one on a $60 ETF. `compute_qqq_hedge_plan` still passes QQQ's historical
    literals so its output is unchanged.

    `bd_to_hedge` overrides "reduce the whole portfolio to target". Hedging a
    sleeve -- the semiconductor share of the excess with SMH, the rest with
    QQQ -- needs each plan sized to its own slice; sizing both to the full
    excess would hedge the book twice. See account.hedge_split.

    vix_spike / event_risk / trend_break: real-data trigger inputs (see
    account.systemic_risk_signal, added 2026-09-14) forwarded to
    evaluate_protective_put_hedges(). All three default to False, matching
    this function's behavior before that module existed -- passing none of
    them keeps the only trigger active being BETA_DELTA_EXCESS, as before.
    """
    today = today or datetime.date.today()
    iv = iv_pct / 100.0
    root = (underlying or "").upper()

    target_bd = target_bd_ratio * equity
    if bd_to_hedge is None:
        bd_to_hedge = current_bd - target_bd  # positive => need to reduce

    if width_narrow is None:
        width_narrow = max(strike_step, round(spot * 0.058 / strike_step) * strike_step)
    if width_wide is None:
        width_wide = max(width_narrow + strike_step,
                         round(spot * 0.10 / strike_step) * strike_step)

    plan_exp = today + datetime.timedelta(days=plan_dte)
    plan_occ_exp = plan_exp.strftime("%y%m%d")

    long_strikes = [
        l["strike"] for l in existing_legs
        if l.get("type") == "P" and l.get("qty", 0) > 0
    ]
    ref_buy_k = (float(max(long_strikes)) if long_strikes
                 else float(round(spot * 0.97 / strike_step) * strike_step))

    def _plan(buy_k: float, sell_k: float) -> dict:
        T = plan_dte / 365.0
        gb = bs_greeks(spot, buy_k, T, iv, "put")
        gs = bs_greeks(spot, sell_k, T, iv, "put")
        d_buy, th_buy = gb["delta"], gb["theta"]
        d_sell, th_sell = gs["delta"], gs["theta"]

        # BD contribution per spread: long 1 put at buy_k, short 1 put at sell_k.
        # Net is negative (reduces beta-weighted Delta) since |d_buy| > |d_sell|.
        bd_ps = 100 * (d_buy - d_sell) * spot * beta

        n_total = math.ceil(bd_to_hedge / (-bd_ps)) if bd_to_hedge > 0 and bd_ps < 0 else 0

        p_buy = _bs_put_price(spot, buy_k, T, iv) * 100
        p_sell = _bs_put_price(spot, sell_k, T, iv) * 100
        cost_ps = p_buy - p_sell  # net debit per spread

        post_bdr = ((current_bd + n_total * bd_ps) / equity * 100) if equity else 0
        theta_chg = n_total * (th_buy - th_sell) * 100
        new_cost_tot = current_option_cost + n_total * cost_ps
        new_ocr = (new_cost_tot / equity * 100) if equity else 0

        return {
            "underlying": root,
            "buy_strike": buy_k, "sell_strike": sell_k,
            # OCC 标准顺序是 根 + YYMMDD + C/P + 8位行权价。门⑥账户监控的
            # 对冲卡片一直把 P 拼在最后（QQQ260830004700000P），那个串连本
            # 项目自己的 account.options.OCC_RE 都解析不了，照着下单会被券商
            # 拒掉——所以符号在这里统一生成一次，页面直接用。
            "buy_occ": f"{root}{plan_occ_exp}P{int(round(buy_k * 1000)):08d}",
            "sell_occ": f"{root}{plan_occ_exp}P{int(round(sell_k * 1000)):08d}",
            "n_total": n_total,
            "bd_per_spread": round(bd_ps, 0),
            "d_long": round(d_buy, 3), "d_short": round(d_sell, 3),
            "cost_per_spread": round(cost_ps, 2),
            "total_cost": round(n_total * cost_ps, 0),
            "post_bd_ratio": round(post_bdr, 1),
            "theta_change": round(theta_chg, 2),
            "new_ocr": round(new_ocr, 1),
            "max_loss": round(n_total * cost_ps, 0),
            "max_payoff": round(n_total * (buy_k - sell_k) * 100 - n_total * cost_ps, 0),
        }

    plan_a = _plan(ref_buy_k, ref_buy_k - width_narrow)
    plan_b = _plan(ref_buy_k, ref_buy_k - width_wide)

    # Plan C: keep existing legs, top up with plan A's spread structure.
    remaining_bd = bd_to_hedge + existing_bd
    if remaining_bd <= 0 or plan_a["bd_per_spread"] >= 0:
        n_add = 0
    else:
        n_add = math.ceil(remaining_bd / (-plan_a["bd_per_spread"]))
    post_bdc = ((current_bd + existing_bd + n_add * plan_a["bd_per_spread"])
                / equity * 100) if equity else 0

    plan_c = {
        "underlying": root,
        "n_existing": n_existing,
        "n_additional": n_add,
        "buy_strike": ref_buy_k,
        # Plan A's structure, so the width follows plan A rather than a
        # literal $35 that only ever matched QQQ.
        "sell_strike": ref_buy_k - width_narrow,
        "buy_occ": plan_a["buy_occ"],
        "sell_occ": plan_a["sell_occ"],
        "cost_per_spread": plan_a["cost_per_spread"],
        "additional_cost": round(n_add * plan_a["cost_per_spread"], 0),
        "post_bd_ratio": round(post_bdc, 1),
        "existing_bd": round(existing_bd, 0),
    }

    hedge_governance = evaluate_protective_put_hedges(
        existing_legs,
        equity=equity,
        beta_delta_pct=current_bdr * 100,
        target_beta_delta_pct=target_bd_ratio * 100,
        today=today,
        vix_spike=vix_spike,
        event_risk=event_risk,
        trend_break=trend_break,
    )

    return {
        "underlying": root,
        "equity": equity,
        "current_bd_ratio": round(current_bdr * 100, 1),
        "target_bd_ratio": round(target_bd_ratio * 100, 1),
        "bd_to_hedge": round(bd_to_hedge, 0),
        "spot": round(spot, 2),
        "iv": round(iv_pct, 1),
        "beta": beta,
        "width_narrow": width_narrow,
        "width_wide": width_wide,
        # 老键名，门⑥账户监控的对冲卡片还在读——QQQ 之外的标的也一样填，
        # 免得调用方要按 underlying 分两套读法。
        "qqq_price": round(spot, 2),
        "qqq_iv": round(iv_pct, 1),
        "b_qqq": beta,
        "existing_legs": existing_legs,
        "existing_bd": round(existing_bd, 0),
        "n_existing": n_existing,
        "plan_dte": plan_dte,
        "plan_occ_exp": plan_occ_exp,
        "plan_exp_str": plan_exp.strftime("%Y-%m-%d"),
        "plan_a": plan_a,
        "plan_b": plan_b,
        "plan_c": plan_c,
        "hedge_governance": hedge_governance,
    }


def compute_qqq_hedge_plan(
    *,
    qqq_price: float,
    qqq_iv_pct: float,
    beta_qqq: float,
    **kwargs,
) -> dict:
    """QQQ 对冲方案——`compute_index_hedge_plan` 的固定参数包装。

    宽度沿用历史上写死的 $35 / $60、行权价对齐 $5，所以输出跟 2026-09-20
    拆出通用函数之前逐字一致；门⑥账户监控的对冲卡片和 tests/test_risk.py
    都还按这个签名调用。
    """
    return compute_index_hedge_plan(
        underlying="QQQ",
        spot=qqq_price,
        iv_pct=qqq_iv_pct,
        beta=beta_qqq,
        width_narrow=35.0,
        width_wide=60.0,
        strike_step=5.0,
        **kwargs,
    )


class SimAction(TypedDict, total=False):
    type: str
    underlying: str
    label: str


class SimOptionRow(TypedDict, total=False):
    quantity: float
    current_price: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    market_value: float | None


class SimImpactResult(TypedDict):
    bd_delta: float
    theta_delta: float
    s10_delta: float
    s20_delta: float
    cash_delta: float
    beta_delta: float
    beta_delta_ratio: float
    theta_per_day: float
    stress_10: float
    stress_10_ratio: float
    stress_20: float
    stress_20_ratio: float
    actions: list[str]


def compute_sim_impact(
    sim_actions: list[SimAction],
    base_snap: dict,
    hplan: dict | None,
    *,
    underlying_prices: dict[str, float],
    beta_map: dict[str, float],
    options_by_underlying: dict[str, list[SimOptionRow]],
) -> SimImpactResult:
    """Apply sim_actions to base_snap and return post-simulation metrics.

    action types: 'close_underlying', 'qqq_hedge', 'no_sim', 'no_change'.

    Logic is a byte-for-byte port of account_monitor.py's original
    _compute_sim_impact (see tests/golden/test_sim_impact_golden.py) --
    the three impure dependencies it used to reach for directly (a network
    price lookup, the module-level _BETA_SPY beta table, and a per-action
    `options_positions` DB query) are now read-only parameters. The caller
    is expected to have already deduplicated `options_by_underlying` by
    underlying (one query per distinct symbol, not one per action) -- this
    changes only how many times the DB is hit, never the rows a given
    underlying resolves to, so re-processing the same underlying across two
    actions still adds its contribution twice, exactly as before.
    """
    equity = base_snap.get("equity", 1)
    bd_delta_sim = theta_delta_sim = vega_delta_sim = s10_delta_sim = s20_delta_sim = cash_delta_sim = 0.0
    descs: list[str] = []

    for action in sim_actions:
        atype = action.get("type", "no_change")

        if atype == "close_underlying":
            und  = action["underlying"]
            S    = underlying_prices.get(und, 0.0)
            b    = beta_map.get(und, 1.0)
            mult = 100.0

            _rows = options_by_underlying.get(und, [])

            _mv_sum = 0.0
            for r in _rows:
                q   = float(r.get("quantity") or 0)
                d   = float(r.get("delta") or 0)
                g   = float(r.get("gamma") or 0)
                th  = float(r.get("theta") or 0)
                vg  = float(r.get("vega") or 0)
                mv  = float(r.get("market_value") or 0)
                bd_delta_sim    -= q * mult * d * S * b if S > 0 else 0.0
                theta_delta_sim -= q * mult * th
                vega_delta_sim  -= q * mult * vg
                _mv_sum += mv
                if S > 0:
                    ds10 = -0.10 * S;  ds20 = -0.20 * S
                    s10_delta_sim -= (q * mult * (d * ds10 + 0.5 * g * ds10**2)
                               + q * mult * vg * 8)
                    s20_delta_sim -= (q * mult * (d * ds20 + 0.5 * g * ds20**2)
                               + q * mult * vg * 16)
            cash_delta_sim += _mv_sum
            descs.append(f"关闭 {und} 期权（收回约${_mv_sum:+,.0f}）")

        elif atype == "qqq_hedge":
            if not hplan or "error" in hplan:
                descs.append("QQQ对冲（数据不足，跳过）");  continue
            _pa = hplan.get("plan_a", {})
            n   = _pa.get("n_total", 0)
            if n <= 0:
                descs.append("QQQ对冲（现有对冲已足够）");  continue
            S   = hplan["qqq_price"];  iv  = hplan["qqq_iv"] / 100
            b   = hplan["b_qqq"];      T   = hplan["plan_dte"] / 365.0
            bk  = _pa["buy_strike"];   sk  = _pa["sell_strike"]
            mult = 100.0
            gb = bs_greeks(S, bk, T, iv, "put")
            gs = bs_greeks(S, sk, T, iv, "put")
            dn  = gb["delta"] - gs["delta"];   gn  = gb["gamma"] - gs["gamma"]
            thn = gb["theta"] - gs["theta"];   vgn = gb["vega"]  - gs["vega"]
            bd_delta_sim    += n * mult * dn  * S * b
            theta_delta_sim += n * mult * thn
            vega_delta_sim  += n * mult * vgn
            ds10 = -0.10 * S;  ds20 = -0.20 * S
            s10_delta_sim += n * (mult * (dn * ds10 + 0.5 * gn * ds10**2) + mult * vgn * 8)
            s20_delta_sim += n * (mult * (dn * ds20 + 0.5 * gn * ds20**2) + mult * vgn * 16)
            cash_delta_sim -= _pa.get("total_cost", 0)
            descs.append(f"QQQ Put Spread×{n}张（方案A，成本${_pa.get('total_cost',0):,.0f}）")

        else:
            descs.append(action.get("label", "持有（不变）"))

    new_bd   = base_snap.get("beta_delta", 0)    + bd_delta_sim
    new_bdr  = new_bd / equity if equity else 0
    new_th   = base_snap.get("theta_per_day", 0) + theta_delta_sim
    new_s10  = base_snap.get("stress_10", 0)     + s10_delta_sim
    new_s20  = base_snap.get("stress_20", 0)     + s20_delta_sim
    new_s10r = new_s10 / equity if equity else 0
    new_s20r = new_s20 / equity if equity else 0

    return {
        "bd_delta":    round(bd_delta_sim, 0),   "theta_delta": round(theta_delta_sim, 2),
        "s10_delta":   round(s10_delta_sim, 0),  "s20_delta":   round(s20_delta_sim, 0),
        "cash_delta":  round(cash_delta_sim, 0),
        "beta_delta":       round(new_bd,  0),
        "beta_delta_ratio": round(new_bdr * 100, 1),
        "theta_per_day":    round(new_th,  2),
        "stress_10":        round(new_s10, 0),
        "stress_10_ratio":  round(new_s10r * 100, 1),
        "stress_20":        round(new_s20, 0),
        "stress_20_ratio":  round(new_s20r * 100, 1),
        "actions":     descs,
    }


def check_otm_spread_alerts(rows: list, *, today: datetime.date | None = None) -> list[dict]:
    """Flag debit spreads (Bear Put / Bull Call) whose combined market value
    has fallen under 10% of what was originally paid for them.

    `rows` are raw options_positions rows (dict or sqlite3.Row) with
    symbol/quantity/strike/expiry/unit_cost/market_value/current_price --
    this groups them into (underlying, expiry, option_type) spreads itself,
    so it takes the DB read's output directly rather than pre-grouped legs.
    Credit spreads (Bull Put / Bear Call) are intentionally not alerted on:
    those were sold for a credit, so their value falling is the expected,
    profitable outcome, not a warning sign.
    """
    from collections import defaultdict

    today = today or datetime.date.today()
    alerts: list[dict] = []

    groups: dict = defaultdict(list)
    for r in rows:
        sym = str(r["symbol"] or "").upper()
        parsed = parse_occ(sym)
        if not parsed:
            continue
        und = parsed["root"]
        exp_str = parsed["expiry"]
        opt_type = parsed["call_put"]
        strike = float(r["strike"] or 0) or parsed["strike"]
        qty = float(r["quantity"] or 0)
        uc = float(r["unit_cost"] or 0)

        mv = r["market_value"]
        if mv is None and r["current_price"] is not None:
            cp = float(r["current_price"])
            mv = cp * abs(qty) * 100 * (1 if qty > 0 else -1)
        mv = float(mv) if mv is not None else None

        groups[(und, exp_str, opt_type)].append({
            "sym": sym, "qty": qty, "strike": strike,
            "unit_cost": uc, "market_value": mv,
        })

    checked: set = set()
    for (und, exp_str, opt_type), legs in groups.items():
        longs = [l for l in legs if l["qty"] > 0]
        shorts = [l for l in legs if l["qty"] < 0]
        if not longs or not shorts:
            continue

        try:
            dte = (datetime.date.fromisoformat(exp_str) - today).days
        except Exception:
            dte = None

        for long_leg in longs:
            for short_leg in shorts:
                key = (und, exp_str, opt_type, long_leg["strike"], short_leg["strike"])
                if key in checked:
                    continue
                checked.add(key)

                ls, ss = long_leg["strike"], short_leg["strike"]
                high_k, low_k = max(ls, ss), min(ls, ss)
                qty_used = min(abs(long_leg["qty"]), abs(short_leg["qty"]))

                spread_label = ""
                is_debit = False
                if opt_type == "P" and ls > ss:
                    spread_label, is_debit = "Bear Put Spread", True
                elif opt_type == "P" and ls < ss:
                    spread_label, is_debit = "Bull Put Spread", False
                elif opt_type == "C" and ls < ss:
                    spread_label, is_debit = "Bull Call Spread", True
                elif opt_type == "C" and ls > ss:
                    spread_label, is_debit = "Bear Call Spread", False

                if not spread_label or not is_debit:
                    continue

                net_cost_per_share = long_leg["unit_cost"] - abs(short_leg["unit_cost"])
                original_cost = max(net_cost_per_share * qty_used * 100, 0.01)

                lmv, smv = long_leg["market_value"], short_leg["market_value"]
                if lmv is None or smv is None:
                    continue
                current_value = lmv + smv

                pct_remaining = current_value / original_cost * 100 if original_cost > 0 else 100
                if pct_remaining >= 10.0:
                    continue

                alerts.append({
                    "underlying":    und,
                    "spread_type":   spread_label,
                    "high_strike":   high_k,
                    "low_strike":    low_k,
                    "opt_type":      opt_type,
                    "expiry":        exp_str,
                    "dte":           dte,
                    "original_cost": round(original_cost, 2),
                    "current_value": round(current_value, 2),
                    "pct_remaining": round(pct_remaining, 1),
                    "long_sym":      long_leg["sym"],
                    "short_sym":     short_leg["sym"],
                    "message": (
                        f"{und} {spread_label} ${low_k:.0f}/{high_k:.0f}"
                        f"{'P' if opt_type == 'P' else 'C'} "
                        f"当前价差市值 ${current_value:.0f}，"
                        f"仅剩原始成本 ${original_cost:.0f} 的 {pct_remaining:.1f}%，"
                        f"价差接近归零"
                    ),
                })

    alerts.sort(key=lambda x: x["pct_remaining"])
    return alerts
