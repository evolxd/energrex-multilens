from __future__ import annotations

import math
import datetime

from scipy.stats import norm


RF_RATE = 0.045
OPTION_MULTIPLIER = 100
DELTA_DRIFT_THRESHOLD = 0.10
VIX_SPIKE_THRESHOLD_PCT = 15.0

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
