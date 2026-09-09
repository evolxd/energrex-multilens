"""Tests for account/performance.py's by_combo Kelly inputs
(avg_win/avg_loss/payoff_b/kelly_f/close_date_min/max).

Written for docs/SIX_GATES_AND_EXPOSURE_DESIGN.md §2 -- that doc's payoff
-ratio table was hand-computed once; this pins the real code that now
computes it, plus the two things the user explicitly asked every
statistic to carry: date range and sample size.

2026-09-09: this used to AST-slice and exec account_monitor.py itself to
reach these two functions (they were module-level functions inside that
5000+-line UI script, with no other way to import them). They've since
been extracted to account/performance.py (a real importable module --
门③仓位管理 now needs them too, not just this test and account_monitor.py's
own UI), so this is a plain import now.
"""
import pathlib
import tempfile
import unittest

import account.db as account_db
from account.performance import compute_performance_stats, wilson_interval

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACCT = "test_kelly_acct"


class PerformanceStatsKellyTests(unittest.TestCase):

    _tmp = None
    _original_db_path = None
    _compute = staticmethod(compute_performance_stats)
    _wilson = staticmethod(wilson_interval)

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._original_db_path = account_db.DB_PATH
        account_db.DB_PATH = pathlib.Path(cls._tmp.name) / "test_kelly.db"
        account_db.init_db()

    @classmethod
    def tearDownClass(cls):
        account_db.DB_PATH = cls._original_db_path
        if cls._tmp:
            cls._tmp.cleanup()

    def setUp(self):
        conn = account_db.db()
        conn.execute("DELETE FROM option_realized_trades WHERE account_id=?", (ACCT,))
        conn.commit()
        conn.close()

    def _seed(self, rows):
        """rows: list of (combo_strategy, close_date, realized_pnl)."""
        conn = account_db.db()
        for i, (combo_strategy, close_date, pnl) in enumerate(rows):
            conn.execute(
                """
                INSERT INTO option_realized_trades
                  (account_id, underlying, symbol, strategy_type, lot_direction,
                   open_date, close_date, holding_days, quantity, open_cash, close_cash,
                   realized_pnl, return_on_risk, win_loss, option_type, expiry, strike,
                   created_at, combo_id, combo_strategy)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ACCT, "TEST", f"TEST{i}", "short_put", "short",
                    close_date, close_date, 0, 1, 0.0, 0.0,
                    pnl, 0.0, "win" if pnl > 0 else "loss", "put", "2026-12-18", 100.0,
                    "2026-01-01T00:00:00", f"combo_{i}", combo_strategy,
                ),
            )
        conn.commit()
        conn.close()

    def test_avg_win_avg_loss_and_payoff_ratio_match_hand_computation(self):
        # 3 wins (600, 500, 400 -> avg 500), 2 losses (-300, -100 -> avg -200)
        self._seed([
            ("put_credit_spread", "2026-01-05", 600.0),
            ("put_credit_spread", "2026-02-10", 500.0),
            ("put_credit_spread", "2026-03-15", 400.0),
            ("put_credit_spread", "2026-04-01", -300.0),
            ("put_credit_spread", "2026-05-20", -100.0),
        ])
        stats = self._compute(ACCT)
        bucket = stats["by_combo"]["put_credit_spread"]
        self.assertEqual(bucket["count"], 5)
        self.assertAlmostEqual(bucket["win_rate"], 0.6)
        self.assertAlmostEqual(bucket["avg_win"], 500.0)
        self.assertAlmostEqual(bucket["avg_loss"], -200.0)
        self.assertAlmostEqual(bucket["payoff_b"], 2.5)   # |500/-200|

    def test_kelly_f_matches_p_minus_q_over_b(self):
        # win_rate=0.6, payoff_b=2.5 -> f* = 0.6 - 0.4/2.5 = 0.44
        self._seed([
            ("put_credit_spread", "2026-01-05", 600.0),
            ("put_credit_spread", "2026-02-10", 500.0),
            ("put_credit_spread", "2026-03-15", 400.0),
            ("put_credit_spread", "2026-04-01", -300.0),
            ("put_credit_spread", "2026-05-20", -100.0),
        ])
        stats = self._compute(ACCT)
        bucket = stats["by_combo"]["put_credit_spread"]
        self.assertAlmostEqual(bucket["kelly_f"], 0.44, places=4)

    def test_negative_kelly_signals_dont_take_this_trade_type(self):
        # Real shape from the doc's call_debit_spread example: losing edge
        # (win_rate too low relative to its own payoff) -> negative Kelly.
        self._seed([
            ("call_debit_spread", "2026-01-05", 1000.0),
            ("call_debit_spread", "2026-02-01", -800.0),
            ("call_debit_spread", "2026-02-15", -800.0),
        ])
        stats = self._compute(ACCT)
        bucket = stats["by_combo"]["call_debit_spread"]
        # win_rate=1/3, payoff_b=1000/800=1.25 -> f* = 1/3 - (2/3)/1.25 = -0.2
        self.assertLess(bucket["kelly_f"], 0)

    def test_close_date_range_and_sample_size_are_carried_on_the_bucket(self):
        """The user's explicit ask: every statistic must carry its date
        range and total sample size -- baked into the data, not just
        mentioned in prose."""
        self._seed([
            ("put_credit_spread", "2025-07-18", 100.0),
            ("put_credit_spread", "2026-09-01", -50.0),
        ])
        stats = self._compute(ACCT)
        bucket = stats["by_combo"]["put_credit_spread"]
        self.assertEqual(bucket["count"], 2)
        self.assertEqual(bucket["close_date_min"], "2025-07-18")
        self.assertEqual(bucket["close_date_max"], "2026-09-01")

    def test_payoff_b_and_kelly_f_are_none_when_no_losses_yet(self):
        """A bucket with zero losing trades so far has an undefined payoff
        ratio -- must report None, not divide by zero or fabricate a number."""
        self._seed([
            ("call_credit_spread", "2026-06-15", 5199.52),
        ])
        stats = self._compute(ACCT)
        bucket = stats["by_combo"]["call_credit_spread"]
        self.assertIsNone(bucket["payoff_b"])
        self.assertIsNone(bucket["kelly_f"])
        # Pool has no losses either (this is the only trade) -> shrunk is
        # also undefined, not silently falling back to a fabricated number.
        self.assertIsNone(bucket["payoff_b_shrunk"])
        self.assertIsNone(bucket["kelly_f_shrunk"])

    def test_shrinkage_pulls_small_buckets_toward_pooled_stats_with_k_20(self):
        """User's pushback: why fragment into small per-strategy buckets
        instead of one pooled statistic? Answer implemented here: keep the
        buckets (different spread types are structurally different bets),
        but shrink each bucket's win_rate/payoff_b toward the pooled
        (all-combo) value, weighted by K_SHRINK virtual trades. User briefly
        set K=30, then explicitly asked to change it back to K=20.

        put_credit_spread: n=5, win_rate=0.6, payoff_b=2.5
        call_debit_spread: n=3, win_rate=1/3, payoff_b=1.25
        Pooled (n=8): 4 wins (600,500,400,1000) avg=625,
                      4 losses (-300,-100,-800,-800) avg=-500
                      -> pooled win_rate=0.5, pooled payoff_b=1.25
        """
        self._seed([
            ("put_credit_spread", "2026-01-05", 600.0),
            ("put_credit_spread", "2026-02-10", 500.0),
            ("put_credit_spread", "2026-03-15", 400.0),
            ("put_credit_spread", "2026-04-01", -300.0),
            ("put_credit_spread", "2026-05-20", -100.0),
            ("call_debit_spread", "2026-01-10", 1000.0),
            ("call_debit_spread", "2026-02-01", -800.0),
            ("call_debit_spread", "2026-02-15", -800.0),
        ])
        stats = self._compute(ACCT)
        self.assertEqual(stats["by_combo"]["put_credit_spread"]["shrink_k"], 20)

        pcs = stats["by_combo"]["put_credit_spread"]
        # (5*0.6 + 20*0.5) / 25 = 13/25
        self.assertAlmostEqual(pcs["win_rate_shrunk"], 13 / 25, places=6)
        # (5*2.5 + 20*1.25) / 25 = 37.5/25 = 1.5
        self.assertAlmostEqual(pcs["payoff_b_shrunk"], 1.5, places=6)
        self.assertAlmostEqual(pcs["kelly_f_shrunk"], 0.2, places=6)
        # Shrinkage must pull the point estimate toward the pool, i.e.
        # strictly between the bucket's raw number and the pooled number.
        self.assertLess(pcs["win_rate_shrunk"], pcs["win_rate"])
        self.assertGreater(pcs["win_rate_shrunk"], 0.5)

        cds = stats["by_combo"]["call_debit_spread"]
        # (3*(1/3) + 20*0.5) / 23 = 11/23
        self.assertAlmostEqual(cds["win_rate_shrunk"], 11 / 23, places=6)
        # call_debit_spread's own payoff_b (1.25) equals the pooled payoff_b
        # here, so shrinkage leaves it unchanged.
        self.assertAlmostEqual(cds["payoff_b_shrunk"], 1.25, places=6)
        self.assertGreater(cds["win_rate_shrunk"], cds["win_rate"])

    def test_wilson_interval_matches_hand_computed_reference(self):
        """p=0.5, n=10, z=1.96 is a standard textbook Wilson interval
        example -- hand-derived here (not just re-trusting the code):
        center = (0.5 + 3.8416/20) / 1.38416 = 0.5 exactly (symmetric case)
        margin = 1.96 * sqrt(0.025 + 0.009604) / 1.38416 ~= 0.26341
        -> (0.2366, 0.7634)."""
        lower, upper = self._wilson(0.5, 10)
        self.assertAlmostEqual(lower, 0.2366, places=3)
        self.assertAlmostEqual(upper, 0.7634, places=3)

    def test_wilson_interval_widens_as_sample_shrinks(self):
        """Same point estimate (50%), smaller n -> wider interval. This is
        the whole point of adding it: a bare '50%' with n=4 should look
        far less trustworthy on screen than '50%' with n=200."""
        lo_small, hi_small = self._wilson(0.5, 4)
        lo_big, hi_big = self._wilson(0.5, 200)
        self.assertLess(lo_small, lo_big)
        self.assertGreater(hi_small, hi_big)
        self.assertGreater(hi_small - lo_small, hi_big - lo_big)

    def test_by_combo_carries_wilson_ci_on_raw_win_rate_not_shrunk(self):
        """Wilson CI must be computed from the bucket's own raw win_rate/n,
        never from win_rate_shrunk -- mixing the two would be statistically
        meaningless (Wilson assumes n independent Bernoulli trials at the
        raw proportion, not a blended estimate)."""
        self._seed([
            ("put_credit_spread", "2026-01-05", 600.0),
            ("put_credit_spread", "2026-02-10", 500.0),
            ("put_credit_spread", "2026-03-15", 400.0),
            ("put_credit_spread", "2026-04-01", -300.0),
            ("put_credit_spread", "2026-05-20", -100.0),
        ])
        stats = self._compute(ACCT)
        bucket = stats["by_combo"]["put_credit_spread"]
        expected_lower, expected_upper = self._wilson(bucket["win_rate"], bucket["count"])
        self.assertAlmostEqual(bucket["win_rate_ci_lower"], expected_lower, places=9)
        self.assertAlmostEqual(bucket["win_rate_ci_upper"], expected_upper, places=9)
        # Sanity: the raw point estimate must sit inside its own interval.
        self.assertLessEqual(bucket["win_rate_ci_lower"], bucket["win_rate"])
        self.assertGreaterEqual(bucket["win_rate_ci_upper"], bucket["win_rate"])


if __name__ == "__main__":
    unittest.main()
