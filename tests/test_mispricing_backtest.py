from datetime import date

import pytest

from scoring.mispricing_backtest import BlindCase, BlindCaseSet, CaseOutcome


def _case(index: int, path: str, *, resolved: bool = False) -> BlindCase:
    return BlindCase(
        case_id=f"CASE-{index:02d}",
        ticker=f"T{index:02d}",
        evidence_cutoff=date(2020, 1, 1),
        reveal_date=date(2021, 1, 1),
        primary_path=path,
        source_snapshot_ids=(f"snapshot-{index}",),
        assessment_file=f"data/mispricing/backtests/case-{index:02d}.json",
        expected_gate_before_reveal="PASS",
        outcome=CaseOutcome.FAILURE if resolved else CaseOutcome.UNRESOLVED,
        outcome_notes="Thesis failed after reveal." if resolved else "",
    )


def test_reveal_date_must_follow_evidence_cutoff():
    with pytest.raises(ValueError, match="after evidence_cutoff"):
        BlindCase(
            case_id="BAD",
            ticker="BAD",
            evidence_cutoff=date(2021, 1, 1),
            reveal_date=date(2021, 1, 1),
            primary_path="hidden_asset_or_scarce_right",
            source_snapshot_ids=("snapshot",),
            assessment_file="case.json",
            expected_gate_before_reveal="PASS",
        )


def test_resolved_case_requires_outcome_notes():
    with pytest.raises(ValueError, match="outcome_notes"):
        BlindCase(
            case_id="BAD-2",
            ticker="BAD",
            evidence_cutoff=date(2020, 1, 1),
            reveal_date=date(2021, 1, 1),
            primary_path="hidden_asset_or_scarce_right",
            source_snapshot_ids=("snapshot",),
            assessment_file="case.json",
            expected_gate_before_reveal="PASS",
            outcome=CaseOutcome.FAILURE,
        )


def test_blind_case_set_requires_scale_path_coverage_and_unrevealed_cases():
    paths = [
        "great_business_temporary_trouble",
        "cashflow_buyback_compounder",
        "hidden_asset_or_scarce_right",
        "liquidation_value_arbitrage",
    ]
    cases = [_case(i, paths[i % len(paths)], resolved=i < 4) for i in range(12)]
    case_set = BlindCaseSet(cases)
    case_set.validate()
    assert len(case_set.unresolved_cases()) == 8
    assert len(case_set.resolved_cases()) == 4


def test_tiny_case_set_is_rejected():
    with pytest.raises(ValueError, match="at least 12"):
        BlindCaseSet([_case(1, "hidden_asset_or_scarce_right")]).validate()


def test_case_set_cannot_be_mostly_revealed():
    paths = [
        "great_business_temporary_trouble",
        "cashflow_buyback_compounder",
        "hidden_asset_or_scarce_right",
        "liquidation_value_arbitrage",
    ]
    cases = [_case(i, paths[i % len(paths)], resolved=i < 7) for i in range(12)]
    with pytest.raises(ValueError, match="half"):
        BlindCaseSet(cases).validate()
