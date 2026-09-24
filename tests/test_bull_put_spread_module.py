"""Unit tests for the private helpers extracted from
bull_put_spread_module.render() (see _scratch plan / conversation record):
_closest_in_tier, _row.

`_dte`/`_release_risk_label` used to live here too, but were consolidated
into spread_ui_common.py (2026-09-24, shared with bull_call_spread_module.py
which had byte-identical copies) -- their tests moved to
tests/test_spread_ui_common.py along with them.

These were closures trapped inside render() with no independent callers, so
there is no pre-extraction runtime snapshot to diff against (unlike the
account_monitor.py extractions) -- the safety net here is: (1) the
extraction itself is a mechanical closure-to-parameter conversion with zero
logic change, verified by diff review; (2) these tests, written against the
now-module-level functions, lock the documented algorithm going forward.

bull_put_spread_module.py has no module-level `st.*` calls (render() is the
only place Streamlit widgets are touched), so it imports cleanly here
without any streamlit stub.
"""
import datetime
import unittest

import bull_put_spread_module  # noqa: F401  (side effect: adds scoring/ to sys.path)
import macro_calendar
from bull_put_spread_module import _closest_in_tier, _row
from bull_put_spread import BullPutCandidate, score_bull_put_spread


class ClosestInTierTests(unittest.TestCase):
    """Covers all 4 branches of the original inline closure."""

    def test_multiple_candidates_in_tier_picks_the_middle_one(self):
        exp_with_dte = [("A", 20), ("B", 22), ("C", 24), ("D", 26), ("E", 28)]
        self.assertEqual(_closest_in_tier(20, 30, exp_with_dte), "C")

    def test_single_candidate_in_tier_is_returned(self):
        exp_with_dte = [("X", 25)]
        self.assertEqual(_closest_in_tier(20, 30, exp_with_dte), "X")

    def test_empty_tier_falls_back_to_nearest_midpoint(self):
        exp_with_dte = [("A", 5), ("B", 50)]
        # mid = 25 -> |5-25|=20 < |50-25|=25 -> "A" wins.
        self.assertEqual(_closest_in_tier(20, 30, exp_with_dte), "A")

    def test_no_expirations_at_all_returns_none(self):
        self.assertIsNone(_closest_in_tier(20, 30, []))


class RowTests(unittest.TestCase):
    """Round numbers chosen so every derived field can be hand-verified:
    width=5, max_loss=3, breakeven=98, rom=2/3, adr clips to score 35,
    buffer_pct clips to score 30, rom clips to score 25, dte=30 is in the
    full-score window -> total_score=100."""

    def setUp(self):
        self.candidate = BullPutCandidate(
            ticker="NVDA", expiration="2026-10-16", dte=30,
            short_strike=100.0, long_strike=95.0, net_credit=2.0, stock_price=110.0,
        )
        self.score = score_bull_put_spread(self.candidate)
        self.today = datetime.date(2026, 9, 1)

    def test_row_fields_match_hand_verified_values(self):
        row = _row(self.score, self.today)

        self.assertEqual(row["到期日"], "2026-10-16")
        self.assertEqual(row["DTE"], 30)
        self.assertEqual(row["Short/Long"], "100/95")
        self.assertEqual(row["Width"], 5.0)
        self.assertEqual(row["Net Credit"], 2.0)
        self.assertEqual(row["Max Profit"], 2.0)
        self.assertEqual(row["Max Loss"], 3.0)
        self.assertEqual(row["Breakeven"], 98.0)
        self.assertEqual(row["ROM"], "66.7%")
        self.assertEqual(row["ADR"], "811%")
        self.assertEqual(row["Buffer%"], "10.9%")
        self.assertEqual(row["盈亏平衡胜率"], "60.0%")
        self.assertEqual(row["ADR得分"], 35.0)
        self.assertEqual(row["Buffer得分"], 30.0)
        self.assertEqual(row["ROM得分"], 25.0)
        self.assertEqual(row["DTE得分"], 10.0)
        self.assertEqual(row["总分"], 100.0)

    def test_row_wires_release_risk_label_with_the_given_today(self):
        row = _row(self.score, self.today)
        expected = macro_calendar.release_risk_label(self.today, self.candidate.expiration)
        self.assertEqual(row["发布日风险"], expected)


if __name__ == "__main__":
    unittest.main()
