"""Tests for the price/fundamentals basis guard.

The case this exists for: TSM's ADR price arrives in USD while its revenue,
EBITDA and net debt arrive in TWD. Enterprise value came out around -4.4e11,
every valuation ratio read "infinitely cheap", the valuation score pinned at
97, and no price in a 5%-400% grid ever crossed a band. Nothing raised, and
the chart looked merely odd rather than wrong.
"""

import pytest

from scoring.basis_check import (
    BASIS_CHECK_FIELDS,
    BASIS_MAX_DRIFT,
    basis_conflict_reason,
    basis_conflicts,
)


def test_tsm_currency_mismatch_is_caught():
    """Real numbers: carried EV/Sales 13.36, recomputed -0.10 from a USD market
    cap plus TWD net debt."""
    carried = {"ev_sales": 13.36, "forward_pe": 24.45}
    recomputed = {"ev_sales": -0.10, "forward_pe": 18.70}
    assert basis_conflicts(carried, recomputed) == ["ev_sales"]


def test_ordinary_staleness_is_not_a_conflict():
    """NVDA: the two sources differ by the usual few percent."""
    carried = {"ev_sales": 20.0, "forward_pe": 23.42}
    recomputed = {"ev_sales": 19.03, "forward_pe": 23.42}
    assert basis_conflicts(carried, recomputed) == []


def test_widest_legitimate_drift_still_passes():
    """The widest genuine disagreement measured across the tracked universe is
    2.28x on ev_sales. If this ever fails, the threshold has drifted into
    refusing real data."""
    carried = {"ev_sales": 10.0}
    recomputed = {"ev_sales": 22.8}
    assert basis_conflicts(carried, recomputed) == []


def test_currency_scale_error_without_a_sign_flip_is_caught():
    """A TWD-for-USD substitution on a company with net debt rather than net
    cash keeps the sign but moves the ratio by ~30x."""
    carried = {"ev_sales": 4.0}
    recomputed = {"ev_sales": 124.0}
    assert basis_conflicts(carried, recomputed) == ["ev_sales"]


def test_conflict_is_symmetric_in_direction():
    assert basis_conflicts({"ev_sales": 100.0}, {"ev_sales": 4.0}) == ["ev_sales"]
    assert basis_conflicts({"ev_sales": 4.0}, {"ev_sales": 100.0}) == ["ev_sales"]


@pytest.mark.parametrize("missing", [None, {}])
def test_missing_values_are_not_conflicts(missing):
    """Absent data is the caller's problem to report, not a basis error."""
    carried = {"ev_sales": 13.36} if missing is None else {}
    recomputed = {} if missing is None else {"ev_sales": 13.36}
    assert basis_conflicts(carried, recomputed) == []


def test_zero_is_not_treated_as_a_conflict():
    """Same rule as the scoring engine: a genuine zero is data, not a fault,
    and it carries no ratio information in either direction."""
    assert basis_conflicts({"ev_sales": 0.0}, {"ev_sales": 12.0}) == []
    assert basis_conflicts({"ev_sales": 12.0}, {"ev_sales": 0.0}) == []


def test_non_numeric_values_are_skipped_rather_than_raising():
    assert basis_conflicts({"ev_sales": "n/a"}, {"ev_sales": 12.0}) == []


def test_noisy_fields_are_excluded_from_the_check():
    """peg_ratio and fcf_yield disagree by up to 14.7x and 119x on good rows,
    so they must not gate the report."""
    assert "peg_ratio" not in BASIS_CHECK_FIELDS
    assert "fcf_yield" not in BASIS_CHECK_FIELDS


def test_threshold_leaves_room_above_the_widest_real_drift():
    assert BASIS_MAX_DRIFT > 2.28


def test_reason_names_the_offending_fields():
    reason = basis_conflict_reason(["ev_sales"])
    assert "ev_sales" in reason
    assert "不出图" in reason
