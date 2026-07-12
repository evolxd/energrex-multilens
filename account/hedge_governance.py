"""Governance rules for protective index puts.

Protective puts are treated as temporary insurance, not as long-term assets.
The module is intentionally deterministic so the Streamlit UI and tests use the
same rules.
"""

from __future__ import annotations

import datetime as _dt

from account.options import parse_occ

HEDGE_UNDERLYINGS = {"QQQ", "SMH"}
DEFAULT_MAX_CAMPAIGN_COST_PCT = 1.0
ROLL_REVIEW_DTE = 21
NAKED_PUT_MAX_DTE = 45
SPREAD_MAX_DTE = 120
LIGHT_PROTECTION_COVERAGE_PCT = 15
HEAVY_PROTECTION_COVERAGE_PCT = 30
LIGHT_MAX_COST_PCT = 0.5
HEAVY_MAX_COST_PCT = 1.0


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_date(value) -> _dt.date | None:
    if isinstance(value, _dt.date):
        return value
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _normalize_position(raw: dict) -> dict:
    symbol = str(raw.get("symbol") or raw.get("sym") or "").upper().strip()
    parsed = parse_occ(symbol)
    root = str(raw.get("underlying") or parsed.get("root") or "").upper()
    option_type = str(
        raw.get("option_type")
        or ("put" if raw.get("type") == "P" else "call" if raw.get("type") == "C" else "")
        or parsed.get("option_type")
        or ""
    ).lower()
    expiry = raw.get("expiry") or parsed.get("expiry")
    strike = raw.get("strike") if raw.get("strike") is not None else parsed.get("strike")
    qty = _to_float(raw.get("quantity", raw.get("qty")))
    price = _to_float(raw.get("current_price", raw.get("price")))
    market_value = raw.get("market_value")
    if market_value is None:
        market_value = qty * price * 100
    return {
        "symbol": symbol,
        "root": root,
        "option_type": option_type,
        "expiry": expiry,
        "strike": _to_float(strike),
        "quantity": qty,
        "price": price,
        "market_value": _to_float(market_value),
    }


def _find_matching_short_put(long_put: dict, positions: list[dict]) -> dict | None:
    candidates = [
        p for p in positions
        if p["root"] == long_put["root"]
        and p["option_type"] == "put"
        and p["expiry"] == long_put["expiry"]
        and p["quantity"] < 0
        and p["strike"] < long_put["strike"]
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p["strike"])


def _round_strike(value: float, increment: int = 5) -> int:
    if value <= 0:
        return 0
    return int(round(value / increment) * increment)


