"""Strict downstream adapters for ENERGREX mispricing cases.

This module does not calculate fair value, IDI, position size, or an order.
It validates ownership boundaries and routes already-computed outputs.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class PriceGate(str, Enum):
    PASS = "PASS"
    WAIT = "WAIT"
    FAIL = "FAIL"


class ExpressionRoute(str, Enum):
    STOCK_REVIEW = "STOCK_REVIEW"
    DEFINED_RISK_OPTION_REVIEW = "DEFINED_RISK_OPTION_REVIEW"
    WAIT = "WAIT"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ValuationBridgeResult:
    valuation_case_id: str
    source_snapshot_id: str
    ticker: str
    as_of_date: dt.date
    data_quality_status: str
    data_validity_rate: float
    methods: tuple[str, ...]
    method_values: Mapping[str, float]
    current_price: float
    bear_value: float
    base_value: float
    bull_value: float
    probability_weighted_value: float
    margin_of_safety: float
    upside_downside_ratio: float
    realization_months: int
    annualized_expected_return: float
    reverse_valuation_result: Any
    price_gate: PriceGate


@dataclass(frozen=True)
class ExpressionDecision:
    route: ExpressionRoute
    reasons: tuple[str, ...]


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    return result


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _date(value: Any, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def build_valuation_request(case: Mapping[str, Any]) -> dict[str, Any]:
    """Build assumptions-only input for the single ENERGREX valuation owner."""
    thesis = case.get("thesis")
    if not isinstance(thesis, Mapping):
        raise ValueError("thesis must be an object")
    request = {
        "schema_version": "2.3",
        "ticker": _text(case.get("ticker"), "ticker").upper(),
        "primary_path": _text(case.get("primary_path"), "primary_path"),
        "market_implied_expectation": _text(
            thesis.get("market_believes") or thesis.get("market_consensus"),
            "thesis.market_believes",
        ),
        "variant_assumptions": _text(
            thesis.get("variant_view"),
            "thesis.variant_view",
        ),
        "payer_stress_assumptions": dict(case.get("payer_economics") or {}),
        "capital_structure": dict(case.get("survival_gate") or {}),
        "value_capture_waterfall": dict(case.get("value_capture") or {}),
        "liquidation_value_if_applicable": case.get("liquidation_value"),
        "thesis_fail_conditions": _text(
            thesis.get("falsifiable_by"),
            "thesis.falsifiable_by",
        ),
    }
    # Deliberately exclude target prices and valuation scores. The valuation
    # owner must calculate them from timestamped normalized inputs.
    return request


def validate_valuation_output(
    output: Mapping[str, Any],
    *,
    expected_ticker: str,
) -> ValuationBridgeResult:
    """Validate a formal valuation result before it can close P5."""
    forbidden_score_only = (
        "valuation_score" in output
        and not all(
            field in output
            for field in ("bear_value", "base_value", "bull_value")
        )
    )
    if forbidden_score_only:
        raise ValueError("valuation_score alone cannot close the P5 price gate")

    ticker = _text(output.get("ticker"), "ticker").upper()
    if ticker != expected_ticker.strip().upper():
        raise ValueError("valuation ticker does not match mispricing case")
    status = _text(
        output.get("data_quality_status"),
        "data_quality_status",
    ).upper()
    validity = _finite(output.get("data_validity_rate"), "data_validity_rate")
    if status != "PASS" or validity < 0.95:
        raise ValueError("formal valuation requires PASS data quality and validity >= 95%")

    raw_methods = output.get("methods")
    if not isinstance(raw_methods, Sequence) or isinstance(raw_methods, (str, bytes)):
        raise ValueError("methods must be a list")
    methods = tuple(dict.fromkeys(_text(value, "methods") for value in raw_methods))
    if len(methods) < 2:
        raise ValueError("formal valuation requires at least two independent methods")
    raw_method_results = output.get("method_results")
    if not isinstance(raw_method_results, Mapping):
        raise ValueError("method_results must provide auditable output for each method")
    method_values: dict[str, float] = {}
    for method in methods:
        result = raw_method_results.get(method)
        if not isinstance(result, Mapping):
            raise ValueError(f"method_results.{method} is missing")
        if str(result.get("status") or "").upper() != "PASS":
            raise ValueError(f"method_results.{method} did not pass")
        value = _finite(result.get("fair_value"), f"method_results.{method}.fair_value")
        if value <= 0:
            raise ValueError(f"method_results.{method}.fair_value must be positive")
        method_values[method] = value
    dispersion = max(method_values.values()) / min(method_values.values()) - 1
    if dispersion > 0.35 and not str(output.get("dispersion_reconciliation") or "").strip():
        raise ValueError(
            "valuation methods diverge by more than 35%; dispersion_reconciliation is required"
        )

    current = _finite(output.get("current_price"), "current_price")
    bear = _finite(output.get("bear_value"), "bear_value")
    base = _finite(output.get("base_value"), "base_value")
    bull = _finite(output.get("bull_value"), "bull_value")
    if min(current, bear, base, bull) <= 0:
        raise ValueError("price and scenario values must be greater than zero")
    if not bear <= base <= bull:
        raise ValueError("scenario values must satisfy bear <= base <= bull")

    try:
        price_gate = PriceGate(str(output.get("price_gate")).upper())
    except ValueError as exc:
        raise ValueError("price_gate must be PASS, WAIT, or FAIL") from exc
    try:
        realization_months = int(output.get("realization_months"))
    except (TypeError, ValueError) as exc:
        raise ValueError("realization_months must be an integer") from exc
    if realization_months <= 0:
        raise ValueError("realization_months must be greater than zero")

    reverse_result = output.get("reverse_valuation_result")
    if reverse_result in (None, "", {}, []):
        raise ValueError("reverse_valuation_result is required")

    return ValuationBridgeResult(
        valuation_case_id=_text(output.get("valuation_case_id"), "valuation_case_id"),
        source_snapshot_id=_text(output.get("source_snapshot_id"), "source_snapshot_id"),
        ticker=ticker,
        as_of_date=_date(output.get("as_of_date"), "as_of_date"),
        data_quality_status=status,
        data_validity_rate=validity,
        methods=methods,
        method_values=method_values,
        current_price=current,
        bear_value=bear,
        base_value=base,
        bull_value=bull,
        probability_weighted_value=_finite(
            output.get("probability_weighted_value"),
            "probability_weighted_value",
        ),
        margin_of_safety=_finite(output.get("margin_of_safety"), "margin_of_safety"),
        upside_downside_ratio=_finite(
            output.get("upside_downside_ratio"),
            "upside_downside_ratio",
        ),
        realization_months=realization_months,
        annualized_expected_return=_finite(
            output.get("annualized_expected_return"),
            "annualized_expected_return",
        ),
        reverse_valuation_result=reverse_result,
        price_gate=price_gate,
    )


def route_portfolio_expression(
    *,
    mispricing_decision: str,
    valuation: ValuationBridgeResult | None,
    option: Mapping[str, Any] | None = None,
    minimum_time_buffer_days: int = 30,
) -> ExpressionDecision:
    """Route to a review queue; never emit BUY or an order."""
    mispricing = str(mispricing_decision or "").strip().upper()
    if mispricing in {"REJECT_P3", "WATCH_P2", ""}:
        return ExpressionDecision(
            ExpressionRoute.BLOCKED,
            ("mispricing evidence or a hard gate has not passed",),
        )
    if valuation is None:
        return ExpressionDecision(
            ExpressionRoute.BLOCKED,
            ("formal valuation output is missing",),
        )
    if valuation.price_gate == PriceGate.PASS:
        return ExpressionDecision(
            ExpressionRoute.STOCK_REVIEW,
            ("price gate passed; hand off for portfolio review",),
        )

    if mispricing not in {
        "DEEP_RESEARCH_P0",
        "DEEP_RESEARCH_P1",
        "WAIT_FOR_PRICE",
    }:
        return ExpressionDecision(
            ExpressionRoute.BLOCKED,
            (f"unsupported mispricing state: {mispricing}",),
        )
    if option is None:
        return ExpressionDecision(
            ExpressionRoute.WAIT,
            ("stock price gate did not pass and no defined-risk option was supplied",),
        )

    reasons: list[str] = []
    if option.get("defined_risk") is not True:
        reasons.append("option structure is not explicitly defined-risk")
    catalyst = _date(option.get("catalyst_date"), "option.catalyst_date")
    expiry = _date(option.get("expiry"), "option.expiry")
    if expiry < catalyst + dt.timedelta(days=minimum_time_buffer_days):
        reasons.append("expiry does not leave the required catalyst buffer")

    maximum_loss = _finite(option.get("maximum_loss"), "option.maximum_loss")
    loss_budget = _finite(
        option.get("portfolio_loss_budget"),
        "option.portfolio_loss_budget",
    )
    if maximum_loss <= 0 or loss_budget <= 0 or maximum_loss > loss_budget:
        reasons.append("maximum loss exceeds the portfolio loss budget")

    break_even = _finite(option.get("break_even"), "option.break_even")
    if break_even > valuation.base_value:
        reasons.append("option break-even exceeds base fair value")
    upside_cap_raw = option.get("upside_cap")
    if upside_cap_raw is not None and _finite(upside_cap_raw, "option.upside_cap") < valuation.base_value:
        reasons.append("option upside cap is below base fair value")

    spread = _finite(
        option.get("bid_ask_spread_pct"),
        "option.bid_ask_spread_pct",
    )
    if spread > 0.10:
        reasons.append("bid/ask spread exceeds 10%")
    try:
        open_interest = int(option.get("open_interest"))
    except (TypeError, ValueError) as exc:
        raise ValueError("option.open_interest must be an integer") from exc
    if open_interest < 50:
        reasons.append("open interest is below 50")
    if option.get("portfolio_limits_pass") is not True:
        reasons.append("portfolio concentration or expiry limits have not passed")

    if reasons:
        return ExpressionDecision(ExpressionRoute.WAIT, tuple(reasons))
    return ExpressionDecision(
        ExpressionRoute.DEFINED_RISK_OPTION_REVIEW,
        ("all option eligibility gates passed; human portfolio review is still required",),
    )
