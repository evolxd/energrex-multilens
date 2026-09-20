"""Measured betas, and the warning that stops a weak one passing for a strong one.

Two of the largest holdings (SPCX, ETHU) were absent from the beta table, so
`account.risk.compute_portfolio_stress_test` silently applied its `1.0` default
to both -- understating Beta-Delta in the direction that makes a leveraged book
look calm. Vendor data could not fix it: Firstrade reported 25.14 for SPCX,
above the 7.30 ceiling the volatility ratio allows even at perfect correlation,
and Alpha Vantage returned None for both names.
"""

import pytest

from account.beta_quality import (
    DERIVED_BETAS,
    LOW_CONFIDENCE_R2,
    beta_overrides,
    low_confidence_held,
)


def test_both_previously_missing_symbols_now_have_a_measured_beta():
    assert set(beta_overrides()) == {"SPCX", "ETHU"}


def test_measured_betas_are_far_from_the_1_0_default_they_replaced():
    # The point of the exercise: the default was not a harmless approximation.
    for beta in beta_overrides().values():
        assert beta > 2.0


@pytest.mark.parametrize("symbol", sorted(DERIVED_BETAS))
def test_every_entry_carries_the_evidence_behind_it(symbol):
    entry = DERIVED_BETAS[symbol]
    assert entry.observations >= 30                 # enough to regress at all
    assert 0.0 <= entry.r_squared <= 1.0
    assert entry.std_error > 0
    assert entry.sample_start < entry.sample_end
    assert entry.note


@pytest.mark.parametrize("symbol", sorted(DERIVED_BETAS))
def test_beta_stays_under_the_volatility_ratio_ceiling(symbol):
    """Beta cannot exceed vol_ratio, which is what makes 25.14 impossible.

    SPCX's ratio was 7.30 and ETHU's 8.70; a regression beta above that would
    mean a correlation over 1.
    """
    assert DERIVED_BETAS[symbol].beta < 7.0


def test_both_names_are_flagged_low_confidence():
    # Beta above 2.5 with R² under 0.2: they move a lot, but mostly not with
    # the market. Sizing a hedge off beta alone will disappoint.
    for entry in DERIVED_BETAS.values():
        assert entry.is_low_confidence
        assert entry.r_squared < LOW_CONFIDENCE_R2


def test_only_symbols_actually_held_are_reported():
    assert [e.symbol for e in low_confidence_held(["ETHU"])] == ["ETHU"]
    assert low_confidence_held(["NVDA", "AVGO"]) == []
    assert low_confidence_held([]) == []


def test_holdings_are_matched_case_insensitively_and_untrimmed():
    assert [e.symbol for e in low_confidence_held([" ethu "])] == ["ETHU"]


def test_worst_fit_is_reported_first():
    reported = low_confidence_held(["SPCX", "ETHU"])
    assert [e.symbol for e in reported] == ["ETHU", "SPCX"]   # R² 0.110 < 0.163


def test_describe_names_the_beta_and_its_fit():
    described = DERIVED_BETAS["SPCX"].describe()
    assert "2.57" in described and "0.16" in described
