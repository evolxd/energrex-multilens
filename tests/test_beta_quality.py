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


def test_every_symbol_that_was_silently_defaulting_now_has_a_measured_beta():
    # 2026-09-21：KLAC/ONTO/PATH 也曾整段落在 1.0 的静默缺省上（$7,324 现货）。
    assert {"SPCX", "ETHU", "KLAC", "ONTO", "PATH"} <= set(beta_overrides())


def test_the_leveraged_names_are_far_from_the_1_0_default_they_replaced():
    # 这几个标的是当初做这件事的理由：缺省值不是无害的近似。
    for symbol in ("SPCX", "ETHU", "KLAC", "ONTO"):
        assert beta_overrides()[symbol] > 1.4


def test_a_measured_one_point_zero_is_allowed_and_is_not_the_same_as_defaulting():
    """PATH 测出来就是 0.99，这不是失败——是结论。

    它跟"没人测过所以取 1.0"必须能分开：后者在 DERIVED_BETAS 里查不到，
    前者查得到，而且带着 R²=0.08 这个"别太当真"的证据。把 beta≈1.0 当成
    测量失败排除掉，就等于规定只有刺激的结果才允许被记录。
    """
    entry = DERIVED_BETAS["PATH"]
    assert entry.beta == pytest.approx(1.0, abs=0.15)
    assert entry.is_low_confidence                      # R² 0.08，标着弱拟合
    assert "PATH" in beta_overrides()                   # 但确实进了表


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
    # Asserted as a property rather than a fixed symbol order: the betas are
    # re-measured weekly, so which name has the poorest fit changes over time.
    fits = [e.r_squared for e in low_confidence_held(["SPCX", "ETHU"])]
    assert fits == sorted(fits)


def test_describe_names_the_beta_and_its_fit():
    described = DERIVED_BETAS["SPCX"].describe()
    assert "2.57" in described and "0.16" in described
