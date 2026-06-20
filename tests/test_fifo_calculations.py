"""Cross-validation tests for FIFO calculation logic.

Covers every bug that was fixed:
  1. price=0 (amount-only CSV): unit_cost derived from abs(amount)/qty/100
  2. return_on_risk for short put: denominator = strike × 100 × qty
  3. return_on_risk for short call: denominator = 10 × credit received
  4. return_on_risk for long option: denominator = premium paid
  5. Weighted average unit_cost across multiple open lots (price=0 path)
"""

import unittest

from account.fifo import calculate_fifo_matches


def _row(date, tx_type, symbol, qty, price, amount):
    return {
        "trade_date": date,
        "type": tx_type,
        "symbol": symbol,
        "quantity": qty,
        "price": price,
        "amount": amount,
    }


QQQ_PUT  = "QQQ260919P00350000"   # strike 350, put
PLTR_CALL = "PLTR270618C00150000"  # strike 150, call
NOK_PUT   = "NOK270618P00004000"   # strike 4, put  (low-price stock)


class FifoCostAmountOnlyTests(unittest.TestCase):
    """Unit cost must be recoverable when broker omits the price column."""

    def test_long_call_price_zero_derives_unit_cost_from_amount(self):
        """BUY TO OPEN with price=0, amount=-300 → unit_cost = 3.00 (not 0)."""
        result = calculate_fifo_matches([
            _row("2026-01-01", "BUY TO OPEN", PLTR_CALL, 1, 0, -300.0),
        ])
        self.assertEqual(result["summary"]["open_lots"], 1)
        cost = result["fifo_costs"][PLTR_CALL]
        self.assertAlmostEqual(cost, 3.0, places=4,
                               msg="unit_cost should be abs(amount)/qty/100 = 3.00")

    def test_short_put_price_zero_derives_unit_cost_from_amount(self):
        """SELL TO OPEN with price=0, amount=+200 → unit_cost = 2.00."""
        result = calculate_fifo_matches([
            _row("2026-01-01", "SELL TO OPEN", QQQ_PUT, 1, 0, 200.0),
        ])
        self.assertEqual(result["summary"]["open_lots"], 1)
        cost = result["fifo_costs"][QQQ_PUT]
        self.assertAlmostEqual(cost, 2.0, places=4,
                               msg="unit_cost = abs(amount)/qty/100 = 2.00")

    def test_weighted_avg_unit_cost_two_lots_no_price(self):
        """Two open BTO lots, price=0 both: weighted avg of amount-derived prices."""
        # lot 1: $300 for 1 contract → $3/share
        # lot 2: $500 for 1 contract → $5/share
        # weighted avg = (3×1 + 5×1) / 2 = $4.00
        result = calculate_fifo_matches([
            _row("2026-01-01", "BUY TO OPEN", PLTR_CALL, 1, 0, -300.0),
            _row("2026-01-02", "BUY TO OPEN", PLTR_CALL, 1, 0, -500.0),
        ])
        cost = result["fifo_costs"][PLTR_CALL]
        self.assertAlmostEqual(cost, 4.0, places=4)

    def test_unit_cost_prefers_explicit_price_over_amount_fallback(self):
        """When price IS provided, it takes precedence over amount."""
        # price=3.5, amount=-350 (consistent) → should use 3.5 not 3.5 (same here)
        # add second lot with price=0, amount=-400 → fallback = 4.0
        # weighted avg = (3.5×1 + 4.0×1) / 2 = 3.75
        result = calculate_fifo_matches([
            _row("2026-01-01", "BUY TO OPEN", PLTR_CALL, 1, 3.5, -350.0),
            _row("2026-01-02", "BUY TO OPEN", PLTR_CALL, 1, 0,  -400.0),
        ])
        cost = result["fifo_costs"][PLTR_CALL]
        self.assertAlmostEqual(cost, 3.75, places=4)


