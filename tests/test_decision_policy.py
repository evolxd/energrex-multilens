from scoring.decision_policy import data_gate_status, evaluate_decision, score_band


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