def generate_qqq_protection_plan(
    *,
    qqq_price: float,
    vix_value: float,
    ma50: float,
    ma200: float,
    beta_delta_pct: float | None = None,
    target_beta_delta_pct: float | None = None,
    event_risk: bool = False,
    trend_break_confirmed: bool = False,
) -> dict:
    """Generate a disciplined QQQ put-spread protection plan.

    Coverage percent means percentage of portfolio Beta-Delta exposure covered,
    not percentage of account equity spent on options.
    """
    qqq_price = _to_float(qqq_price)
    vix_value = _to_float(vix_value)
    ma50 = _to_float(ma50)
    ma200 = _to_float(ma200)
    beta_delta_pct = None if beta_delta_pct is None else _to_float(beta_delta_pct)
    target_beta_delta_pct = None if target_beta_delta_pct is None else _to_float(target_beta_delta_pct)

    if qqq_price <= 0 or ma50 <= 0 or ma200 <= 0:
        return {
            "status": "DATA_INCOMPLETE",
            "action": "NO_TRADE",
            "reason": "QQQ price, MA50, and MA200 are required.",
        }

    if vix_value <= 15:
        vix_regime = "LOW_IV"
        vix_note = "Options are relatively cheap, but low IV alone is not a hedge trigger."
    elif vix_value <= 20:
        vix_regime = "NEUTRAL_IV"
        vix_note = "Use spreads if hedging; avoid casual long-put carry."
    else:
        vix_regime = "HIGH_IV"
        vix_note = "High IV makes naked long puts expensive; prefer spreads or reduce exposure."

    beta_delta_excess = (
        beta_delta_pct is not None
        and target_beta_delta_pct is not None
        and beta_delta_pct > target_beta_delta_pct
    )
    below_50 = qqq_price < ma50
    below_200 = qqq_price < ma200

    trigger_reasons = []
    if beta_delta_excess:
        trigger_reasons.append("BETA_DELTA_EXCESS")
    if event_risk:
        trigger_reasons.append("EVENT_RISK")
    if trend_break_confirmed:
        trigger_reasons.append("TREND_BREAK_CONFIRMED")
    if below_50:
        trigger_reasons.append("QQQ_BELOW_50DMA")
    if below_200:
        trigger_reasons.append("QQQ_BELOW_200DMA")

    if below_200 or trend_break_confirmed:
        action = "HEAVY_PROTECTION"
        coverage_pct = HEAVY_PROTECTION_COVERAGE_PCT
        max_cost_pct = HEAVY_MAX_COST_PCT
        buy_drop = 0.08
        spread_width_pct = 0.16
        reason = "Major trend break or 200DMA break."
    elif below_50 and (beta_delta_excess or event_risk):
        action = "LIGHT_PROTECTION"
        coverage_pct = LIGHT_PROTECTION_COVERAGE_PCT
        max_cost_pct = LIGHT_MAX_COST_PCT
        buy_drop = 0.04
        spread_width_pct = 0.08
        reason = "50DMA break plus portfolio or event-risk trigger."
    elif vix_value <= 15 and (beta_delta_excess or event_risk):
        action = "PRE_HEDGE_REVIEW"
        coverage_pct = LIGHT_PROTECTION_COVERAGE_PCT
        max_cost_pct = LIGHT_MAX_COST_PCT
        buy_drop = 0.04
        spread_width_pct = 0.08
        reason = "Cheap IV plus portfolio trigger; review one small spread, not a standing hedge."
    else:
        return {
            "status": "NO_HEDGE_NEEDED",
            "action": "NO_TRADE",
            "vix_regime": vix_regime,
            "vix_note": vix_note,
            "trigger_reasons": trigger_reasons,
            "coverage_pct": 0,
            "max_cost_pct": 0.0,
            "reason": "No sufficient hedge trigger. Keep cash or reduce exposure if worried.",
        }

    buy_put = _round_strike(qqq_price * (1 - buy_drop))
    sell_put = _round_strike(buy_put - (qqq_price * spread_width_pct))
    if sell_put >= buy_put:
        sell_put = buy_put - 5

    return {
        "status": "REVIEW_REQUIRED",
        "action": action,
        "vix_regime": vix_regime,
        "vix_note": vix_note,
        "trigger_reasons": trigger_reasons,
        "coverage_pct": coverage_pct,
        "max_cost_pct": max_cost_pct,
        "dte_target_min": 45,
        "dte_target_max": 90,
        "structure": "QQQ debit put spread",
        "buy_put": buy_put,
        "sell_put": sell_put,
        "spread_width": buy_put - sell_put,
        "reason": reason,
        "guardrail": (
            "Do not use naked long puts as a standing hedge. If the spread cannot "
            "fit the cost budget, reduce exposure instead."
        ),
    }


