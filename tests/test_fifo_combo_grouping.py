"""Tests for account/fifo.py's group_realized_trades_into_combos.

Prompted directly by the user (2026-09-06): "我现在长期使用spread option，你分开
计算胜率对我不公平啊" -- scoring a spread's protective leg as its own
independent win/loss misrepresents a strategy that is only ever meant to be
judged as one combined position. This is how professional options
performance reporting actually works: win/loss and P&L on the WHOLE spread,
not the leg.
"""
import unittest

from account.fifo import calculate_fifo_matches, group_realized_trades_into_combos


def _leg(underlying="PLTR", expiry="2026-10-16", option_type="put",
         open_date="2026-06-01", lot_direction="short", quantity=1.0,
         open_cash=900.0, close_cash=-100.0, realized_pnl=800.0):
    return {
        "underlying": underlying, "symbol": f"{underlying}FAKE",
        "strategy_type": f"{lot_direction}_{option_type}",
        "lot_direction": lot_direction, "open_date": open_date,
        "close_date": "2026-07-01", "holding_days": 30,
        "quantity": quantity, "open_cash": open_cash, "close_cash": close_cash,
        "realized_pnl": realized_pnl,
        "win_loss": "win" if realized_pnl > 0 else "loss",
        "option_type": option_type, "expiry": expiry, "strike": 150.0,
    }


class ComboGroupingUnitTests(unittest.TestCase):

    def test_opposite_legs_same_day_same_qty_get_paired(self):
        """The exact scenario the user described: short put (wins alone)
        + long put (loses alone), same underlying/expiry/day -> one combo."""
        short_leg = _leg(lot_direction="short", quantity=1.0,
                          open_cash=900.0, realized_pnl=800.0)   # wins alone
        long_leg  = _leg(lot_direction="long", quantity=1.0,
                          open_cash=-300.0, realized_pnl=-250.0)  # loses alone
        realized = [short_leg, long_leg]
        group_realized_trades_into_combos(realized)

        self.assertEqual(short_leg["combo_id"], long_leg["combo_id"])
        self.assertIsNotNone(short_leg["combo_id"])
        # net_open_cash = 900 + (-300) = 600 > 0 -> credit spread
        self.assertEqual(short_leg["combo_strategy"], "put_credit_spread")
        self.assertEqual(long_leg["combo_strategy"], "put_credit_spread")
        # combined: 800 + (-250) = 550, a real win, even though the long
        # leg alone shows a loss
        self.assertAlmostEqual(short_leg["combo_pnl"], 550.0)
        self.assertAlmostEqual(long_leg["combo_pnl"], 550.0)

    def test_only_the_short_leg_is_the_combo_head(self):
        short_leg = _leg(lot_direction="short")
        long_leg  = _leg(lot_direction="long", open_cash=-300.0, realized_pnl=-250.0)
        realized = [short_leg, long_leg]
        group_realized_trades_into_combos(realized)
        self.assertTrue(short_leg["is_combo_head"])
        self.assertFalse(long_leg["is_combo_head"])

    def test_debit_spread_labeled_correctly(self):
        """Net cash paid at open (long leg costs more than the short leg
        brings in) -> debit spread, not credit."""
        long_leg  = _leg(lot_direction="long", open_cash=-900.0, realized_pnl=200.0)
        short_leg = _leg(lot_direction="short", open_cash=300.0, realized_pnl=-100.0)
        realized = [long_leg, short_leg]
        group_realized_trades_into_combos(realized)
        # net_open_cash = -900 + 300 = -600 < 0 -> debit
        self.assertEqual(long_leg["combo_strategy"], "put_debit_spread")

    def test_unpaired_naked_leg_stays_its_own_single_combo(self):
        naked = _leg(lot_direction="short")
        realized = [naked]
        group_realized_trades_into_combos(realized)
        self.assertEqual(naked["combo_strategy"], "short_put")   # unchanged
        self.assertEqual(naked["combo_pnl"], naked["realized_pnl"])
        self.assertTrue(naked["is_combo_head"])

    def test_mismatched_quantity_is_not_force_paired(self):
        """2-lot short vs 1-lot long on the same day/underlying/expiry --
        exact-quantity pairing must NOT guess a partial split."""
        short_leg = _leg(lot_direction="short", quantity=2.0)
        long_leg  = _leg(lot_direction="long", quantity=1.0, open_cash=-300.0)
        realized = [short_leg, long_leg]
        group_realized_trades_into_combos(realized)
        self.assertNotEqual(short_leg["combo_id"], long_leg["combo_id"])
        self.assertEqual(short_leg["combo_strategy"], "short_put")
        self.assertEqual(long_leg["combo_strategy"], "long_put")

    def test_different_open_dates_are_not_paired(self):
        short_leg = _leg(lot_direction="short", open_date="2026-06-01")
        long_leg  = _leg(lot_direction="long", open_date="2026-06-02", open_cash=-300.0)
        realized = [short_leg, long_leg]
        group_realized_trades_into_combos(realized)
        self.assertNotEqual(short_leg["combo_id"], long_leg["combo_id"])

    def test_different_underlyings_are_not_paired(self):
        short_leg = _leg(lot_direction="short", underlying="PLTR")
        long_leg  = _leg(lot_direction="long", underlying="NVDA", open_cash=-300.0)
        realized = [short_leg, long_leg]
        group_realized_trades_into_combos(realized)
        self.assertNotEqual(short_leg["combo_id"], long_leg["combo_id"])

    def test_call_spreads_pair_independently_of_put_spreads_same_day(self):
        """A put spread and a call spread opened the same day on the same
        underlying must not cross-pair with each other."""
        put_short  = _leg(option_type="put", lot_direction="short")
        put_long   = _leg(option_type="put", lot_direction="long", open_cash=-300.0)
        call_short = _leg(option_type="call", lot_direction="short")
        call_long  = _leg(option_type="call", lot_direction="long", open_cash=-300.0)
        realized = [put_short, put_long, call_short, call_long]
        group_realized_trades_into_combos(realized)
        self.assertEqual(put_short["combo_id"], put_long["combo_id"])
        self.assertEqual(call_short["combo_id"], call_long["combo_id"])
        self.assertNotEqual(put_short["combo_id"], call_short["combo_id"])

    def test_two_identical_spreads_same_day_pair_separately_not_cross_matched(self):
        """Two bull put spreads opened the same day, same underlying/expiry,
        each internally consistent -- must not accidentally cross-pair leg
        A's short with leg B's long."""
        s1 = _leg(lot_direction="short", realized_pnl=100.0)
        l1 = _leg(lot_direction="long", open_cash=-300.0, realized_pnl=-50.0)
        s2 = _leg(lot_direction="short", realized_pnl=120.0)
        l2 = _leg(lot_direction="long", open_cash=-300.0, realized_pnl=-60.0)
        realized = [s1, l1, s2, l2]
        group_realized_trades_into_combos(realized)
        ids = {id(s1): s1["combo_id"], id(l1): l1["combo_id"],
               id(s2): s2["combo_id"], id(l2): l2["combo_id"]}
        # every leg got paired with exactly one other leg (4 legs -> 2 combos)
        self.assertEqual(len(set(ids.values())), 2)
        # and the combined P&L per combo is internally consistent (100-50=50 or 120-60=60)
        combo_pnls = sorted([s1["combo_pnl"], s2["combo_pnl"]])
        self.assertEqual(combo_pnls, [50.0, 60.0])


