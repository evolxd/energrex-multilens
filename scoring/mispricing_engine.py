"""Energrex Mispricing & Special Situations Engine v2.3.

This module is intentionally separate from the existing five-dimension score.
It discovers and gates mispricing theses; it does not create a second final
investment score or duplicate the valuation engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
from operator import eq, ge, gt, le, lt, ne
from typing import Iterable, Mapping, Sequence


class GateStatus(str, Enum):
    PASS = "PASS"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
    FAIL = "FAIL"


class OpportunityPath(str, Enum):
    TEMPORARY_TROUBLE = "great_business_temporary_trouble"
    BUYBACK_COMPOUNDER = "cashflow_buyback_compounder"
    HIDDEN_ASSET = "hidden_asset_or_scarce_right"
    CAPITAL_ALLOCATION = "capital_allocation_rerating"
    IMPLIED_EXPECTATION = "implied_expectation_mismatch"
    LIQUIDATION_ARBITRAGE = "liquidation_value_arbitrage"


class ConvictionAction(str, Enum):
    HOLD_DISCIPLINE = "HOLD_DISCIPLINE"
    PRICE_OPPOSITION_REVIEW_FACTS = "PRICE_OPPOSITION_REVIEW_FACTS"
    REASSESS = "REASSESS"
    EXIT = "EXIT"


_OPERATORS = {"<": lt, "<=": le, ">": gt, ">=": ge, "==": eq, "!=": ne}


@dataclass(frozen=True)
class MetricRule:
    """Executable thesis rule, rather than an exact free-text string match."""

    metric: str
    operator: str
    threshold: float
    action: ConvictionAction
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.metric.strip():
            raise ValueError("metric must be non-empty")
        if self.operator not in _OPERATORS:
            raise ValueError(f"unsupported operator: {self.operator}")
        if self.action not in {ConvictionAction.REASSESS, ConvictionAction.EXIT}:
            raise ValueError("metric rules may only trigger REASSESS or EXIT")

    def matches(self, observed_value: float) -> bool:
        return bool(_OPERATORS[self.operator](observed_value, self.threshold))


@dataclass(frozen=True)
class PriceReviewRule:
    """Price can trigger fact review, never an automatic exit."""

    reference: str
    threshold_pct: float
    lookback_days: int | None = None

    def __post_init__(self) -> None:
        if not self.reference.strip():
            raise ValueError("price review reference must be non-empty")
        if self.threshold_pct >= 0:
            raise ValueError("price review threshold must be a negative percentage")
        if self.lookback_days is not None and self.lookback_days <= 0:
            raise ValueError("lookback_days must be positive when provided")

    def triggered(self, price_change_pct: float | None) -> bool:
        return price_change_pct is not None and price_change_pct <= self.threshold_pct


@dataclass(frozen=True)
class PayerEconomics:
    end_user: str
    economic_payers: Sequence[str]
    price_setter: str
    payment_necessity: str
    payer_credit_quality: str
    payer_concentration: str
    policy_dependency: str
    cost_pass_through_ability: str
    collection_cycle_days: float | None = None
    bad_debt_risk: str = "unknown"
    payer_mix_trend: str = "stable"
    leverage_support_assessment: str = ""
    revenue_predictability: str = "unknown"
    pricing_pressure_risk: str = "unknown"
    policy_shock_risk: str = "unknown"
    reasonable_net_leverage_ceiling: float | None = None
    stress_case_reimbursement_change_pct: float | None = None

    def __post_init__(self) -> None:
        if not self.end_user.strip() or not self.economic_payers:
            raise ValueError("end user and at least one economic payer are required")
        if self.collection_cycle_days is not None and self.collection_cycle_days < 0:
            raise ValueError("collection_cycle_days cannot be negative")
        if self.reasonable_net_leverage_ceiling is not None and self.reasonable_net_leverage_ceiling < 0:
            raise ValueError("reasonable_net_leverage_ceiling cannot be negative")

    def warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.payer_concentration.lower() == "high":
            warnings.append("High payer concentration can create pricing pressure.")
        if self.policy_dependency.lower() == "high":
            warnings.append("High policy dependency requires reimbursement stress tests.")
        if self.payment_necessity.lower() == "high" and self.cost_pass_through_ability.lower() in {"low", "medium-low"}:
            warnings.append("Demand may be rigid while margins remain vulnerable to payer price controls.")
        if self.reasonable_net_leverage_ceiling is None:
            warnings.append("Reasonable leverage ceiling has not been quantified.")
        return warnings


@dataclass(frozen=True)
class LiquidationCatalyst:
    catalyst_type: str
    evidence: str
    expected_milestone: str
    expected_timeline_months: int
    control_or_board_support: bool = False

    def __post_init__(self) -> None:
        if not self.catalyst_type.strip() or not self.evidence.strip() or not self.expected_milestone.strip():
            raise ValueError("liquidation catalyst requires type, evidence, and milestone")
        if self.expected_timeline_months <= 0:
            raise ValueError("catalyst timeline must be positive")


@dataclass(frozen=True)
class LiquidationInputs:
    cash_and_equivalents: float = 0.0
    marketable_securities: float = 0.0
    receivables: float = 0.0
    receivables_recovery_rate: float = 0.0
    inventory: float = 0.0
    inventory_recovery_rate: float = 0.0
    property_and_equipment_sale_value: float = 0.0
    subsidiary_and_stake_value: float = 0.0
    hidden_asset_value: float = 0.0
    hidden_asset_confidence_discount: float = 1.0
    total_debt: float = 0.0
    lease_liabilities: float = 0.0
    pension_and_legal_claims: float = 0.0
    preferred_and_minority_claims: float = 0.0
    tax_and_transaction_cost: float = 0.0
    cash_burn_until_realization: float = 0.0
    market_cap: float = 0.0
    timeline_months: int = 0
    catalyst: LiquidationCatalyst | None = None

    def __post_init__(self) -> None:
        rate_fields = (
            "receivables_recovery_rate",
            "inventory_recovery_rate",
            "hidden_asset_confidence_discount",
        )
        for name in rate_fields:
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        for item in fields(self):
            if item.name in rate_fields or item.name in {"timeline_months", "catalyst"}:
                continue
            value = getattr(self, item.name)
            if isinstance(value, (int, float)) and value < 0:
                raise ValueError(f"{item.name} cannot be negative")
        if self.timeline_months < 0:
            raise ValueError("timeline_months cannot be negative")

    def common_equity_recovery(self) -> float:
        gross = (
            self.cash_and_equivalents
            + self.marketable_securities
            + self.receivables * self.receivables_recovery_rate
            + self.inventory * self.inventory_recovery_rate
            + self.property_and_equipment_sale_value
            + self.subsidiary_and_stake_value
            + self.hidden_asset_value * self.hidden_asset_confidence_discount
        )
        claims = (
            self.total_debt
            + self.lease_liabilities
            + self.pension_and_legal_claims
            + self.preferred_and_minority_claims
            + self.tax_and_transaction_cost
            + self.cash_burn_until_realization
        )
        return gross - claims

    def discount_to_recovery(self) -> float | None:
        recovery = self.common_equity_recovery()
        if recovery <= 0 or self.market_cap <= 0:
            return None
        return 1.0 - self.market_cap / recovery

    def annualized_return_if_realized(self) -> float | None:
        recovery = self.common_equity_recovery()
        if recovery <= 0 or self.market_cap <= 0 or self.timeline_months <= 0:
            return None
        years = self.timeline_months / 12.0
        return (recovery / self.market_cap) ** (1.0 / years) - 1.0

    def catalyst_is_actionable(self) -> bool:
        if self.catalyst is None:
            return False
        return self.catalyst.control_or_board_support or bool(self.catalyst.evidence.strip())


@dataclass(frozen=True)
class ConvictionProtocol:
    thesis_core_facts: Sequence[str]
    price_review_rule: PriceReviewRule
    metric_rules: Sequence[MetricRule]
    add_on_weakness_conditions: Sequence[str] = field(default_factory=tuple)
    prohibited_actions: Sequence[str] = field(default_factory=tuple)
    maximum_waiting_months: int | None = None

    def __post_init__(self) -> None:
        if not self.thesis_core_facts:
            raise ValueError("at least one thesis core fact is required")
        if self.maximum_waiting_months is not None and self.maximum_waiting_months <= 0:
            raise ValueError("maximum_waiting_months must be positive")

    def classify_event(
        self,
        *,
        price_change_pct: float | None = None,
        observed_metrics: Mapping[str, float] | None = None,
        elapsed_months: int | None = None,
    ) -> ConvictionAction:
        observed_metrics = observed_metrics or {}
        matched_actions: list[ConvictionAction] = []
        for rule in self.metric_rules:
            if rule.metric in observed_metrics and rule.matches(observed_metrics[rule.metric]):
                matched_actions.append(rule.action)
        if ConvictionAction.EXIT in matched_actions:
            return ConvictionAction.EXIT
        if ConvictionAction.REASSESS in matched_actions:
            return ConvictionAction.REASSESS
        if self.maximum_waiting_months is not None and elapsed_months is not None:
            if elapsed_months >= self.maximum_waiting_months:
                return ConvictionAction.REASSESS
        if self.price_review_rule.triggered(price_change_pct):
            return ConvictionAction.PRICE_OPPOSITION_REVIEW_FACTS
        return ConvictionAction.HOLD_DISCIPLINE


@dataclass(frozen=True)
class PillarResult:
    name: str
    gate: GateStatus
    score: float
    max_score: float
    evidence: Sequence[str] = field(default_factory=tuple)
    warnings: Sequence[str] = field(default_factory=tuple)

    def validate(self) -> None:
        if not 0 <= self.score <= self.max_score:
            raise ValueError(f"{self.name}: score must be between 0 and {self.max_score}")
        if self.gate is GateStatus.PASS and not self.evidence:
            raise ValueError(f"{self.name}: PASS requires at least one evidence item")


PILLAR_WEIGHTS: Mapping[str, float] = {
    "business_truth": 25.0,
    "survival": 20.0,
    "mispricing": 20.0,
    "value_capture": 20.0,
    "price_odds": 15.0,
}


def validate_pillar_contract(pillars: Iterable[PillarResult]) -> None:
    items = list(pillars)
    if len(items) != len(PILLAR_WEIGHTS):
        raise ValueError("Exactly five unique pillars are required")
    names = [p.name for p in items]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate pillars are not allowed")
    missing = set(PILLAR_WEIGHTS) - set(names)
    extra = set(names) - set(PILLAR_WEIGHTS)
    if missing or extra:
        raise ValueError(f"Invalid pillar set; missing={sorted(missing)}, extra={sorted(extra)}")
    for pillar in items:
        pillar.validate()
        expected = PILLAR_WEIGHTS[pillar.name]
        if pillar.max_score != expected:
            raise ValueError(f"{pillar.name}: max_score must be {expected}")


@dataclass(frozen=True)
class MispricingAssessment:
    ticker: str
    primary_path: OpportunityPath
    variant_view: str
    pillars: Sequence[PillarResult]
    payer_economics: PayerEconomics | None = None
    liquidation: LiquidationInputs | None = None
    conviction: ConvictionProtocol | None = None
    secondary_paths: Sequence[OpportunityPath] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.ticker.strip() or not self.variant_view.strip():
            raise ValueError("ticker and variant_view are required")
        if self.primary_path in self.secondary_paths:
            raise ValueError("primary path cannot be repeated as a secondary path")
        if len(set(self.secondary_paths)) != len(self.secondary_paths):
            raise ValueError("secondary paths must be unique")

    def overall_gate(self) -> GateStatus:
        validate_pillar_contract(self.pillars)
        statuses = {p.gate for p in self.pillars}
        if GateStatus.FAIL in statuses:
            return GateStatus.FAIL
        if GateStatus.NEEDS_EVIDENCE in statuses:
            return GateStatus.NEEDS_EVIDENCE
        return GateStatus.PASS

    def discovery_score(self) -> float | None:
        """Return one research-priority score only after every gate passes."""
        validate_pillar_contract(self.pillars)
        if self.overall_gate() is not GateStatus.PASS:
            return None
        return round(100.0 * sum(p.score for p in self.pillars) / sum(PILLAR_WEIGHTS.values()), 2)

    def quick_reject_reasons(self) -> list[str]:
        validate_pillar_contract(self.pillars)
        reasons = [f"{p.name} gate failed" for p in self.pillars if p.gate is GateStatus.FAIL]
        if self.primary_path is OpportunityPath.LIQUIDATION_ARBITRAGE:
            if self.liquidation is None:
                reasons.append("Liquidation path requires a liquidation model")
            else:
                if self.liquidation.common_equity_recovery() <= 0:
                    reasons.append("No positive conservative recovery for common equity")
                if self.liquidation.timeline_months <= 0:
                    reasons.append("Liquidation timeline is not defined")
                if not self.liquidation.catalyst_is_actionable():
                    reasons.append("No actionable liquidation catalyst")
        return reasons
