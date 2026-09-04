from scoring.decision_policy import (
    apply_mispricing_gate,
    apply_v23_case_gate,
    data_gate_status,
    evaluate_decision,
    score_band,
)


def test_score_bands_are_non_actionable_quality_labels():
    assert score_band(80) == "⭐ 综合强劲"
    assert score_band(65) == "✅ 综合良好"
    assert score_band(50) == "👀 综合中性"
    assert score_band(35) == "⚠️ 谨慎评估"
    assert score_band(34.9) == "🚫 风险较高"


def test_95_percent_data_gate_blocks_actionable_conclusion():
    decision = evaluate_decision(90, 90, 0.90, validation_status="PASS")
    assert decision.label == "🧾 数据待复核"
    assert decision.actionable is False
    assert decision.status == "PARTIAL"


def test_high_price_zone_vetoes_strong_composite_score():
    decision = evaluate_decision(90, 59.9, 0.99, validation_status="PASS")
    assert decision.label == "⚠️ 高价观察"
    assert decision.actionable is False


def test_failed_data_gate_still_discloses_high_price_zone():
    decision = evaluate_decision(58.6, 42.4, 0.90, validation_status="PASS")
    assert decision.label == "🧾 数据待复核（高价区）"
    assert decision.actionable is False


def test_candidate_requires_score_valuation_and_valid_data():
    decision = evaluate_decision(70, 65, 0.96, validation_status="PASS")
    assert decision.label == "✅ 候选"
    assert decision.actionable is True


def test_high_multiple_triple_signal_blocks_actionable_candidate():
    decision = evaluate_decision(
        70, 80, 0.99, validation_status="PASS",
        forward_pe=45.0, ev_sales=15.0, fcf_yield=0.016,
    )
    assert decision.label == "⚠️ 高估值待验证"
    assert decision.status == "VALUATION_REVIEW"
    assert decision.actionable is False


def test_human_review_is_a_hard_veto():
    assert data_gate_status(1.0, human_review_required=True) == "REVIEW_REQUIRED"


def test_missing_mispricing_case_does_not_change_existing_decision():
    base = evaluate_decision(82, 78, 0.99, validation_status="PASS")
    assert apply_mispricing_gate(base, None) == base


def test_v23_case_requires_formal_valuation_not_only_valuation_score():
    base = evaluate_decision(82, 78, 0.99, validation_status="PASS")
    gated = apply_v23_case_gate(
        base,
        "DEEP_RESEARCH_P0",
        formal_valuation_status=None,
        formal_price_gate=None,
    )
    assert gated.actionable is False
    assert gated.status == "FORMAL_VALUATION_REVIEW"


def test_v23_formal_price_wait_cannot_be_overridden_by_high_score():
    base = evaluate_decision(95, 95, 0.99, validation_status="PASS")
    gated = apply_v23_case_gate(
        base,
        "DEEP_RESEARCH_P0",
        formal_valuation_status="PASS",
        formal_price_gate="WAIT",
    )
    assert gated.actionable is False
    assert gated.status == "FORMAL_PRICE_WAIT"


def test_v23_pass_preserves_the_single_base_decision_and_score_band():
    base = evaluate_decision(82, 78, 0.99, validation_status="PASS")
    gated = apply_v23_case_gate(
        base,
        "DEEP_RESEARCH_P0",
        formal_valuation_status="PASS",
        formal_price_gate="PASS",
    )
    assert gated == base


def test_wait_for_price_closes_actionability_without_changing_score_band():
    base = evaluate_decision(82, 78, 0.99, validation_status="PASS")
    gated = apply_mispricing_gate(base, "WAIT_FOR_PRICE", blocking_reasons=["赔率不足"])
    assert gated.actionable is False
    assert gated.status == "WAIT_FOR_PRICE"
    assert gated.score_band == base.score_band
