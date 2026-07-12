from pathlib import Path
import sys

import pytest

SCORING = Path(__file__).parents[1] / "scoring"
sys.path.insert(0, str(SCORING))

from ai_profile import (  # noqa: E402
    AI_CORE,
    AI_ENABLED,
    AI_UNVERIFIED,
    QUALITY_TRADITIONAL,
    classify_ai_profile,
    score_ai_role,
)
from formula import compute_base_score  # noqa: E402


def test_aapl_like_exposure_routes_to_ai_enabled_without_ai_penalty():
    profile = classify_ai_profile(
        {"ai_revenue_exposure_pct": 0.18, "ai_profit_exposure_pct": 0.20},
        "MEGA_TECH",
    )
    assert profile.key == AI_ENABLED
    assert profile.exposure == pytest.approx(0.19)
    assert profile.weights["ai_exposure"] == 0.20
    assert profile.weights["quality"] == 0.15
    assert profile.bonus == pytest.approx(2.25)


def test_core_ai_company_keeps_ai_as_a_weighted_dimension():
    profile = classify_ai_profile(
        {"ai_revenue_exposure_pct": 0.70, "ai_profit_exposure_pct": 0.80},
        "AI_CHIP",
    )
    assert profile.key == AI_CORE
    assert profile.weights["ai_exposure"] == 0.20
    assert profile.bonus == 0.0


@pytest.mark.parametrize("category", ["CYBERSECURITY", "SEMI_EQUIP", "MEGA_TECH"])
def test_ai_beneficiary_categories_use_enabled_profile_not_core(category):
    profile = classify_ai_profile(
        {"ai_revenue_exposure_pct": 0.60, "ai_profit_exposure_pct": 0.70},
        category,
    )
    assert profile.key == AI_ENABLED
    assert profile.weights["ai_exposure"] == 0.20
    assert profile.bonus == 5.0


def test_traditional_company_gets_only_a_small_positive_ai_accelerator():
    profile = classify_ai_profile(
        {"ai_revenue_exposure_pct": 0.04, "ai_profit_exposure_pct": 0.06},
        "MEGA_TECH",
    )
    assert profile.key == QUALITY_TRADITIONAL
    assert profile.weights["ai_exposure"] == 0.20
    assert profile.bonus == pytest.approx(1.50)


def test_missing_ai_evidence_neither_penalizes_nor_rewards():
    profile = classify_ai_profile({}, "MEGA_TECH")
    assert profile.key == AI_UNVERIFIED
    assert profile.weights["ai_exposure"] == 0.20
    assert profile.bonus == 0.0


def test_low_ai_score_uses_neutral_baseline_for_enabled_company():
    profile = classify_ai_profile({"ai_revenue_exposure_pct": 0.20}, "MEGA_TECH")
    common = {
        "valuation": 70,
        "growth": 55,
        "quality": 75,
        "expectation_gap": 50,
        "momentum": 60,
    }
    low_ai = compute_base_score(
        {**common, "ai_exposure": score_ai_role(0, profile.key)}, 4, False,
        dim_weights=profile.weights, positive_adjustment=profile.bonus,
    )
    high_ai = compute_base_score(
        {**common, "ai_exposure": score_ai_role(100, profile.key)}, 4, False,
        dim_weights=profile.weights, positive_adjustment=profile.bonus,
    )
    assert low_ai == high_ai


def test_ftnt_like_enabled_profile_does_not_receive_quality_weight_transfer():
    profile = classify_ai_profile(
        {"ai_revenue_exposure_pct": 0.235}, "CYBERSECURITY"
    )
    dims = {
        "valuation": 83.4,
        "growth": 68.97,
        "quality": 86.84,
        "ai_exposure": score_ai_role(37.65, profile.key),
        "expectation_gap": 43.56,
        "momentum": 99.0,
    }
    final = compute_base_score(
        dims, 5.23, False,
        dim_weights=profile.weights, positive_adjustment=profile.bonus,
    )
    assert final == pytest.approx(69.35, abs=0.02)
    assert final < 70


@pytest.mark.parametrize(
    "data,category",
    [
        ({"ai_revenue_exposure_pct": 0.80}, "AI_CHIP"),
        ({"ai_revenue_exposure_pct": 0.20}, "MEGA_TECH"),
        ({"ai_revenue_exposure_pct": 0.05}, "MEGA_TECH"),
        ({}, "MEGA_TECH"),
    ],
)
def test_every_profile_weight_set_sums_to_one(data, category):
    assert sum(classify_ai_profile(data, category).weights.values()) == pytest.approx(1.0)
