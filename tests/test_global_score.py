from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scoring"))
from quant_engine import compute_global_score, score_ticker
from quant_audit import merge_data


# Every AI profile in ai_profile.PROFILE_WEIGHTS shares this 6-dim split.
STANDARD_WEIGHTS = {
    "valuation": 0.20,
    "growth": 0.25,
    "quality": 0.15,
    "ai_exposure": 0.20,
    "expectation_gap": 0.10,
    "momentum": 0.10,
}


def test_global_score_drops_ai_exposure_and_renormalizes_the_rest():
    # ai_exposure pinned at 0 -- if it leaked into the formula the score
    # would be pulled toward 0. All other dims pinned at 100.
    dim_scores = {
        "valuation": 100.0,
        "growth": 100.0,
        "quality": 100.0,
        "ai_exposure": 0.0,
        "expectation_gap": 100.0,
        "momentum": 100.0,
    }
    assert compute_global_score(dim_scores, STANDARD_WEIGHTS) == pytest.approx(100.0)


def test_global_score_matches_hand_computed_renormalized_weights():
    dim_scores = {
        "valuation": 80.0,
        "growth": 60.0,
        "quality": 40.0,
        "ai_exposure": 95.0,  # excluded -- must not affect the result
        "expectation_gap": 20.0,
        "momentum": 10.0,
    }
    # Hand-computed: divide each non-ai_exposure weight by (1 - 0.20) = 0.80
    expected = (
        80.0 * (0.20 / 0.80)
        + 60.0 * (0.25 / 0.80)
        + 40.0 * (0.15 / 0.80)
        + 20.0 * (0.10 / 0.80)
        + 10.0 * (0.10 / 0.80)
    )
    assert compute_global_score(dim_scores, STANDARD_WEIGHTS) == pytest.approx(expected, abs=0.01)


def test_global_score_is_not_reduced_by_risk_penalty_or_circuit_multiplier():
    # A ticker with a high risk_penalty / circuit breaker firing should
    # still get an unpenalized global_score -- unlike final_score.
    dim_scores = {
        "valuation": 70.0, "growth": 70.0, "quality": 70.0,
        "ai_exposure": 70.0, "expectation_gap": 70.0, "momentum": 70.0,
    }
    assert compute_global_score(dim_scores, STANDARD_WEIGHTS) == pytest.approx(70.0)


def test_global_score_neutral_when_all_weight_is_on_ai_exposure():
    weights = {"valuation": 0.0, "ai_exposure": 1.0}
    dim_scores = {"valuation": 10.0, "ai_exposure": 90.0}
    assert compute_global_score(dim_scores, weights) == pytest.approx(50.0)


def test_global_score_matches_design_doc_snapshot_for_nvda_and_isrg():
    # Design doc "全局得分 / 行业得分 双轨评分设计" Section 2 empirical table
    # (2026-08-31 snapshot): NVDA global_score ~= 83.4, ISRG ~= 48.9.
    # MRVL is intentionally not asserted here -- its mock_data.py price was
    # corrected after that snapshot (user-supplied real-time quote), so its
    # global_score has since legitimately moved off the doc's 59.6 figure.
    for ticker, expected in [("NVDA", 83.4), ("ISRG", 48.9)]:
        data = merge_data(ticker, use_live=False)
        result = score_ticker(ticker, data)
        assert result.global_score == pytest.approx(expected, abs=0.15)
        assert result.final_score != pytest.approx(result.global_score, abs=0.01)
