"""Strict JSON persistence for the Energrex mispricing engine.

The loader rejects incomplete or malformed research records. Missing evidence is
represented explicitly through GateStatus.NEEDS_EVIDENCE; it is never backfilled
with optimistic defaults.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scoring.mispricing_engine import (
    ConvictionAction,
    ConvictionProtocol,
    GateStatus,
    LiquidationCatalyst,
    LiquidationInputs,
    MetricRule,
    MispricingAssessment,
    OpportunityPath,
    PayerEconomics,
    PillarResult,
    PriceReviewRule,
)

SCHEMA_VERSION = "2.3"


def _required(mapping: Mapping[str, Any], key: str) -> Any:
    if key not in mapping:
        raise ValueError(f"missing required field: {key}")
    return mapping[key]


def _sequence(value: Any, field_name: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a JSON array")
    return tuple(value)


def assessment_from_dict(payload: Mapping[str, Any]) -> MispricingAssessment:
    version = str(_required(payload, "version"))
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported mispricing schema version: {version}")

    pillar_payload = _required(payload, "pillars")
    if not isinstance(pillar_payload, list):
        raise ValueError("pillars must be a JSON array")
    pillars = tuple(
        PillarResult(
            name=str(_required(item, "name")),
            gate=GateStatus(str(_required(item, "gate"))),
            score=float(_required(item, "score")),
            max_score=float(_required(item, "max_score")),
            evidence=_sequence(item.get("evidence", []), "evidence"),
            warnings=_sequence(item.get("warnings", []), "warnings"),
        )
        for item in pillar_payload
    )

    payer = None
    payer_payload = payload.get("payer_economics")
    if payer_payload is not None:
        payer = PayerEconomics(
            end_user=str(_required(payer_payload, "end_user")),
            economic_payers=_sequence(_required(payer_payload, "economic_payers"), "economic_payers"),
            price_setter=str(_required(payer_payload, "price_setter")),
            payment_necessity=str(_required(payer_payload, "payment_necessity")),
            payer_credit_quality=str(_required(payer_payload, "payer_credit_quality")),
            payer_concentration=str(_required(payer_payload, "payer_concentration")),
            policy_dependency=str(_required(payer_payload, "policy_dependency")),
            cost_pass_through_ability=str(_required(payer_payload, "cost_pass_through_ability")),
            collection_cycle_days=payer_payload.get("collection_cycle_days"),
            bad_debt_risk=str(payer_payload.get("bad_debt_risk", "unknown")),
            payer_mix_trend=str(payer_payload.get("payer_mix_trend", "stable")),
            leverage_support_assessment=str(payer_payload.get("leverage_support_assessment", "")),
            revenue_predictability=str(payer_payload.get("revenue_predictability", "unknown")),
            pricing_pressure_risk=str(payer_payload.get("pricing_pressure_risk", "unknown")),
            policy_shock_risk=str(payer_payload.get("policy_shock_risk", "unknown")),
            reasonable_net_leverage_ceiling=payer_payload.get("reasonable_net_leverage_ceiling"),
            stress_case_reimbursement_change_pct=payer_payload.get("stress_case_reimbursement_change_pct"),
        )

    liquidation = None
    liquidation_payload = payload.get("liquidation")
    if liquidation_payload is not None:
        catalyst = None
        catalyst_payload = liquidation_payload.get("catalyst")
        if catalyst_payload is not None:
            catalyst = LiquidationCatalyst(
                catalyst_type=str(_required(catalyst_payload, "catalyst_type")),
                evidence=str(_required(catalyst_payload, "evidence")),
                expected_milestone=str(_required(catalyst_payload, "expected_milestone")),
                expected_timeline_months=int(_required(catalyst_payload, "expected_timeline_months")),
                control_or_board_support=bool(catalyst_payload.get("control_or_board_support", False)),
            )
        liquidation = LiquidationInputs(
            **{key: value for key, value in liquidation_payload.items() if key != "catalyst"},
            catalyst=catalyst,
        )

    conviction = None
    conviction_payload = payload.get("conviction")
    if conviction_payload is not None:
        price_payload = _required(conviction_payload, "price_review_rule")
        metric_payload = _required(conviction_payload, "metric_rules")
        conviction = ConvictionProtocol(
            thesis_core_facts=_sequence(_required(conviction_payload, "thesis_core_facts"), "thesis_core_facts"),
            price_review_rule=PriceReviewRule(
                reference=str(_required(price_payload, "reference")),
                threshold_pct=float(_required(price_payload, "threshold_pct")),
                lookback_days=price_payload.get("lookback_days"),
            ),
            metric_rules=tuple(
                MetricRule(
                    metric=str(_required(item, "metric")),
                    operator=str(_required(item, "operator")),
                    threshold=float(_required(item, "threshold")),
                    action=ConvictionAction(str(_required(item, "action"))),
                    rationale=str(item.get("rationale", "")),
                )
                for item in metric_payload
            ),
            add_on_weakness_conditions=_sequence(conviction_payload.get("add_on_weakness_conditions", []), "add_on_weakness_conditions"),
            prohibited_actions=_sequence(conviction_payload.get("prohibited_actions", []), "prohibited_actions"),
            maximum_waiting_months=conviction_payload.get("maximum_waiting_months"),
        )

    assessment = MispricingAssessment(
        ticker=str(_required(payload, "ticker")),
        primary_path=OpportunityPath(str(_required(payload, "primary_path"))),
        secondary_paths=tuple(OpportunityPath(str(item)) for item in _sequence(payload.get("secondary_paths", []), "secondary_paths")),
        variant_view=str(_required(payload, "variant_view")),
        pillars=pillars,
        payer_economics=payer,
        liquidation=liquidation,
        conviction=conviction,
    )
    # Force full contract validation during load, not later in the UI.
    assessment.overall_gate()
    return assessment


def load_assessment(path: str | Path) -> MispricingAssessment:
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("assessment root must be a JSON object")
    return assessment_from_dict(payload)
