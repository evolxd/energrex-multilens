"""Energrex Mispricing & Special Situations Engine v2.3.

This module is intentionally separate from the existing five-dimension score.
It discovers and gates mispricing theses; it does not create a second final
investment score or duplicate the valuation engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
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

    def warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.payer_concentration.lower() == "high":
            warnings.append("High payer concentration can create pricing pressure.")
        if self.policy_dependency.lower() == "high":
            warnings.append("High policy dependency requires reimbursement stress tests.")
        if self.payment_necessity.lower() == "high" and self.cost_pass_through_ability.lower() in {"low", "medium-low"}:
            warnings.append("Demand may be rigid while margins remain vulnerable to payer price controls.")
        return warnings


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
    total_debt: float = 0.0
    lease_liabilities: float = 0.0
    pension_and_legal_claims: float = 0.0
    preferred_and_minority_claims: float = 0.0
    tax_and_transaction_cost: float = 0.0
    cash_burn_until_realization: float = 0.0
    market_cap: float = 0.0
    timeline_months: int = 0

    def common_equity_recovery(self) -> float:
        gross = (
            self.cash_and_equivalents
            + self.marketable_securities
            + self.receivables * self.receivables_recovery_rate
            + self.inventory * self.inventory_recovery_rate
            + self.property_and_equipment_sale_value
            + self.subsidiary_and_stake_value
            + self.hidden_asset_value
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


@dataclass(frozen=True)
class ConvictionProtocol:
    thesis_core_facts: Sequence[str]
    allowed_price_drawdown_pct: float
    reassessment_triggers: Sequence[str]
    mandatory_exit_triggers: Sequence[str]
    add_on_weakness_conditions: Sequence[str] = field(default_factory=tuple)
    prohibited_actions: Sequence[str] = field(default_factory=tuple)
    maximum_waiting_months: int | None = None

    def classify_event(self, *, price_change_pct: float | None = None, fact_trigger: str | None = None) -> str:
        if fact_trigger and fact_trigger in self.mandatory_exit_triggers:
            return "EXIT"
        if fact_trigger and fact_trigger in self.reassessment_triggers:
            return "REASSESS"
        if price_change_pct is not None and price_change_pct <= -abs(self.allowed_price_drawdown_pct):
            return "PRICE_OPPOSITION_REVIEW_FACTS"
        return "HOLD_DISCIPLINE"


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

    def overall_gate(self) -> GateStatus:
        statuses = {p.gate for p in self.pillars}
        if GateStatus.FAIL in statuses:
            return GateStatus.FAIL
        if GateStatus.NEEDS_EVIDENCE in statuses:
            return GateStatus.NEEDS_EVIDENCE
        return GateStatus.PASS

    def discovery_score(self) -> float | None:
        """Return one discovery score only after every hard gate passes.

        This score ranks research priority. It must not replace Energrex IDI.
        """
        for p in self.pillars:
            p.validate()
        if self.overall_gate() is GateStatus.FAIL:
            return None
        total_max = sum(p.max_score for p in self.pillars)
        if total_max <= 0:
            return None
        return round(100.0 * sum(p.score for p in self.pillars) / total_max, 2)

    def quick_reject_reasons(self) -> list[str]:
        reasons: list[str] = []
        for p in self.pillars:
            if p.gate is GateStatus.FAIL:
                reasons.append(f"{p.name} gate failed")
        if self.primary_path is OpportunityPath.LIQUIDATION_ARBITRAGE and self.liquidation:
            if self.liquidation.common_equity_recovery() <= 0:
                reasons.append("No positive conservative recovery for common equity")
            if self.liquidation.timeline_months <= 0:
                reasons.append("Liquidation timeline is not defined")
        return reasons


PILLAR_WEIGHTS: Mapping[str, float] = {
    "business_truth": 25.0,
    "survival": 20.0,
    "mispricing": 20.0,
    "value_capture": 20.0,
    "price_odds": 15.0,
}


def validate_pillar_contract(pillars: Iterable[PillarResult]) -> None:
    items = list(pillars)
    names = {p.name for p in items}
    missing = set(PILLAR_WEIGHTS) - names
    extra = names - set(PILLAR_WEIGHTS)
    if missing or extra:
        raise ValueError(f"Invalid pillar set; missing={sorted(missing)}, extra={sorted(extra)}")
    for p in items:
        p.validate()
        expected = PILLAR_WEIGHTS[p.name]
        if p.max_score != expected:
            raise ValueError(f"{p.name}: max_score must be {expected}")