def evaluate_protective_put_hedges(
    positions: list[dict],
    *,
    equity: float,
    beta_delta_pct: float,
    target_beta_delta_pct: float,
    vix_spike: bool = False,
    event_risk: bool = False,
    trend_break: bool = False,
    today: _dt.date | None = None,
    max_campaign_cost_pct: float = DEFAULT_MAX_CAMPAIGN_COST_PCT,
) -> dict:
    """Evaluate whether current QQQ/SMH protective puts obey hedge discipline."""
    today = today or _dt.date.today()
    normalized = [_normalize_position(p) for p in positions]
    hedge_positions = [
        p for p in normalized
        if p["root"] in HEDGE_UNDERLYINGS and p["option_type"] == "put"
    ]
    long_puts = [p for p in hedge_positions if p["quantity"] > 0]

    trigger_reasons = []
    if beta_delta_pct > target_beta_delta_pct:
        trigger_reasons.append("BETA_DELTA_EXCESS")
    if vix_spike:
        trigger_reasons.append("VIX_SPIKE")
    if event_risk:
        trigger_reasons.append("EVENT_RISK")
    if trend_break:
        trigger_reasons.append("TREND_BREAK")
    trigger_active = bool(trigger_reasons)

    net_hedge_value = sum(p["market_value"] for p in hedge_positions)
    campaign_cost_pct = max(net_hedge_value, 0.0) / equity * 100 if equity else 0.0
    portfolio_issues = []
    if campaign_cost_pct > max_campaign_cost_pct:
        portfolio_issues.append({
            "code": "HEDGE_COST_OVER_BUDGET",
            "severity": "VIOLATION",
            "message": (
                f"Current hedge premium {campaign_cost_pct:.2f}% exceeds "
                f"{max_campaign_cost_pct:.2f}% campaign budget."
            ),
        })

    if not long_puts:
        status = "MISSING_HEDGE" if trigger_active else "NO_HEDGE_NEEDED"
        return {
            "status": status,
            "trigger_active": trigger_active,
            "trigger_reasons": trigger_reasons,
            "campaign_cost_pct": round(campaign_cost_pct, 2),
            "portfolio_issues": portfolio_issues,
            "rows": [],
            "summary": (
                "Risk trigger is active but no QQQ/SMH long put hedge is open."
                if trigger_active else
                "No active QQQ/SMH protective put is needed."
            ),
        }

    rows = []
    for long_put in long_puts:
        expiry_date = _parse_date(long_put["expiry"])
        dte = (expiry_date - today).days if expiry_date else None
        short_leg = _find_matching_short_put(long_put, normalized)
        structure = "PUT_SPREAD" if short_leg else "NAKED_LONG_PUT"
        issues = []

        if not trigger_active:
            issues.append({
                "code": "IDLE_HEDGE",
                "severity": "VIOLATION",
                "message": "No active system-risk trigger; do not keep paying long-put carry.",
            })
        if dte is None:
            issues.append({
                "code": "MISSING_DTE",
                "severity": "REVIEW",
                "message": "Expiry date is missing or unparseable.",
            })
        elif dte <= ROLL_REVIEW_DTE:
            issues.append({
                "code": "ROLL_OR_CLOSE_WINDOW",
                "severity": "REVIEW",
                "message": f"DTE {dte} <= {ROLL_REVIEW_DTE}; close, roll, or let expire only by plan.",
            })
        if structure == "NAKED_LONG_PUT":
            issues.append({
                "code": "PREFER_PUT_SPREAD",
                "severity": "REVIEW",
                "message": "Small-account protection should normally use put spreads to control cost.",
            })
            if dte is not None and dte > NAKED_PUT_MAX_DTE:
                issues.append({
                    "code": "NAKED_LONG_PUT_TOO_LONG",
                    "severity": "VIOLATION",
                    "message": (
                        f"Naked long put DTE {dte} exceeds {NAKED_PUT_MAX_DTE}; "
                        "avoid buy-and-hold insurance."
                    ),
                })
        elif dte is not None and dte > SPREAD_MAX_DTE:
            issues.append({
                "code": "SPREAD_TOO_LONG",
                "severity": "REVIEW",
                "message": f"Put spread DTE {dte} exceeds {SPREAD_MAX_DTE}; confirm event window.",
            })

        severities = {issue["severity"] for issue in issues}
        row_status = "VIOLATION" if "VIOLATION" in severities else "REVIEW" if issues else "VALID_HEDGE"
        rows.append({
            "symbol": long_put["symbol"],
            "root": long_put["root"],
            "structure": structure,
            "short_leg": short_leg["symbol"] if short_leg else None,
            "dte": dte,
            "status": row_status,
            "issues": issues,
        })

    all_issues = portfolio_issues + [issue for row in rows for issue in row["issues"]]
    severities = {issue["severity"] for issue in all_issues}
    if "VIOLATION" in severities:
        status = "VIOLATION"
        summary = "Protective put discipline is violated; review or exit the hedge."
    elif "REVIEW" in severities:
        status = "REVIEW"
        summary = "Protective put is allowed only with documented trigger, budget, and exit plan."
    else:
        status = "VALID_HEDGE"
        summary = "Protective put hedge is consistent with current governance rules."

    return {
        "status": status,
        "trigger_active": trigger_active,
        "trigger_reasons": trigger_reasons,
        "campaign_cost_pct": round(campaign_cost_pct, 2),
        "portfolio_issues": portfolio_issues,
        "rows": rows,
        "summary": summary,
    }
