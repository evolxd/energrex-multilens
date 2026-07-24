from scoring.mispricing_engine import (
    ConvictionProtocol,
    GateStatus,
    LiquidationInputs,
    MispricingAssessment,
    OpportunityPath,
    PillarResult,
    validate_pillar_contract,
)


def _pillars(survival_gate=GateStatus.PASS):
    return [
        PillarResult("business_truth", GateStatus.PASS, 20, 25),
        PillarResult("survival", survival_gate, 15, 20),
        PillarResult("mispricing", GateStatus.PASS, 16, 20),
        PillarResult("value_capture", GateStatus.PASS, 15, 20),
        PillarResult("price_odds", GateStatus.PASS, 10, 15),
    ]


def test_hard_gate_failure_cannot_be_offset_by_score():
    assessment = MispricingAssessment(
        ticker="TEST",
        primary_path=OpportunityPath.HIDDEN_ASSET,
        variant_view="Market discounts an asset the common equity can capture.",
        pillars=_pillars(GateStatus.FAIL),
    )
    assert assessment.overall_gate() is GateStatus.FAIL
    assert assessment.discovery_score() is None
    assert "survival gate failed" in assessment.quick_reject_reasons()


def test_single_discovery_score_after_gates_pass():
    assessment = MispricingAssessment(
        ticker="TEST",
        primary_path=OpportunityPath.IMPLIED_EXPECTATION,
        variant_view="Consensus embeds a permanent decline.",
        pillars=_pillars(),
    )
    assert assessment.overall_gate() is GateStatus.PASS
    assert assessment.discovery_score() == 76.0


def test_liquidation_recovery_reaches_common_equity_only_after_claims():
    inputs = LiquidationInputs(
        cash_and_equivalents=50,
        marketable_securities=20,
        receivables=40,
        receivables_recovery_rate=0.75,
        inventory=20,
        inventory_recovery_rate=0.5,
        property_and_equipment_sale_value=30,
        total_debt=60,
        lease_liabilities=10,
        pension_and_legal_claims=5,
        tax_and_transaction_cost=5,
        cash_burn_until_realization=10,
        market_cap=40,
        timeline_months=24,
    )
    assert inputs.common_equity_recovery() == 50
    assert round(inputs.discount_to_recovery(), 2) == 0.20
    assert round(inputs.annualized_return_if_realized(), 3) == 0.118


def test_conviction_protocol_separates_price_from_fact_opposition():
    protocol = ConvictionProtocol(
        thesis_core_facts=("payer mix stable",),
        allowed_price_drawdown_pct=30,
        reassessment_triggers=("payer mix worsens",),
        mandatory_exit_triggers=("liquidity covenant breach",),
    )
    assert protocol.classify_event(price_change_pct=-35) == "PRICE_OPPOSITION_REVIEW_FACTS"
    assert protocol.classify_event(fact_trigger="payer mix worsens") == "REASSESS"
    assert protocol.classify_event(fact_trigger="liquidity covenant breach") == "EXIT"


def test_pillar_contract_requires_exact_five_pillars():
    validate_pillar_contract(_pillars())
