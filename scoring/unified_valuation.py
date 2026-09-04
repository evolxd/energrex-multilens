"""ENERGREX unified, point-in-time equity valuation service.

The service owns formal fair values for V2.3 cases. It does not calculate IDI,
position size, option Greeks, or orders.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Sequence


MODEL_VERSION = "UNIFIED_VALUATION_1.0"
ALLOWED_SOURCE_TYPES = {"PRIMARY", "MARKET", "CONSENSUS", "MODEL"}
SCENARIOS = ("bear", "base", "bull")


class ValuationProfile(str, Enum):
    MATURE_PROFITABLE = "MATURE_PROFITABLE"
    HIGH_GROWTH = "HIGH_GROWTH"
    FINANCIAL = "FINANCIAL"
    ASSET_NAV = "ASSET_NAV"


@dataclass(frozen=True)
class FieldSpec:
    unit: str
    max_age_days: int
    cross_check_required: bool = True


COMMON_SPECS = {
    "current_price": FieldSpec("USD/share", 3),
    "diluted_shares": FieldSpec("shares", 130),
}

FIELD_CROSSCHECK_LIMITS = {
    "current_price": 0.01,
    "diluted_shares": 0.01,
    "net_cash": 0.02,
    "revenue_ttm": 0.01,
    "forward_eps": 0.05,
    "forward_revenue": 0.03,
    "book_value_per_share": 0.01,
    "gross_asset_value": 0.05,
    "total_claims": 0.02,
}
FIELD_CROSSCHECK_MODES = {
    "current_price": "INDEPENDENT_ORIGIN",
    "diluted_shares": "INDEPENDENT_EXTRACTION",
    "net_cash": "INDEPENDENT_EXTRACTION",
    "revenue_ttm": "INDEPENDENT_EXTRACTION",
    "forward_eps": "INDEPENDENT_ORIGIN",
    "forward_revenue": "INDEPENDENT_ORIGIN",
    "book_value_per_share": "INDEPENDENT_EXTRACTION",
    "gross_asset_value": "INDEPENDENT_EXTRACTION",
    "total_claims": "INDEPENDENT_EXTRACTION",
}

PROFILE_SPECS = {
    ValuationProfile.MATURE_PROFITABLE: {
        **COMMON_SPECS,
        "net_cash": FieldSpec("USD", 130),
        "revenue_ttm": FieldSpec("USD", 130),
        "forward_eps": FieldSpec("USD/share", 45),
    },
    ValuationProfile.HIGH_GROWTH: {
        **COMMON_SPECS,
        "net_cash": FieldSpec("USD", 130),
        "revenue_ttm": FieldSpec("USD", 130),
        "forward_revenue": FieldSpec("USD", 45),
    },
    ValuationProfile.FINANCIAL: {
        **COMMON_SPECS,
        "book_value_per_share": FieldSpec("USD/share", 130),
    },
    ValuationProfile.ASSET_NAV: {
        **COMMON_SPECS,
        "gross_asset_value": FieldSpec("USD", 130),
        "total_claims": FieldSpec("USD", 130),
    },
}


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _positive(value: Any, field: str) -> float:
    number = _finite(value, field)
    if number <= 0:
        raise ValueError(f"{field} must be greater than zero")
    return number


def _nonnegative(value: Any, field: str) -> float:
    number = _finite(value, field)
    if number < 0:
        raise ValueError(f"{field} must be non-negative")
    return number


def _rate(value: Any, field: str, *, low: float = -1.0, high: float = 2.0) -> float:
    number = _finite(value, field)
    if not low <= number <= high:
        raise ValueError(f"{field} must be between {low} and {high}")
    return number


def _iso_datetime(value: Any, field: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def _iso_date(value: Any, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _profile(value: Any) -> ValuationProfile:
    try:
        return ValuationProfile(str(value))
    except ValueError as exc:
        raise ValueError(f"Unsupported valuation profile: {value}") from exc


def _field_values_and_quality(
    request: Mapping[str, Any],
    profile: ValuationProfile,
) -> tuple[dict[str, float], dict[str, Any]]:
    raw_fields = request.get("fields")
    if not isinstance(raw_fields, Mapping):
        raise ValueError("fields must be an object")
    as_of = _iso_datetime(request.get("as_of"), "as_of")
    values: dict[str, float] = {}
    results: dict[str, Any] = {}
    valid_count = 0
    critical_veto = False

    for name, spec in PROFILE_SPECS[profile].items():
        record = raw_fields.get(name)
        reasons: list[str] = []
        valid = True
        if not isinstance(record, Mapping):
            valid = False
            reasons.append("MISSING")
        else:
            try:
                values[name] = _finite(record.get("value"), f"fields.{name}.value")
            except ValueError:
                valid = False
                reasons.append("INVALID_VALUE")
            if record.get("unit") != spec.unit:
                valid = False
                reasons.append("UNIT_MISMATCH")
            if not str(record.get("source") or "").strip():
                valid = False
                reasons.append("SOURCE_MISSING")
            source_family = str(record.get("source_family") or "").strip().upper()
            origin_family = str(record.get("origin_family") or "").strip().upper()
            lineage_id = str(record.get("lineage_id") or "").strip()
            source_locator = str(record.get("source_locator") or "").strip()
            if not source_family:
                valid = False
                reasons.append("SOURCE_FAMILY_MISSING")
            if not origin_family:
                valid = False
                reasons.append("ORIGIN_FAMILY_MISSING")
            if not lineage_id:
                valid = False
                reasons.append("LINEAGE_ID_MISSING")
            if not source_locator:
                valid = False
                reasons.append("SOURCE_LOCATOR_MISSING")
            if str(record.get("source_type") or "") not in ALLOWED_SOURCE_TYPES:
                valid = False
                reasons.append("SOURCE_TYPE_INVALID")
            try:
                observed = _iso_datetime(
                    record.get("observed_at"),
                    f"fields.{name}.observed_at",
                )
                available = _iso_datetime(
                    record.get("available_at"),
                    f"fields.{name}.available_at",
                )
                retrieved = _iso_datetime(
                    record.get("retrieved_at"),
                    f"fields.{name}.retrieved_at",
                )
                if observed > available:
                    valid = False
                    reasons.append("OBSERVED_AFTER_AVAILABLE")
                if available > retrieved:
                    valid = False
                    reasons.append("AVAILABLE_AFTER_RETRIEVED")
                if available > as_of or retrieved > as_of:
                    valid = False
                    reasons.append("LOOKAHEAD")
                if (as_of - available).total_seconds() / 86400 > spec.max_age_days:
                    valid = False
                    reasons.append("STALE")
            except ValueError:
                valid = False
                reasons.append("TIMESTAMP_INVALID")
            verification = record.get("verification")
            if not isinstance(verification, Mapping) or (
                verification.get("status") != "VERIFIED"
            ):
                valid = False
                reasons.append("UNVERIFIED")
            if spec.cross_check_required:
                if (
                    not isinstance(verification, Mapping)
                    or not str(verification.get("secondary_source") or "").strip()
                ):
                    valid = False
                    reasons.append("CROSS_CHECK_MISSING")
                else:
                    secondary_family = str(
                        verification.get("secondary_source_family") or ""
                    ).strip().upper()
                    secondary_origin = str(
                        verification.get("secondary_origin_family") or ""
                    ).strip().upper()
                    secondary_lineage = str(
                        verification.get("secondary_lineage_id") or ""
                    ).strip()
                    secondary_locator = str(
                        verification.get("secondary_source_locator") or ""
                    ).strip()
                    secondary_extraction = str(
                        verification.get("secondary_extraction_method") or ""
                    ).strip()
                    cross_check_mode = str(
                        verification.get("cross_check_mode") or ""
                    ).strip().upper()
                    if not secondary_family or secondary_family == source_family:
                        valid = False
                        reasons.append("CROSS_CHECK_NOT_INDEPENDENT")
                    if not secondary_locator or secondary_locator == source_locator:
                        valid = False
                        reasons.append("CROSS_CHECK_LOCATOR_NOT_INDEPENDENT")
                    if not secondary_origin or not secondary_lineage:
                        valid = False
                        reasons.append("CROSS_CHECK_LINEAGE_MISSING")
                    if cross_check_mode not in {
                        "INDEPENDENT_ORIGIN",
                        "INDEPENDENT_EXTRACTION",
                    }:
                        valid = False
                        reasons.append("CROSS_CHECK_MODE_INVALID")
                    elif cross_check_mode != FIELD_CROSSCHECK_MODES[name]:
                        valid = False
                        reasons.append("CROSS_CHECK_MODE_MISMATCH")
                    elif (
                        cross_check_mode == "INDEPENDENT_ORIGIN"
                        and secondary_origin == origin_family
                    ):
                        valid = False
                        reasons.append("CROSS_CHECK_ORIGIN_NOT_INDEPENDENT")
                    elif (
                        cross_check_mode == "INDEPENDENT_EXTRACTION"
                        and (
                            not secondary_extraction
                            or secondary_extraction
                            == str(record.get("extraction_method") or "").strip()
                        )
                    ):
                        valid = False
                        reasons.append("CROSS_CHECK_EXTRACTION_NOT_INDEPENDENT")
                    try:
                        secondary_available = _iso_datetime(
                            verification.get("secondary_available_at"),
                            f"fields.{name}.verification.secondary_available_at",
                        )
                        secondary_retrieved = _iso_datetime(
                            verification.get("secondary_retrieved_at"),
                            f"fields.{name}.verification.secondary_retrieved_at",
                        )
                        if secondary_available > secondary_retrieved:
                            valid = False
                            reasons.append("SECONDARY_AVAILABLE_AFTER_RETRIEVED")
                        if secondary_available > as_of or secondary_retrieved > as_of:
                            valid = False
                            reasons.append("SECONDARY_LOOKAHEAD")
                    except ValueError:
                        valid = False
                        reasons.append("SECONDARY_TIMESTAMP_INVALID")
                    try:
                        difference = _nonnegative(
                            verification.get("relative_difference"),
                            f"fields.{name}.verification.relative_difference",
                        )
                        tolerance = _nonnegative(
                            verification.get("tolerance"),
                            f"fields.{name}.verification.tolerance",
                        )
                        maximum = FIELD_CROSSCHECK_LIMITS[name]
                        if tolerance > maximum:
                            valid = False
                            reasons.append("CROSS_CHECK_TOLERANCE_TOO_WIDE")
                        if difference > tolerance:
                            valid = False
                            reasons.append("CROSS_CHECK_CONFLICT")
                    except (KeyError, ValueError):
                        valid = False
                        reasons.append("CROSS_CHECK_METRICS_INVALID")
        if valid:
            valid_count += 1
        else:
            critical_veto = True
        results[name] = {"valid": valid, "reasons": reasons}

    total = len(PROFILE_SPECS[profile])
    validity_rate = round(valid_count / total, 4) if total else 0.0
    status = (
        "PASS"
        if validity_rate >= 0.95 and not critical_veto
        else "PARTIAL"
        if validity_rate >= 0.85 and not critical_veto
        else "REVIEW_REQUIRED"
    )
    return values, {
        "status": status,
        "validity_rate": validity_rate,
        "critical_veto": critical_veto,
        "field_results": results,
        "missing_or_invalid_fields": [
            name for name, result in results.items() if not result["valid"]
        ],
    }


def _scenario(request: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    scenarios = request.get("scenarios")
    if not isinstance(scenarios, Mapping):
        raise ValueError("scenarios must be an object")
    result = scenarios.get(name)
    if not isinstance(result, Mapping):
        raise ValueError(f"scenarios.{name} must be an object")
    if not str(result.get("assumption_basis") or "").strip():
        raise ValueError(f"scenarios.{name}.assumption_basis is required")
    return result


def _series(value: Any, field: str) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a list")
    result = [_rate(item, field) for item in value]
    if not result:
        raise ValueError(f"{field} must not be empty")
    return result


def _dcf(
    scenario: Mapping[str, Any],
    *,
    revenue_start: float,
    shares: float,
    net_cash: float,
) -> dict[str, Any]:
    growth = _series(scenario.get("revenue_growth"), "revenue_growth")
    margins = _series(scenario.get("fcf_margin"), "fcf_margin")
    if len(growth) != len(margins):
        raise ValueError("revenue_growth and fcf_margin must have equal lengths")
    discount = _rate(scenario.get("discount_rate"), "discount_rate", low=0.001, high=0.50)
    terminal_growth = _rate(
        scenario.get("terminal_growth"),
        "terminal_growth",
        low=-0.20,
        high=0.10,
    )
    if discount <= terminal_growth:
        raise ValueError("discount_rate must be greater than terminal_growth")
    revenue = revenue_start
    pv_fcf = 0.0
    yearly: list[dict[str, float]] = []
    final_fcf = 0.0
    for year, (growth_rate, margin) in enumerate(zip(growth, margins), 1):
        revenue *= 1 + growth_rate
        fcf = revenue * margin
        pv = fcf / (1 + discount) ** year
        pv_fcf += pv
        final_fcf = fcf
        yearly.append({"year": year, "revenue": revenue, "fcf": fcf, "pv_fcf": pv})
    terminal = final_fcf * (1 + terminal_growth) / (discount - terminal_growth)
    pv_terminal = terminal / (1 + discount) ** len(growth)
    equity = pv_fcf + pv_terminal + net_cash
    return {
        "status": "PASS",
        "fair_value": equity / shares,
        "equity_value": equity,
        "pv_explicit_fcf": pv_fcf,
        "pv_terminal_value": pv_terminal,
        "terminal_value_share": pv_terminal / (pv_fcf + pv_terminal)
        if pv_fcf + pv_terminal
        else None,
        "yearly": yearly,
    }


def _pe(scenario: Mapping[str, Any], *, fallback_eps: float) -> dict[str, Any]:
    eps = _finite(scenario.get("forward_eps", fallback_eps), "forward_eps")
    multiple = _positive(scenario.get("target_pe"), "target_pe")
    if eps <= 0:
        raise ValueError("forward_eps must be positive for P/E")
    return {
        "status": "PASS",
        "fair_value": eps * multiple,
        "forward_eps": eps,
        "target_pe": multiple,
    }


def _ev_sales(
    scenario: Mapping[str, Any],
    *,
    fallback_revenue: float,
    shares: float,
    net_cash: float,
) -> dict[str, Any]:
    revenue = _positive(
        scenario.get("forward_revenue", fallback_revenue),
        "forward_revenue",
    )
    multiple = _positive(scenario.get("target_ev_sales"), "target_ev_sales")
    enterprise_value = revenue * multiple
    equity_value = enterprise_value + net_cash
    return {
        "status": "PASS",
        "fair_value": equity_value / shares,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "forward_revenue": revenue,
        "target_ev_sales": multiple,
    }


def _justified_pb(
    scenario: Mapping[str, Any],
    *,
    book_value_per_share: float,
) -> dict[str, Any]:
    roe = _rate(scenario.get("normalized_roe"), "normalized_roe", low=-0.50, high=1.50)
    cost = _rate(scenario.get("cost_of_equity"), "cost_of_equity", low=0.001, high=0.50)
    growth = _rate(scenario.get("terminal_growth"), "terminal_growth", low=-0.20, high=0.10)
    if cost <= growth:
        raise ValueError("cost_of_equity must exceed terminal_growth")
    justified_multiple = (roe - growth) / (cost - growth)
    if justified_multiple <= 0:
        raise ValueError("justified P/B is non-positive")
    return {
        "status": "PASS",
        "fair_value": book_value_per_share * justified_multiple,
        "justified_pb": justified_multiple,
        "normalized_roe": roe,
        "cost_of_equity": cost,
    }


def _residual_income(
    scenario: Mapping[str, Any],
    *,
    book_value_per_share: float,
) -> dict[str, Any]:
    roe_path = _series(scenario.get("roe_path"), "roe_path")
    cost = _rate(scenario.get("cost_of_equity"), "cost_of_equity", low=0.001, high=0.50)
    payout = _rate(scenario.get("payout_ratio"), "payout_ratio", low=0.0, high=1.0)
    terminal_roe = _rate(
        scenario.get("terminal_roe"),
        "terminal_roe",
        low=-0.50,
        high=1.50,
    )
    terminal_growth = _rate(
        scenario.get("terminal_growth"),
        "terminal_growth",
        low=-0.20,
        high=0.10,
    )
    if cost <= terminal_growth:
        raise ValueError("cost_of_equity must exceed terminal_growth")
    book = book_value_per_share
    pv_residual = 0.0
    yearly: list[dict[str, float]] = []
    for year, roe in enumerate(roe_path, 1):
        earnings = roe * book
        residual = (roe - cost) * book
        pv = residual / (1 + cost) ** year
        pv_residual += pv
        yearly.append(
            {"year": year, "beginning_book": book, "residual_income": residual}
        )
        book += earnings * (1 - payout)
    terminal_residual = (terminal_roe - cost) * book
    terminal_value = terminal_residual / (cost - terminal_growth)
    pv_terminal = terminal_value / (1 + cost) ** len(roe_path)
    fair_value = book_value_per_share + pv_residual + pv_terminal
    if fair_value <= 0:
        raise ValueError("residual income value is non-positive")
    return {
        "status": "PASS",
        "fair_value": fair_value,
        "pv_residual_income": pv_residual,
        "pv_terminal_residual_income": pv_terminal,
        "yearly": yearly,
    }


def _adjusted_nav(
    scenario: Mapping[str, Any],
    *,
    gross_asset_value: float,
    total_claims: float,
    shares: float,
) -> dict[str, Any]:
    recovery = _rate(
        scenario.get("nav_recovery_rate"),
        "nav_recovery_rate",
        low=0.0,
        high=1.50,
    )
    operating = _finite(
        scenario.get("operating_business_value", 0),
        "operating_business_value",
    )
    hidden = _finite(scenario.get("hidden_asset_value", 0), "hidden_asset_value")
    operating = _nonnegative(operating, "operating_business_value")
    hidden = _nonnegative(hidden, "hidden_asset_value")
    costs = _nonnegative(scenario.get("transaction_cost", 0), "transaction_cost")
    common = gross_asset_value * recovery + operating + hidden - total_claims - costs
    if common <= 0:
        raise ValueError("adjusted NAV leaves no positive value for common equity")
    return {
        "status": "PASS",
        "fair_value": common / shares,
        "common_equity_value": common,
        "nav_recovery_rate": recovery,
    }


def _liquidation_recovery(
    scenario: Mapping[str, Any],
    *,
    gross_asset_value: float,
    total_claims: float,
    shares: float,
) -> dict[str, Any]:
    recovery = _rate(
        scenario.get("liquidation_recovery_rate"),
        "liquidation_recovery_rate",
        low=0.0,
        high=1.0,
    )
    hidden = _nonnegative(
        scenario.get("hidden_liquidation_value", 0),
        "hidden_liquidation_value",
    )
    wind_down = _nonnegative(scenario.get("wind_down_cost", 0), "wind_down_cost")
    cash_burn = _nonnegative(scenario.get("cash_burn", 0), "cash_burn")
    common = (
        gross_asset_value * recovery
        + hidden
        - total_claims
        - wind_down
        - cash_burn
    )
    if common <= 0:
        raise ValueError("liquidation leaves no positive value for common equity")
    return {
        "status": "PASS",
        "fair_value": common / shares,
        "common_equity_value": common,
        "liquidation_recovery_rate": recovery,
    }


def _method_functions(
    profile: ValuationProfile,
    values: Mapping[str, float],
) -> Mapping[str, Callable[[Mapping[str, Any]], dict[str, Any]]]:
    shares = _positive(values.get("diluted_shares"), "diluted_shares")
    if profile == ValuationProfile.MATURE_PROFITABLE:
        return {
            "DCF": lambda scenario: _dcf(
                scenario,
                revenue_start=_positive(values.get("revenue_ttm"), "revenue_ttm"),
                shares=shares,
                net_cash=_finite(values.get("net_cash"), "net_cash"),
            ),
            "FORWARD_PE": lambda scenario: _pe(
                scenario,
                fallback_eps=_finite(values.get("forward_eps"), "forward_eps"),
            ),
        }
    if profile == ValuationProfile.HIGH_GROWTH:
        return {
            "DCF": lambda scenario: _dcf(
                scenario,
                revenue_start=_positive(values.get("revenue_ttm"), "revenue_ttm"),
                shares=shares,
                net_cash=_finite(values.get("net_cash"), "net_cash"),
            ),
            "EV_SALES": lambda scenario: _ev_sales(
                scenario,
                fallback_revenue=_positive(
                    values.get("forward_revenue"),
                    "forward_revenue",
                ),
                shares=shares,
                net_cash=_finite(values.get("net_cash"), "net_cash"),
            ),
        }
    if profile == ValuationProfile.FINANCIAL:
        book = _positive(values.get("book_value_per_share"), "book_value_per_share")
        return {
            "JUSTIFIED_PB": lambda scenario: _justified_pb(
                scenario,
                book_value_per_share=book,
            ),
            "RESIDUAL_INCOME": lambda scenario: _residual_income(
                scenario,
                book_value_per_share=book,
            ),
        }
    gross = _positive(values.get("gross_asset_value"), "gross_asset_value")
    claims = _nonnegative(values.get("total_claims"), "total_claims")
    return {
        "ADJUSTED_NAV": lambda scenario: _adjusted_nav(
            scenario,
            gross_asset_value=gross,
            total_claims=claims,
            shares=shares,
        ),
        "LIQUIDATION_RECOVERY": lambda scenario: _liquidation_recovery(
            scenario,
            gross_asset_value=gross,
            total_claims=claims,
            shares=shares,
        ),
    }


def _blend_methods(
    method_results: Mapping[str, Mapping[str, Any]],
    scenario: Mapping[str, Any],
) -> tuple[float, float, dict[str, float]]:
    values = {
        name: _positive(result.get("fair_value"), f"{name}.fair_value")
        for name, result in method_results.items()
        if result.get("status") == "PASS"
    }
    if not values:
        raise ValueError("no valuation method passed")
    raw_weights = scenario.get("method_weights") or {}
    if raw_weights and not isinstance(raw_weights, Mapping):
        raise ValueError("method_weights must be an object")
    weights = {
        name: _finite(raw_weights.get(name, 1.0), f"method_weights.{name}")
        for name in values
    }
    if any(weight <= 0 for weight in weights.values()) or sum(weights.values()) <= 0:
        raise ValueError("method weights must be positive")
    total_weight = sum(weights.values())
    normalized = {name: weight / total_weight for name, weight in weights.items()}
    if len(normalized) > 1 and min(normalized.values()) < 0.20:
        raise ValueError("each formal valuation method must carry at least 20% weight")
    blended = sum(values[name] * normalized[name] for name in values)
    dispersion = max(values.values()) / min(values.values()) - 1 if len(values) > 1 else 0
    return blended, dispersion, normalized


def _reverse_dcf_growth(
    *,
    market_price: float,
    revenue_start: float,
    shares: float,
    net_cash: float,
    scenario: Mapping[str, Any],
) -> dict[str, Any]:
    margins = _series(scenario.get("fcf_margin"), "fcf_margin")
    years = len(margins)
    low, high = -0.50, 1.00
    for _ in range(100):
        mid = (low + high) / 2
        trial = dict(scenario)
        trial["revenue_growth"] = [mid] * years
        value = _dcf(
            trial,
            revenue_start=revenue_start,
            shares=shares,
            net_cash=net_cash,
        )["fair_value"]
        if value < market_price:
            low = mid
        else:
            high = mid
    return {
        "method": "REVERSE_DCF",
        "market_implied_constant_revenue_growth": (low + high) / 2,
        "years": years,
        "assumption": "constant annual revenue growth with base-scenario margin path",
    }


def _reverse_valuation(
    profile: ValuationProfile,
    values: Mapping[str, float],
    base_scenario: Mapping[str, Any],
) -> dict[str, Any]:
    price = _positive(values.get("current_price"), "current_price")
    if profile in {
        ValuationProfile.MATURE_PROFITABLE,
        ValuationProfile.HIGH_GROWTH,
    }:
        return _reverse_dcf_growth(
            market_price=price,
            revenue_start=_positive(values.get("revenue_ttm"), "revenue_ttm"),
            shares=_positive(values.get("diluted_shares"), "diluted_shares"),
            net_cash=_finite(values.get("net_cash"), "net_cash"),
            scenario=base_scenario,
        )
    if profile == ValuationProfile.FINANCIAL:
        book = _positive(values.get("book_value_per_share"), "book_value_per_share")
        cost = _rate(base_scenario.get("cost_of_equity"), "cost_of_equity", low=0.001, high=0.50)
        growth = _rate(base_scenario.get("terminal_growth"), "terminal_growth", low=-0.20, high=0.10)
        implied_pb = price / book
        implied_roe = implied_pb * (cost - growth) + growth
        return {
            "method": "REVERSE_JUSTIFIED_PB",
            "market_implied_pb": implied_pb,
            "market_implied_normalized_roe": implied_roe,
        }
    gross = _positive(values.get("gross_asset_value"), "gross_asset_value")
    claims = _finite(values.get("total_claims"), "total_claims")
    shares = _positive(values.get("diluted_shares"), "diluted_shares")
    operating = _finite(
        base_scenario.get("operating_business_value", 0),
        "operating_business_value",
    )
    hidden = _finite(base_scenario.get("hidden_asset_value", 0), "hidden_asset_value")
    costs = _finite(base_scenario.get("transaction_cost", 0), "transaction_cost")
    implied_recovery = (price * shares + claims + costs - operating - hidden) / gross
    return {
        "method": "REVERSE_NAV",
        "market_implied_gross_asset_recovery_rate": implied_recovery,
    }


def run_unified_valuation(request: Mapping[str, Any]) -> dict[str, Any]:
    """Run the formal valuation service and return the V2.3 output contract."""
    if str(request.get("schema_version") or "") != "1.0":
        raise ValueError("schema_version must be 1.0")
    evidence_mode = str(request.get("evidence_mode") or "").strip().upper()
    if evidence_mode not in {"LIVE", "ENGINEERING_ONLY"}:
        raise ValueError("evidence_mode must be LIVE or ENGINEERING_ONLY")
    evidence_snapshot_id = str(request.get("evidence_snapshot_id") or "").strip()
    if evidence_mode == "LIVE" and not evidence_snapshot_id:
        raise ValueError("LIVE valuation requires evidence_snapshot_id")
    ticker = str(request.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("ticker is required")
    valuation_case_id = str(request.get("valuation_case_id") or "").strip()
    if not valuation_case_id:
        raise ValueError("valuation_case_id is required")
    profile = _profile(request.get("profile"))
    as_of = _iso_datetime(request.get("as_of"), "as_of")
    as_of_date = _iso_date(request.get("as_of_date"), "as_of_date")
    if as_of.date() != as_of_date:
        raise ValueError("as_of and as_of_date must refer to the same date")

    values, quality = _field_values_and_quality(request, profile)
    snapshot_payload = {
        "ticker": ticker,
        "profile": profile.value,
        "as_of": as_of.isoformat(),
        "evidence_mode": evidence_mode,
        "evidence_snapshot_id": evidence_snapshot_id or None,
        "fields": request.get("fields"),
    }
    source_snapshot_id = _stable_hash(snapshot_payload)

    method_functions = _method_functions(profile, values)
    scenario_outputs: dict[str, Any] = {}
    all_method_names: set[str] = set()
    method_failure = False
    excessive_dispersion = False
    reconciliation = str(request.get("dispersion_reconciliation") or "").strip()

    for scenario_name in SCENARIOS:
        assumptions = _scenario(request, scenario_name)
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for name, function in method_functions.items():
            try:
                results[name] = function(assumptions)
                all_method_names.add(name)
            except ValueError as exc:
                errors[name] = str(exc)
        if len(results) < 2:
            method_failure = True
        blended, dispersion, weights = _blend_methods(results, assumptions)
        if dispersion > 0.35 and not reconciliation:
            excessive_dispersion = True
        scenario_outputs[scenario_name] = {
            "fair_value": blended,
            "method_results": results,
            "method_errors": errors,
            "method_weights": weights,
            "dispersion_pct": dispersion * 100,
            "assumptions": dict(assumptions),
        }

    bear = scenario_outputs["bear"]["fair_value"]
    base = scenario_outputs["base"]["fair_value"]
    bull = scenario_outputs["bull"]["fair_value"]
    scenario_order_valid = bear <= base <= bull
    probabilities = request.get("scenario_probabilities") or {
        "bear": 0.25,
        "base": 0.50,
        "bull": 0.25,
    }
    if not isinstance(probabilities, Mapping):
        raise ValueError("scenario_probabilities must be an object")
    probability_values = {
        name: _rate(probabilities.get(name), f"scenario_probabilities.{name}", low=0, high=1)
        for name in SCENARIOS
    }
    if abs(sum(probability_values.values()) - 1.0) > 1e-9:
        raise ValueError("scenario probabilities must sum to 1")
    probability_weighted = sum(
        scenario_outputs[name]["fair_value"] * probability_values[name]
        for name in SCENARIOS
    )

    try:
        realization_months = int(request.get("realization_months"))
    except (TypeError, ValueError) as exc:
        raise ValueError("realization_months must be an integer") from exc
    if realization_months <= 0:
        raise ValueError("realization_months must be greater than zero")

    formal_status = quality["status"]
    review_reasons = list(quality["missing_or_invalid_fields"])
    if method_failure:
        formal_status = "PARTIAL" if formal_status == "PASS" else formal_status
        review_reasons.append("fewer than two methods passed in at least one scenario")
    if excessive_dispersion:
        formal_status = "REVIEW_REQUIRED"
        review_reasons.append("method dispersion exceeds 35% without reconciliation")
    if not scenario_order_valid:
        formal_status = "REVIEW_REQUIRED"
        review_reasons.append("scenario values do not satisfy bear <= base <= bull")
    if evidence_mode == "ENGINEERING_ONLY":
        formal_status = "REVIEW_REQUIRED"
        review_reasons.append("synthetic engineering fixtures cannot support investment action")

    current = _positive(values.get("current_price"), "current_price")
    base_upside = base / current - 1
    downside = max(current - bear, 0)
    upside_downside_ratio = (
        (base - current) / downside
        if downside > 0
        else 999.0
        if base > current
        else 0.0
    )
    margin_of_safety = (base - current) / base
    annualized = (probability_weighted / current) ** (12 / realization_months) - 1

    if formal_status != "PASS":
        price_gate = "FAIL"
        boundary = "REVIEW_REQUIRED"
    elif base_upside >= 0.25 and upside_downside_ratio >= 2.0:
        price_gate = "PASS"
        boundary = "CHEAP"
    elif base > current:
        price_gate = "WAIT"
        boundary = "FAIR"
    else:
        price_gate = "FAIL"
        boundary = "EXPENSIVE"

    base_methods = scenario_outputs["base"]["method_results"]
    reverse = _reverse_valuation(
        profile,
        values,
        _scenario(request, "base"),
    )
    return {
        "model_version": MODEL_VERSION,
        "schema_version": "1.0",
        "valuation_case_id": valuation_case_id,
        "source_snapshot_id": source_snapshot_id,
        "evidence_snapshot_id": evidence_snapshot_id or None,
        "ticker": ticker,
        "profile": profile.value,
        "evidence_mode": evidence_mode,
        "as_of_date": as_of_date.isoformat(),
        "data_quality_status": formal_status,
        "data_validity_rate": quality["validity_rate"],
        "data_quality": quality,
        "methods": sorted(base_methods),
        "method_results": base_methods,
        "dispersion_reconciliation": reconciliation or None,
        "current_price": current,
        "bear_value": bear,
        "base_value": base,
        "bull_value": bull,
        "probability_weighted_value": probability_weighted,
        "scenario_probabilities": probability_values,
        "scenarios": scenario_outputs,
        "reverse_valuation_result": reverse,
        "margin_of_safety": margin_of_safety,
        "upside_downside_ratio": upside_downside_ratio,
        "realization_months": realization_months,
        "annualized_expected_return": annualized,
        "price_gate": price_gate,
        "decision_boundary": boundary,
        "review_reasons": review_reasons,
    }
