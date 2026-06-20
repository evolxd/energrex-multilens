import datetime
import unittest

from account.risk import (
    bs_greeks,
    calculate_option_position_greeks,
    delta_drift_trigger,
    summarize_portfolio_greeks,
    vix_spike_trigger,
)


class RiskTests(unittest.TestCase):
    def test_bs_greeks_known_deterministic_case(self):
        greeks = bs_greeks(100, 100, 30 / 365, 0.30, "call")

        self.assertAlmostEqual(greeks["delta"], 0.5343, places=4)
        self.assertAlmostEqual(greeks["gamma"], 0.046213, places=6)
        self.assertAlmostEqual(greeks["theta"], -0.0631, places=4)
        self.assertAlmostEqual(greeks["vega"], 0.1140, places=4)

    def test_calculate_option_position_greeks(self):
        row = calculate_option_position_greeks(
            symbol="TEST260717C00100000",
            underlying="TEST",
            option_type="call",
            quantity=2,
            strike=100,
            expiry="2026-07-17",
            spot_price=105,
            current_price=6,
            iv=0.40,
            iv_source="test",
            today=datetime.date(2026, 6, 18),
        )

        self.assertEqual(row["dte"], 29)
        self.assertEqual(row["qty"], 2)
        self.assertEqual(row["iv_src"], "test")
        self.assertFalse(row["high_gamma"])
        self.assertAlmostEqual(row["pos_delta"], 1.3975, places=4)
        self.assertAlmostEqual(row["pos_theta"], -15.84, places=2)

    def test_summarize_portfolio_greeks_cleans_internal_raw(self):
        rows = [
            calculate_option_position_greeks(
                symbol="AAA260717C00100000",
                underlying="AAA",
                option_type="call",
                quantity=2,
                strike=100,
                expiry="2026-07-17",
                spot_price=105,
                current_price=6,
                iv=0.40,
                iv_source="test",
                today=datetime.date(2026, 6, 18),
            ),
            calculate_option_position_greeks(
                symbol="BBB260717P00050000",
                underlying="BBB",
                option_type="put",
                quantity=-1,
                strike=50,
                expiry="2026-07-17",
                spot_price=48,
                current_price=3,
                iv=0.50,
                iv_source="db",
                today=datetime.date(2026, 6, 18),
            ),
        ]

        summary = summarize_portfolio_greeks(rows)

        self.assertEqual(summary["n_contracts"], 3)
        self.assertAlmostEqual(summary["totals"]["delta"], 1.9743, places=4)
        self.assertEqual(summary["top_long"], "AAA")
        self.assertEqual(summary["top_short"], "BBB")
        self.assertEqual(summary["iv_src_counts"], {"test": 1, "db": 1})
        self.assertNotIn("_raw", summary["rows"][0])

    def test_delta_drift_trigger(self):
        trigger = delta_drift_trigger(1.0, 10, 0.25)
        quiet = delta_drift_trigger(1.0, 10, 0.15)

        self.assertEqual(trigger["level"], "HIGH")
        self.assertAlmostEqual(trigger["drift"], 0.15)
        self.assertIsNone(quiet)

    def test_vix_spike_trigger(self):
        trigger = vix_spike_trigger({"vix": 25.1, "change_pct": 16})

        self.assertEqual(trigger["level"], "CRITICAL")
        self.assertEqual(trigger["change_pct"], 16.0)
        self.assertIsNone(vix_spike_trigger({"vix": 20, "change_pct": 5}))
        self.assertIsNone(vix_spike_trigger({"vix": None, "change_pct": None}))


if __name__ == "__main__":
    unittest.main()
