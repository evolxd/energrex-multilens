"""Tests for account/repository.py::compute_margin_usage_pct.

2026-09-11: user spotted the "保证金使用率趋势" chart spiking to ~100% once
in mid-June then sitting flat at 0% for the following three months. Root
cause traced to two bugs in the retired inline version of this formula:
(1) `margin_used=None` ("we didn't parse this field this sync") and
`margin_used=0` ("Firstrade showed $0.00") were both silently collapsed to
a confident 0.0% via `mu = data.get(...) or 0`, so months of "we don't
know" render as a flat, trustworthy-looking 0% line instead of a gap;
(2) a handful of historical rows in data/energrex.db turned out to be
computed with a different, no-longer-present formula
(margin_used/(margin_used+margin_available)) -- likely a leftover from
before this repo's git history was scrubbed (the DB file itself isn't
tracked by git). This function is the single, testable, honest version:
unknown stays None, not 0.
"""
import unittest

from account.repository import compute_margin_usage_pct


class ComputeMarginUsagePctTests(unittest.TestCase):

    def test_normal_case(self):
        self.assertAlmostEqual(compute_margin_usage_pct(5000.0, 50000.0), 10.0)

    def test_confirmed_zero_margin_used_is_a_real_zero(self):
        self.assertEqual(compute_margin_usage_pct(0.0, 50000.0), 0.0)

    def test_missing_margin_used_is_none_not_zero(self):
        """This is the bug: previously `None or 0` made this indistinguishable
        from a confirmed $0.00 margin balance."""
        self.assertIsNone(compute_margin_usage_pct(None, 50000.0))

    def test_missing_total_equity_is_none(self):
        self.assertIsNone(compute_margin_usage_pct(5000.0, None))

    def test_zero_total_equity_is_none_not_division_by_zero(self):
        self.assertIsNone(compute_margin_usage_pct(5000.0, 0.0))

    def test_negative_margin_used_from_bad_scrape_passes_through(self):
        """Not this function's job to sanity-check upstream scrape garbage --
        it only owns the division. A negative cash/margin_used pairing
        (parser picked up the wrong number) should surface as a visibly
        wrong negative percentage on the chart, not get silently clamped."""
        self.assertAlmostEqual(compute_margin_usage_pct(-363.05, 36513.33), -0.994, places=2)

    def test_historical_row_no_longer_matches_retired_formula(self):
        """The poisoned 2026-06-17 row: margin_used=8401.35, margin_available=3.0,
        stored margin_usage_pct=99.96 (matches mu/(mu+avail), a formula this
        codebase's git history has never contained). The correct value under
        the one true formula (mu/total_equity), using that row's own
        total_equity=34526.24, is ~24.3%, not ~100%."""
        got = compute_margin_usage_pct(8401.35, 34526.24)
        self.assertAlmostEqual(got, 24.336, places=2)
        self.assertLess(got, 99.0)


if __name__ == "__main__":
    unittest.main()
