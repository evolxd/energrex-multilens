"""V2.4 special-situations calculations for the ENERGREX mispricing engine."""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any, Mapping

from scoring.mispricing_evidence import EvidenceRecord, evidence_quality


ASSET_FIELDS = (
    "cash_and_equivalents",
    "marketable_securities_recovery_value",
    "receivables_recovery_value",
    "inventory_recovery_value",
    "property_and_equipment_conservative_sale_value",
    "subsidiaries_and_stakes_value",
    "hidden_assets_value",
)

CLAIM_FIELDS = (
    "total_debt",
    "lease_liabilities",
    "pension_claims",
    "legal_and_regulatory_claims",
    "preferred_equity",
    "minority_interest",
    "tax_cost",
    "transaction_and_wind_down_cost",
    "cash_burn_until_realization",
)

LIQUIDATION_GATE_FIELDS = (
    "title_verified",
    "recovery_rates_supported",
    "hidden_liabilities_bounded",
    "cash_burn_bounded",
    "common_equity_priority_clear",
    "catalyst_or_control_path_present",
    "annualized_return_above_hurdle",
)


@dataclass(frozen=True)
class LiquidationResult:
    estimated_common_equity_recovery: float
    current_market_cap: float
    discount_to_market_cap: float
    liquidation_timeline_months: int
    estimated_annualized_return: float
    gate_status: str
    gate_reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...]


def _finite_nonnegative(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return number


def _valued_field(case: Mapping[str, Any], field: str) -> tuple[float, tuple[str, ...]]:
    """Read a `{value, evidence_ids}` field.

    Every liquidation input must cite the evidence it came from — a bare
    number is rejected, matching the evidence discipline the other five
    gates already require.
    """
    raw = case.get(field)
    if not isinstance(raw, Mapping):
        raise ValueError(f"{field} must be an object with value and evidence_ids")
    value = _finite_nonnegative(raw.get("value"), f"{field}.value")
    evidence_ids = tuple(
        str(item).strip() for item in raw.get("evidence_ids", ()) if str(item).strip()
    )
    if not evidence_ids:
        raise ValueError(f"{field}.evidence_ids must cite at least one evidence record")
    return value, evidence_ids


def _recovery_value(
    case: Mapping[str, Any],
    *,
    gross_field: str,
    rate_field: str,
    value_field: str,
) -> tuple[float, tuple[str, ...]]:
    if case.get(value_field) is not None:
        return _valued_field(case, value_field)
    gross, gross_evidence = _valued_field(case, gross_field)
    rate, rate_evidence = _valued_field(case, rate_field)
    if rate > 1:
        raise ValueError(f"{rate_field}.value must be between 0 and 1")
    return gross * rate, tuple(dict.fromkeys(gross_evidence + rate_evidence))


def calculate_liquidation_value(
    case: Mapping[str, Any],
    *,
    evidence_book: Mapping[str, EvidenceRecord],
    today: dt.date | None = None,
) -> LiquidationResult:
    """Calculate value available to common equity after all senior claims.

    Missing values are errors rather than optimistic zeroes. The caller must
    explicitly enter zero when a claim is known not to exist. Every asset and
    claim figure must cite evidence_ids; stale, missing, expired, or
    conflicted evidence reopens an otherwise-passing gate the same way it
    does for the other five gates (see mispricing_evidence.evidence_quality).
    """
    field_evidence: dict[str, tuple[str, ...]] = {}
    values: dict[str, float] = {}

    receivables_value, receivables_evidence = _recovery_value(
        case,
        gross_field="receivables_gross",
        rate_field="receivables_recovery_rate",
        value_field="receivables_recovery_value",
    )
    values["receivables_recovery_value"] = receivables_value
    field_evidence["receivables_recovery_value"] = receivables_evidence

    inventory_value, inventory_evidence = _recovery_value(
        case,
        gross_field="inventory_gross",
        rate_field="inventory_recovery_rate",
        value_field="inventory_recovery_value",
    )
    values["inventory_recovery_value"] = inventory_value
    field_evidence["inventory_recovery_value"] = inventory_evidence

    for field in ASSET_FIELDS:
        if field in values:
            continue
        value, evidence_ids = _valued_field(case, field)
        values[field] = value
        field_evidence[field] = evidence_ids

    for field in CLAIM_FIELDS:
        value, evidence_ids = _valued_field(case, field)
        values[field] = value
        field_evidence[field] = evidence_ids

    assets = sum(values[field] for field in ASSET_FIELDS)
    claims = sum(values[field] for field in CLAIM_FIELDS)
    recovery = assets - claims

    market_cap = _finite_nonnegative(case.get("current_market_cap"), "current_market_cap")
    if market_cap <= 0:
        raise ValueError("current_market_cap must be greater than zero")
    try:
        months = int(case.get("liquidation_timeline_months"))
    except (TypeError, ValueError) as exc:
        raise ValueError("liquidation_timeline_months must be an integer") from exc
    if months <= 0:
        raise ValueError("liquidation_timeline_months must be greater than zero")

    discount = (recovery - market_cap) / market_cap
    annualized = (
        (recovery / market_cap) ** (12 / months) - 1
        if recovery > 0
        else -1.0
    )

    all_evidence_ids = tuple(
        dict.fromkeys(
            evidence_id
            for ids in field_evidence.values()
            for evidence_id in ids
        )
    )
    quality = evidence_quality(all_evidence_ids, evidence_book, today=today)

    raw_gate = case.get("liquidation_gate")
    reasons: list[str] = []
    if not isinstance(raw_gate, Mapping):
        reasons.append("liquidation_gate is missing")
        status = "NEEDS_EVIDENCE"
    else:
        missing = [field for field in LIQUIDATION_GATE_FIELDS if raw_gate.get(field) is None]
        failed = [field for field in LIQUIDATION_GATE_FIELDS if raw_gate.get(field) is False]
        if failed:
            reasons.extend(f"{field} failed" for field in failed)
            status = "FAIL"
        elif missing:
            reasons.extend(f"{field} is unknown" for field in missing)
            status = "NEEDS_EVIDENCE"
        elif quality["issues"]:
            reasons.extend(quality["issues"])
            status = "NEEDS_EVIDENCE"
        else:
            status = "PASS"

    return LiquidationResult(
        estimated_common_equity_recovery=recovery,
        current_market_cap=market_cap,
        discount_to_market_cap=discount,
        liquidation_timeline_months=months,
        estimated_annualized_return=annualized,
        gate_status=status,
        gate_reasons=tuple(reasons),
        evidence_ids=all_evidence_ids,
    )


def as_liquidation_dict(result: LiquidationResult) -> dict[str, Any]:
    return {
        "estimated_common_equity_recovery": round(
            result.estimated_common_equity_recovery, 4
        ),
        "current_market_cap": round(result.current_market_cap, 4),
        "discount_to_market_cap": round(result.discount_to_market_cap, 6),
        "liquidation_timeline_months": result.liquidation_timeline_months,
        "estimated_annualized_return": round(
            result.estimated_annualized_return, 6
        ),
        "gate_status": result.gate_status,
        "gate_reasons": list(result.gate_reasons),
        "evidence_ids": list(result.evidence_ids),
    }
