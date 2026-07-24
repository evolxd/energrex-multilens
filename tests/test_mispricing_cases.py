from pathlib import Path

import pytest

from scoring.mispricing_engine import (
    GateStatus,
    LiquidationInputs,
    MispricingAssessment,
    OpportunityPath,
    PillarResult,
)
from scoring.mispricing_store import load_assessment


def _pillars(*, survival=GateStatus.PASS, value_capture=GateStatus.PASS):
    return (
        PillarResult("business_truth", GateStatus.PASS, 20, 25, evidence=("verified",)),
        PillarResult("survival", survival, 15, 20, evidence=("verified",) if survival is GateStatus.PASS else ()),
        PillarResult("mispricing", GateStatus.PASS, 15, 20, evidence=("verified",)),
        PillarResult("value_capture", value_capture, 15, 20, evidence=("verified",) if value_capture is GateStatus.PASS else ()),
        PillarResult("price_odds", GateStatus.PASS, 10, 15, evidence=("verified",)),
    )


@pytest.mark.parametrize("ticker", ["DVA", "FUTU", "VSAT"])
def test_research_templates_load_as_needs_evidence(ticker):
    assessment = load_assessment(Path(f"data/mispricing/{ticker}.template.json"))
    assert assessment.ticker == ticker
    assert assessment.overall_gate() is GateStatus.NEEDS_EVIDENCE
    assert assessment.discovery_score() is None


def test_false_positive_high_buyback_but_survival_fails():
    assessment = MispricingAssessment(
        ticker="BUYBACK_TRAP",
        primary_path=OpportunityPath.BUYBACK_COMPOUNDER,
        variant_view="Headline buybacks conceal a refinancing problem.",
        pillars=_pillars(survival=GateStatus.FAIL),
    )
    assert assessment.discovery_score() is None
    assert "survival gate failed" in assessment.quick_reject_reasons()


def test_false_positive_hidden_asset_but_common_equity_cannot_capture():
    assessment = MispricingAssessment(
        ticker="ASSET_TRAP",
        primary_path=OpportunityPath.HIDDEN_ASSET,
        variant_view="The asset exists but senior claims absorb its value.",
        pillars=_pillars(value_capture=GateStatus.FAIL),
    )
    assert assessment.discovery_score() is None
    assert "value_capture gate failed" in assessment.quick_reject_reasons()


def test_false_positive_liquidation_value_without_catalyst():
    assessment = MispricingAssessment(
        ticker="NO_CATALYST",
        primary_path=OpportunityPath.LIQUIDATION_ARBITRAGE,
        variant_view="Asset value appears high but management has no release mechanism.",
        pillars=_pillars(),
        liquidation=LiquidationInputs(
            cash_and_equivalents=100,
            total_debt=20,
            market_cap=40,
            timeline_months=12,
        ),
    )
    assert "No actionable liquidation catalyst" in assessment.quick_reject_reasons()


def test_false_positive_liquidation_value_consumed_by_cash_burn():
    assessment = MispricingAssessment(
        ticker="BURN_TRAP",
        primary_path=OpportunityPath.LIQUIDATION_ARBITRAGE,
        variant_view="Gross assets are consumed before realization.",
        pillars=_pillars(),
        liquidation=LiquidationInputs(
            cash_and_equivalents=100,
            total_debt=20,
            cash_burn_until_realization=90,
            market_cap=40,
            timeline_months=12,
        ),
    )
    assert "No positive conservative recovery for common equity" in assessment.quick_reject_reasons()


def test_false_positive_unverified_story_cannot_publish_score():
    pillars = list(_pillars())
    pillars[2] = PillarResult(
        "mispricing",
        GateStatus.NEEDS_EVIDENCE,
        19,
        20,
        warnings=("Variant view remains a story without verified evidence.",),
    )
    assessment = MispricingAssessment(
        ticker="STORY_TRAP",
        primary_path=OpportunityPath.IMPLIED_EXPECTATION,
        variant_view="A compelling story is not verified variant perception.",
        pillars=tuple(pillars),
    )
    assert assessment.overall_gate() is GateStatus.NEEDS_EVIDENCE
    assert assessment.discovery_score() is None
