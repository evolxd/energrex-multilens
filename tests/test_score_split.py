"""Tests for splitting the blended score into its three readings.

Fixture numbers are the live 2026-08-04 readings for the tickers that made
the problem visible.
"""

import pytest

from scoring.score_split import (
    COMPANY_WEIGHTS,
    LEVERAGE_CLAUSE,
    VOLATILITY_CLAUSE,
    circuit_detail,
    company_score,
    momentum_score,
    risk_score,
    split_scores,
)

MU_DIMS = {
    "valuation": 88.55,
    "growth": 100.0,
    "quality": 72.68,
    "ai_exposure": 47.87,
    "expectation_gap": 71.91,
    "momentum": 93.76,
}
MU_BLENDED = 52.73
MU_RISK_PENALTY = 9.44


def test_company_score_excludes_momentum():
    """Where a price sits against its 200-day average says nothing about
    whether the business is any good."""
    with_momentum = dict(MU_DIMS)
    without = {k: v for k, v in MU_DIMS.items() if k != "momentum"}
    assert company_score(with_momentum) == pytest.approx(company_score(without))


def test_company_weights_are_renormalised():
    """The production weights sum to 0.90 because momentum holds 0.10.
    Without renormalising, a perfect company would score 90."""
    assert sum(COMPANY_WEIGHTS.values()) == pytest.approx(0.90)
    perfect = {dim: 100.0 for dim in COMPANY_WEIGHTS}
    assert company_score(perfect) == pytest.approx(100.0)


def test_mu_company_score_matches_the_live_reading():
    """MU shows 52.73 on the leaderboard against a company score of 78.2.
    Nothing about the business moved; its beta crossed 2.2."""
    assert company_score(MU_DIMS) == pytest.approx(78.2, abs=0.1)


def test_mu_split_makes_the_seventeen_point_cliff_legible():
    split = split_scores(
        MU_DIMS, risk_penalty=MU_RISK_PENALTY,
        beta=2.213, drawdown_abs=0.391, de_ratio=0.25,
        blended=MU_BLENDED,
    )
    assert split.company == pytest.approx(78.2, abs=0.1)
    assert split.momentum == pytest.approx(93.76)
    assert split.risk == pytest.approx(52.8, abs=0.1)
    assert split.circuit.clauses == [VOLATILITY_CLAUSE]
    assert split.company_vs_blended == pytest.approx(25.5, abs=0.2)


def test_missing_dimension_renormalises_rather_than_scoring_zero():
    partial = {"valuation": 80.0, "growth": 60.0}
    # 80*0.20 + 60*0.25 over a weight of 0.45, not over 0.90.
    assert company_score(partial) == pytest.approx((80 * 0.20 + 60 * 0.25) / 0.45)


def test_no_dimensions_returns_none_rather_than_zero():
    assert company_score({}) is None


def test_momentum_is_reported_untouched():
    assert momentum_score(MU_DIMS) == pytest.approx(93.76)
    assert momentum_score({"valuation": 50.0}) is None


# ── Risk ───────────────────────────────────────────────────────────────

def test_risk_score_inverts_the_penalty():
    assert risk_score(0.0) == pytest.approx(100.0)
    assert risk_score(20.0) == pytest.approx(0.0)
    assert risk_score(9.44) == pytest.approx(52.8, abs=0.1)


def test_risk_score_is_clamped_and_handles_missing():
    assert risk_score(50.0) == 0.0
    assert risk_score(-5.0) == 100.0
    assert risk_score(None) is None


# ── The circuit, and why it must stay separate ─────────────────────────

def test_volatility_clause_needs_both_conditions():
    """MU sat at a 39% drawdown for weeks; only when beta crossed 2.2 did the
    score fall seventeen points."""
    assert circuit_detail(2.142, 0.391, 0.25).triggered is False
    fired = circuit_detail(2.213, 0.391, 0.25)
    assert fired.triggered
    assert fired.clauses == [VOLATILITY_CLAUSE]


def test_leverage_clause_fires_on_a_calm_stock():
    """IBM: beta 0.70, calmer than the index, tripped purely on leverage.
    Collapsing this into the same label as ARM's beta of 3.91 loses the only
    part that matters."""
    ibm = circuit_detail(0.70, 0.375, 2.50)
    assert ibm.triggered
    assert ibm.clauses == [LEVERAGE_CLAUSE]
    assert "负债权益比" in ibm.detail


def test_both_clauses_are_reported_together():
    afrm = circuit_detail(3.62, 0.539, 3.50)
    assert afrm.clauses == [VOLATILITY_CLAUSE, LEVERAGE_CLAUSE]
    assert VOLATILITY_CLAUSE in afrm.label and LEVERAGE_CLAUSE in afrm.label


def test_untriggered_circuit_has_an_empty_label():
    quiet = circuit_detail(1.1, 0.15, 0.3)
    assert not quiet.triggered
    assert quiet.label == ""


def test_missing_inputs_do_not_fabricate_a_trigger():
    assert circuit_detail(None, None, None).triggered is False
    assert circuit_detail(3.0, None, None).triggered is False
    assert circuit_detail(None, 0.9, None).triggered is False


def test_drawdown_sign_does_not_matter():
    assert circuit_detail(2.5, -0.50, 0.1).triggered
    assert circuit_detail(2.5, 0.50, 0.1).triggered


# ── The whole split ────────────────────────────────────────────────────

def test_split_exposes_the_gap_the_single_number_hides():
    """APP: company 92.4, leaderboard 53.5. Thirty-nine points with nothing
    on screen to explain them."""
    split = split_scores(
        {"valuation": 95.0, "growth": 100.0, "quality": 88.0,
         "ai_exposure": 90.0, "expectation_gap": 85.0, "momentum": 60.0},
        risk_penalty=8.0, beta=2.53, drawdown_abs=0.50, de_ratio=1.50,
        blended=53.5,
    )
    assert split.company > 90
    assert split.circuit.triggered
    assert split.circuit.clauses == [VOLATILITY_CLAUSE]
    assert split.company_vs_blended > 35


def test_gap_is_none_when_the_blend_is_unknown():
    split = split_scores(MU_DIMS, risk_penalty=9.44)
    assert split.company_vs_blended is None