class ComboSummaryEndToEndTests(unittest.TestCase):
    """Through the real calculate_fifo_matches entry point, not just the
    grouping helper in isolation."""

    def test_credit_spread_scores_as_one_win_not_a_win_and_a_loss(self):
        rows = [
            # open bull put spread: sell 150p, buy 140p
            {"trade_date": "2026-06-01", "type": "SELL TO OPEN",
             "symbol": "NVDA260716P00150000", "quantity": -1, "price": 9.0, "amount": 899.95},
            {"trade_date": "2026-06-01", "type": "BUY TO OPEN",
             "symbol": "NVDA260716P00140000", "quantity": 1, "price": 3.0, "amount": -300.05},
            # close both legs later, same day, spread finishes deep OTM (max profit)
            {"trade_date": "2026-07-10", "type": "BUY TO CLOSE",
             "symbol": "NVDA260716P00150000", "quantity": 1, "price": 0.5, "amount": -50.02},
            {"trade_date": "2026-07-10", "type": "SELL TO CLOSE",
             "symbol": "NVDA260716P00140000", "quantity": -1, "price": 0.05, "amount": 4.98},
        ]
        result = calculate_fifo_matches(rows)

        # leg-level: short put wins, long put loses (exactly the "unfair" split)
        by_type = {r["strategy_type"]: r for r in result["realized"]}
        self.assertEqual(by_type["short_put"]["win_loss"], "win")
        self.assertEqual(by_type["long_put"]["win_loss"], "loss")

        # combo-level: the whole spread is one winning trade
        self.assertEqual(result["combo_summary"]["combo_count"], 1)
        self.assertEqual(result["combo_summary"]["wins"], 1)
        self.assertEqual(result["combo_summary"]["losses"], 0)
        self.assertGreater(result["combo_summary"]["total_realized_pnl"], 0)

        for leg in result["realized"]:
            self.assertEqual(leg["combo_strategy"], "put_credit_spread")


if __name__ == "__main__":
    unittest.main()
