import unittest

from account.fifo import calculate_fifo_matches


class FifoTests(unittest.TestCase):
    def test_long_option_buy_to_open_sell_to_close(self):
        rows = [
            {
                "trade_date": "2026-01-01",
                "type": "BUY TO OPEN",
                "symbol": "PLTR260717C00150000",
                "quantity": 1,
                "price": 3.0,
                "amount": -300.0,
            },
            {
                "trade_date": "2026-01-10",
                "type": "SELL TO CLOSE",
                "symbol": "PLTR260717C00150000",
                "quantity": 1,
                "price": 5.0,
                "amount": 500.0,
            },
        ]

        result = calculate_fifo_matches(rows)

        self.assertEqual(result["summary"]["realized_count"], 1)
        self.assertEqual(result["summary"]["open_lots"], 0)
        self.assertEqual(result["summary"]["total_realized_pnl"], 200.0)
        trade = result["realized"][0]
        self.assertEqual(trade["underlying"], "PLTR")
        self.assertEqual(trade["strategy_type"], "long_call")
        self.assertEqual(trade["option_type"], "call")
        self.assertEqual(trade["win_loss"], "win")
        self.assertEqual(trade["holding_days"], 9)

    def test_short_option_sell_to_open_buy_to_close(self):
        rows = [
            {
                "trade_date": "2026-02-01",
                "type": "SELL TO OPEN",
                "symbol": "QQQ260717P00350000",
                "quantity": 1,
                "price": 2.0,
                "amount": 200.0,
            },
            {
                "trade_date": "2026-02-05",
                "type": "BUY TO CLOSE",
                "symbol": "QQQ260717P00350000",
                "quantity": 1,
                "price": 0.5,
                "amount": -50.0,
            },
        ]

        result = calculate_fifo_matches(rows)

        self.assertEqual(result["summary"]["realized_count"], 1)
        self.assertEqual(result["summary"]["total_realized_pnl"], 150.0)
        trade = result["realized"][0]
        self.assertEqual(trade["lot_direction"], "short")
        self.assertEqual(trade["strategy_type"], "short_put")
        self.assertEqual(trade["option_type"], "put")
        self.assertEqual(trade["win_loss"], "win")

    def test_open_lot_fifo_cost_remains_when_not_closed(self):
        rows = [
            {
                "trade_date": "2026-03-01",
                "type": "BUY TO OPEN",
                "symbol": "META260717C00200000",
                "quantity": 1,
                "price": 4.0,
                "amount": -400.0,
            },
            {
                "trade_date": "2026-03-02",
                "type": "BUY TO OPEN",
                "symbol": "META260717C00200000",
                "quantity": 1,
                "price": 6.0,
                "amount": -600.0,
            },
        ]

        result = calculate_fifo_matches(rows)

        self.assertEqual(result["summary"]["realized_count"], 0)
        self.assertEqual(result["summary"]["open_lots"], 1)
        self.assertEqual(result["fifo_costs"]["META260717C00200000"], 5.0)


if __name__ == "__main__":
    unittest.main()
