"""Unit tests for the private helpers to be extracted from
bull_call_spread_module.render(): _in_target_window, _row.

`_dte`/`_release_risk_label` used to live here too, but were consolidated
into spread_ui_common.py (2026-09-24, shared with bull_put_spread_module.py
which had byte-identical copies) -- their tests moved to
tests/test_spread_ui_common.py along with them.

Mirrors tests/test_bull_put_spread_module.py's approach (see that file's
docstring for why there is no pre-extraction runtime snapshot to diff
against here -- these closures currently have no independent caller).

Call's expiration-window logic (_in_target_window: a fixed calendar-month
window, e.g. Sep-Dec of the current year) is intentionally NOT the same
algorithm as Put's DTE-tier auto-selection (_closest_in_tier) -- confirmed
2026-09-24 as a deliberate strategy difference (Put sellers lean on a
multi-tier DTE ladder; Call buyers care about a specific delivery-month
window). Do not unify them.

bull_call_spread_module.py has no module-level `st.*` calls (render() is
the only place Streamlit widgets are touched), so it imports cleanly here
without any streamlit stub.
"""
import datetime
import unittest

import bull_call_spread_module  # noqa: F401  (side effect: adds scoring/ to sys.path)
import macro_calendar
from bull_call_spread_module import _in_target_window, _row
from bull_call_spread import BullCallCandidate, score_bull_call_spread


class InTargetWindowTests(unittest.TestCase):
    """Covers both conditions of `d.year == today.year and d.month in target_months`."""

    TODAY = datetime.date(2026, 6, 1)
    TARGET_MONTHS = (9, 10, 11, 12)

    def test_same_year_month_inside_window_is_true(self):
        self.assertTrue(_in_target_window("2026-09-15", self.TODAY, self.TARGET_MONTHS))

    def test_same_year_month_outside_window_is_false(self):
        self.assertFalse(_in_target_window("2026-08-15", self.TODAY, self.TARGET_MONTHS))

    def test_month_matches_but_wrong_year_is_false(self):
        """The year guard is easy to lose in a careless refactor: month 9
        alone is not sufficient, it must be *this* year's September."""
        self.assertFalse(_in_target_window("2027-09-15", self.TODAY, self.TARGET_MONTHS))

    def test_window_boundary_december_is_true(self):
        self.assertTrue(_in_target_window("2026-12-31", self.TODAY, self.TARGET_MONTHS))


class RowTests(unittest.TestCase):
    """Round numbers chosen so every derived field can be hand-verified:
    width=10, max_profit=7, max_loss=3 (=net_debit), breakeven=103,
    rom=7/3, adr clips to score 35, move_needed is negative (already past
    breakeven) and clips to score 30, rom clips to score 25, DTE score is
    always a flat 10 for calls -> total_score=100."""

    def setUp(self):
        self.candidate = BullCallCandidate(
            ticker="NVDA", expiration="2026-11-20", dte=60,
            long_strike=100.0, short_strike=110.0, net_debit=3.0, stock_price=105.0,
        )
        self.score = score_bull_call_spread(self.candidate)
        self.today = datetime.date(2026, 9, 1)

    def test_row_fields_match_hand_verified_values(self):
        row = _row(self.score, self.today)

        self.assertEqual(row["到期日"], "2026-11-20")
        self.assertEqual(row["DTE"], 60)
        self.assertEqual(row["Long/Short"], "100/110")
        self.assertEqual(row["Width"], 10.0)
        self.assertEqual(row["Net Debit"], 3.0)
        self.assertEqual(row["Max Profit"], 7.0)
        self.assertEqual(row["Max Loss"], 3.0)
        self.assertEqual(row["Breakeven"], 103.0)
        self.assertEqual(row["ROM"], "233.3%")
        self.assertEqual(row["ADR"], "1419%")
        self.assertEqual(row["所需涨幅%"], "-1.9%")
        self.assertEqual(row["ADR得分"], 35.0)
        self.assertEqual(row["所需涨幅得分"], 30.0)
        self.assertEqual(row["ROM得分"], 25.0)
        self.assertEqual(row["DTE得分"], 10.0)
        self.assertEqual(row["总分"], 100.0)

    def test_row_wires_release_risk_label_with_the_given_today(self):
        row = _row(self.score, self.today)
        expected = macro_calendar.release_risk_label(self.today, self.candidate.expiration)
        self.assertEqual(row["发布日风险"], expected)


if __name__ == "__main__":
    unittest.main()