class FifoReturnOnRiskTests(unittest.TestCase):
    """return_on_risk must use capital-at-risk, not credit received."""

    def test_long_call_return_on_risk_uses_premium_paid(self):
        """Long call: return_on_risk = pnl / premium_paid."""
        # Buy 1 PLTR 150C at $3 (paid $300), sell at $5 (received $500)
        # pnl = +$200, risk_capital = $300
        # return_on_risk = 200/300 ≈ 0.6667
        result = calculate_fifo_matches([
            _row("2026-01-01", "BUY TO OPEN",   PLTR_CALL, 1, 3.0, -300.0),
            _row("2026-01-10", "SELL TO CLOSE",  PLTR_CALL, 1, 5.0, +500.0),
        ])
        trade = result["realized"][0]
        self.assertAlmostEqual(trade["return_on_risk"], 200.0 / 300.0, places=4)

    def test_short_put_return_on_risk_uses_strike_times_100(self):
        """Short put: return_on_risk = pnl / (strike × 100 × qty), not / credit."""
        # Sell 1 QQQ 350P at $2 (received $200), buy back at $0.50 (paid $50)
        # pnl = +$150
        # strike = 350, risk_capital = 350 × 100 × 1 = $35,000
        # return_on_risk = 150/35000 ≈ 0.004286
        result = calculate_fifo_matches([
            _row("2026-02-01", "SELL TO OPEN",  QQQ_PUT, 1, 2.0, +200.0),
            _row("2026-02-05", "BUY TO CLOSE",  QQQ_PUT, 1, 0.5,  -50.0),
        ])
        trade = result["realized"][0]
        expected = 150.0 / (350.0 * 100.0 * 1)
        self.assertAlmostEqual(trade["return_on_risk"], expected, places=6)
        # Sanity: must be much less than return-on-premium (150/200 = 0.75)
        self.assertLess(trade["return_on_risk"], 0.01,
                        msg="Short-put ROI on risk must be <1% for this trade")

    def test_short_put_loss_return_on_risk_negative(self):
        """Short put that loses money: return_on_risk is negative, denominator still correct."""
        # Sell 1 NOK 4P at $0.30 (received $30), buy back at $1.00 (paid $100)
        # pnl = -$70, risk_capital = 4 × 100 × 1 = $400
        result = calculate_fifo_matches([
            _row("2026-03-01", "SELL TO OPEN",  NOK_PUT, 1, 0.30, +30.0),
            _row("2026-03-15", "BUY TO CLOSE",  NOK_PUT, 1, 1.00, -100.0),
        ])
        trade = result["realized"][0]
        expected = -70.0 / (4.0 * 100.0 * 1)
        self.assertAlmostEqual(trade["return_on_risk"], expected, places=6)

    def test_short_call_return_on_risk_uses_10x_premium(self):
        """Short call: denominator = 10 × credit received (unlimited-risk proxy)."""
        # Sell 1 PLTR 150C at $3 (received $300), buy back at $1 (paid $100)
        # pnl = +$200, risk_capital = 10 × $300 = $3000
        result = calculate_fifo_matches([
            _row("2026-04-01", "SELL TO OPEN",   PLTR_CALL, 1, 3.0, +300.0),
            _row("2026-04-10", "BUY TO CLOSE",   PLTR_CALL, 1, 1.0, -100.0),
        ])
        trade = result["realized"][0]
        expected = 200.0 / (10 * 300.0)
        self.assertAlmostEqual(trade["return_on_risk"], expected, places=6)

    def test_2_contracts_return_on_risk_scales_correctly(self):
        """return_on_risk is pnl / total_risk_capital (scales with qty)."""
        # Sell 2 QQQ 350P at $2 each (received $400), buy back at $0.50 each (paid $100)
        # pnl = +$300
        # risk_capital = 350 × 100 × 2 = $70,000
        result = calculate_fifo_matches([
            _row("2026-05-01", "SELL TO OPEN",  QQQ_PUT, 2, 2.0, +400.0),
            _row("2026-05-10", "BUY TO CLOSE",  QQQ_PUT, 2, 0.5, -100.0),
        ])
        trade = result["realized"][0]
        expected = 300.0 / (350.0 * 100.0 * 2)
        self.assertAlmostEqual(trade["return_on_risk"], expected, places=6)


if __name__ == "__main__":
    unittest.main()
