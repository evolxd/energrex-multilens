import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scoring"))
from quant_engine import score_risk_penalty


SAFE = {
    "beta": 0.80,
    "volatility_30d": 0.15,
    "valuation_risk": 0.10,
    "liquidity_risk": 0.02,
    "max_drawdown_1y": 0.10,
}

WORST = {
    "beta": 2.50,
    "volatility_30d": 0.70,
    "valuation_risk": 0.90,
    "liquidity_risk": 0.30,
    "max_drawdown_1y": 0.60,
}


def penalty(data):
    dim, _, _, _ = score_risk_penalty(data, de_ratio=0.30)
    return dim.dim_score


def test_safe_inputs_have_zero_penalty_and_worst_inputs_reach_cap():
    assert penalty(SAFE) == pytest.approx(0.0)
    assert penalty(WORST) == pytest.approx(20.0)


@pytest.mark.parametrize("field", list(SAFE))
def test_each_worsening_risk_input_can_only_increase_penalty(field):
    baseline = penalty(SAFE)
    changed = dict(SAFE)
    changed[field] = WORST[field]
    assert penalty(changed) > baseline


def test_aapl_like_low_market_risk_is_not_labeled_as_high_penalty():
    aapl = {
        "beta": 0.85,
        "volatility_30d": 0.20,
        "valuation_risk": 0.55,
        "liquidity_risk": 0.02,
        "max_drawdown_1y": 0.18,
    }
    assert penalty(aapl) == pytest.approx(3.80, abs=0.02)
