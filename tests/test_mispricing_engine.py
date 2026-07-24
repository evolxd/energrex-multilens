import pytest

from scoring.mispricing_engine import (
    ConvictionAction,
    ConvictionProtocol,
    GateStatus,
    LiquidationCatalyst,
    LiquidationInputs,
    MetricRule,
    MispricingAssessment,
    OpportunityPath,
    PillarResult,
    PriceReviewRule,
    validate_pillar_contract,
)


def _pillars(survival_gate=GateStatus.PASS):
    return [
        PillarResult("business_truth", GateStatus.PASS, 20, 25, evidence=("business model verified",)),
        PillarResult("survival", survival_gate, 15, 20, evidence=("liquidity reviewed",)),
        PillarResult("mispricing", GateStatus.PASS, 16, 20, evidence=("consensus gap documented",)),
        PillarResult("value_capture", GateStatus.PASS, 15, 20, evidence=("common equity waterfall checked",)),
        PillarResult("price_odds", GateStatus.PASS, 10, 15, evidence=("valuation range tested",)),
    ]


def _assessment(pillars=None, path=OpportunityPath.IMPLIED_EXPECTATION, liquidation=None):
    return MispricingAssessment(
        ticker="TEST",
        primary_path=path,
        variant_view="Consensus embeds a permanent decline.",
        pillars=pillars or _pillars(),
        liquidation=liquidation,
    )


def test_hard_gate_failure_cannot_be_offset_by_score():
    assessment = _assessment(_pillars(GateStatus.FAIL), OpportunityPath.HIDDEN_ASSET)
    assert assessment.overall_gate() is GateStatus.FAIL
    assert assessment.discovery_score() is None
    assert "survival gate failed" in assessment.quick_reject_reasons()


def test_needs_evidence_cannot_publish_formal_score():
    assessment = _assessment(_pillars(GateStatus.NEEDS_EVIDENCE))
    assert assessment.overall_gate() is GateStatus.NEEDS_EVIDENCE
    assert assessment.discovery_score() is None


def test_single_discovery_score_after_all_gates_pass():
    assessment = _assessment()
    assert assessment.overall_gate() is GateStatus.PASS
    assert assessment.discovery_score() == 76.0


def test_pillar_contract_requires_exactly_five_unique_pillars():
    validate_pillar_contract(_pillars())
    with pytest.raises(ValueError, match="Exactly five"):
        validate_pillar_contract(_pillars()[:-1])
    duplicated = _pillars()[:-1] + [_pillars()[0]]
    with pytest.raises(ValueError, match="Duplicate"):
        validate_pillar_contract(duplicated)


def test_discovery_score_validates_contract_internally():
    assessment = _assessment(_pillars()[:-1])
    with pytest.raises(ValueError, match="Exactly five"):
        assessment.discovery_score()


def test_pass_gate_requires_evidence():
    pillars = _pillars()
    pillars[0] = PillarResult("business_truth", GateStatus.PASS, 20, 25)
    with pytest.raises(ValueError, match="PASS requires"):
        _assessment(pillars).overall_gate()


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
        catalyst=LiquidationCatalyst(
            catalyst_type="asset_sale",
            evidence="Board announced a signed sale process.",
            expected_milestone="binding bids",
            expected_timeline_months=12,
            control_or_board_support=True,
        ),
    )
    assert inputs.common_equity_recovery() == 50
    assert round(inputs.discount_to_recovery(), 2) == 0.20
    assert round(inputs.annualized_return_if_realized(), 3) == 0.118
    assert inputs.catalyst_is_actionable()


@pytest.mark.parametrize(
    "kwargs, expected_message",
    [
        ({"receivables_recovery_rate": 1.1}, "between 0 and 1"),
        ({"inventory_recovery_rate": -0.1}, "between 0 and 1"),
        ({"total_debt": -1}, "cannot be negative"),
        ({"timeline_months": -1}, "cannot be negative"),
    ],
)
def test_liquidation_inputs_reject_invalid_values(kwargs, expected_message):
    with pytest.raises(ValueError, match=expected_message):
        LiquidationInputs(**kwargs)


def test_hidden_asset_value_is_confidence_discounted():
    inputs = LiquidationInputs(hidden_asset_value=100, hidden_asset_confidence_discount=0.4)
    assert inputs.common_equity_recovery() == 40


def test_liquidation_path_requires_model_timeline_and_catalyst():
    no_model = _assessment(path=OpportunityPath.LIQUIDATION_ARBITRAGE)
    assert "Liquidation path requires a liquidation model" in no_model.quick_reject_reasons()

    no_catalyst = _assessment(
        path=OpportunityPath.LIQUIDATION_ARBITRAGE,
        liquidation=LiquidationInputs(cash_and_equivalents=100, market_cap=50, timeline_months=12),
    )
    assert "No actionable liquidation catalyst" in no_catalyst.quick_reject_reasons()


def test_conviction_protocol_uses_structured_metric_rules():
    protocol = ConvictionProtocol(
        thesis_core_facts=("payer mix stable",),
        price_review_rule=PriceReviewRule(reference="entry_price", threshold_pct=-30),
        metric_rules=(
            MetricRule("commercial_payer_mix", "<", 0.25, ConvictionAction.REASSESS),
            MetricRule("interest_coverage", "<", 2.0, ConvictionAction.EXIT),
        ),
        maximum_waiting_months=24,
    )
    assert protocol.classify_event(price_change_pct=-35) is ConvictionAction.PRICE_OPPOSITION_REVIEW_FACTS
    assert protocol.classify_event(observed_metrics={"commercial_payer_mix": 0.20}) is ConvictionAction.REASSESS
    assert protocol.classify_event(observed_metrics={"interest_coverage": 1.7}) is ConvictionAction.EXIT
    assert protocol.classify_event(elapsed_months=24) is ConvictionAction.REASSESS
    assert protocol.classify_event(price_change_pct=-10) is ConvictionAction.HOLD_DISCIPLINE


def test_exit_rule_has_priority_over_reassessment_rule():
    protocol = ConvictionProtocol(
        thesis_core_facts=("cash flow remains stable",),
        price_review_rule=PriceReviewRule(reference="entry_price", threshold_pct=-25),
        metric_rules=(
            MetricRule("revenue_growth", "<", 0, ConvictionAction.REASSESS),
            MetricRule("liquidity_months", "<", 6, ConvictionAction.EXIT),
        ),
    )
    result = protocol.classify_event(observed_metrics={"revenue_growth": -0.1, "liquidity_months": 3})
    assert result is ConvictionAction.EXIT


def test_price_rule_cannot_be_positive_or_act_as_exit():
    with pytest.raises(ValueError, match="negative percentage"):
        PriceReviewRule(reference="entry_price", threshold_pct=10)
    rule = PriceReviewRule(reference="entry_price", threshold_pct=-20)
    assert rule.triggered(-25)
