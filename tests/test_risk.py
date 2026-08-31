import datetime
import json
import tempfile
import unittest
from pathlib import Path

from account.risk import (
    DEFAULT_OPTIONS_COST_RATIO_LIMIT,
    DEFAULT_RISK_LIMITS,
    bs_greeks,
    build_recommendations,
    calculate_option_position_greeks,
    classify_drawdown_status,
    classify_stress_status,
    compute_portfolio_stress_test,
    compute_twr_drawdown,
    delta_drift_trigger,
    load_options_cost_ratio_limit,
    score_label,
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


class OptionsCostRatioLimitTests(unittest.TestCase):
    def test_missing_file_returns_default_silently(self):
        missing = Path(tempfile.mkdtemp()) / "does_not_exist.json"
        self.assertEqual(
            load_options_cost_ratio_limit(missing), DEFAULT_OPTIONS_COST_RATIO_LIMIT
        )

    def test_valid_override_file_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio_config.json"
            path.write_text(json.dumps({"options_cost_ratio_limit": 0.35}), encoding="utf-8")
            self.assertEqual(load_options_cost_ratio_limit(path), 0.35)

    def test_malformed_file_falls_back_to_default_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio_config.json"
            path.write_text("{not valid json", encoding="utf-8")
            self.assertEqual(
                load_options_cost_ratio_limit(path), DEFAULT_OPTIONS_COST_RATIO_LIMIT
            )


class TwrDrawdownTests(unittest.TestCase):
    def test_fewer_than_two_observations_has_no_drawdown(self):
        self.assertEqual(compute_twr_drawdown({}), 0.0)
        self.assertEqual(compute_twr_drawdown({"2026-06-01": 100000}), 0.0)

    def test_pure_loss_registers_as_drawdown(self):
        nav = {"2026-06-01": 100000, "2026-06-02": 90000}
        self.assertAlmostEqual(compute_twr_drawdown(nav), 0.10, places=6)

    def test_partial_recovery_keeps_the_worse_drawdown(self):
        nav = {"2026-06-01": 100000, "2026-06-02": 90000, "2026-06-03": 99000}
        # -10% then +10% off the new base recovers most of the loss, but the
        # worst drawdown along the path (-10%) is what should be reported,
        # not the smaller current gap to the running peak.
        self.assertAlmostEqual(compute_twr_drawdown(nav), 0.10, places=6)

    def test_deposit_on_a_flat_day_is_not_counted_as_a_gain(self):
        # NAV rose by exactly the deposit amount -- zero real return, so this
        # must not register as a recovery/gain that resets the peak upward.
        nav = {"2026-06-01": 100000, "2026-06-02": 110000}
        cashflow = {"2026-06-02": 10000}
        self.assertAlmostEqual(compute_twr_drawdown(nav, cashflow), 0.0, places=6)

    def test_withdrawal_on_a_flat_day_is_not_counted_as_a_loss(self):
        nav = {"2026-06-01": 100000, "2026-06-02": 90000}
        cashflow = {"2026-06-02": -10000}
        self.assertAlmostEqual(compute_twr_drawdown(nav, cashflow), 0.0, places=6)


class PortfolioStressTestTests(unittest.TestCase):
    def test_stock_and_option_positions_aggregate_correctly(self):
        stocks = [{"symbol": "AAPL", "quantity": 100, "market_value": 15000.0}]
        options = [{
            "symbol": "AAPL260717C00150000",
            "quantity": 1,
            "current_price": 5.0,
            "market_value": 500.0,
            "strike": 150.0,
            "expiry": "2026-07-17",
        }]

        result = compute_portfolio_stress_test(
            stocks, options,
            underlying_prices={"AAPL": 150.0},
            iv_map={"AAPL": {"iv": 0.30, "src": "test"}},
            beta_map={"AAPL": 1.2},
            today=datetime.date(2026, 6, 17),
        )

        # Independently derived from the same Black-Scholes call this
        # function uses internally (dte=30, S=K=150, iv=0.30) -- this test
        # is exercising the position-loop assembly (signs, which total each
        # term feeds, per-position aggregation), not bs_greeks itself, which
        # test_bs_greeks_known_deterministic_case already locks down.
        greeks = bs_greeks(150.0, 150.0, 30 / 365, 0.30, "call")
        d, g, th, vg = greeks["delta"], greeks["gamma"], greeks["theta"], greeks["vega"]
        opt_delta_notl = abs(1 * 100 * d * 150.0)
        ds10, ds20 = -0.10 * 150.0, -0.20 * 150.0
        opt_pnl10 = 1 * 100 * (d * ds10 + 0.5 * g * ds10 * ds10) + 1 * 100 * vg * 8
        opt_pnl20 = 1 * 100 * (d * ds20 + 0.5 * g * ds20 * ds20) + 1 * 100 * vg * 16

        self.assertAlmostEqual(result["gross_notional"], 30000.0, places=2)
        self.assertAlmostEqual(result["delta_notional"], 15000.0 + opt_delta_notl, places=2)
        self.assertAlmostEqual(result["beta_delta"], 100 * 150 * 1.2 + 100 * d * 150.0 * 1.2, places=2)
        self.assertAlmostEqual(result["theta_per_day"], 100 * th, places=4)
        self.assertAlmostEqual(result["vega_per_pt"], 100 * vg, places=4)
        self.assertAlmostEqual(result["gamma_total"], 100 * g, places=4)
        self.assertAlmostEqual(result["stress_10"], 100 * (-0.10 * 150.0) + opt_pnl10, places=2)
        self.assertAlmostEqual(result["stress_20"], 100 * (-0.20 * 150.0) + opt_pnl20, places=2)
        self.assertEqual(result["nearest_expiry_date"], datetime.date(2026, 7, 17))
        self.assertEqual(result["nearest_expiry_sym"], "AAPL")

    def test_unknown_underlying_price_falls_back_to_market_value_for_delta_notional(self):
        options = [{
            "symbol": "ZZZZ260717C00100000",
            "quantity": 2,
            "current_price": 3.0,
            "market_value": 600.0,
            "strike": 100.0,
            "expiry": "2026-07-17",
        }]
        result = compute_portfolio_stress_test(
            [], options, underlying_prices={}, iv_map={}, beta_map={},
            today=datetime.date(2026, 6, 17),
        )
        # No spot price for ZZZZ -> can't price Greeks -> delta_notional and
        # gross both fall back to using market value / current_price rather
        # than silently under- or over-stating exposure as zero.
        self.assertAlmostEqual(result["delta_notional"], 600.0, places=2)
        self.assertAlmostEqual(result["gross_notional"], 2 * 100 * 3.0, places=2)
        self.assertEqual(result["stress_10"], 0.0)

    def test_empty_portfolio_returns_zeros(self):
        result = compute_portfolio_stress_test(
            [], [], underlying_prices={}, iv_map={}, beta_map={},
        )
        self.assertEqual(result["gross_notional"], 0.0)
        self.assertEqual(result["delta_notional"], 0.0)
        self.assertIsNone(result["nearest_expiry_date"])


class ScoreLabelTests(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(score_label(None), "")
        self.assertEqual(score_label(85), "85 ⭐")
        self.assertEqual(score_label(70), "70 ✅")
        self.assertEqual(score_label(55), "55 🟡")
        self.assertEqual(score_label(40), "40 ⚠️")
        self.assertEqual(score_label(10), "10 🔴")


class BuildRecommendationsTests(unittest.TestCase):
    def _portfolio(self, **overrides):
        base = {
            "underlying": "PLTR",
            "risk_level": "LOW",
            "type": "Bull Call Debit Spread",
            "recommendation": "持有至到期",
            "current_pnl": 0.0,
            "dte": 30,
            "pnl_pct": 20.0,
            "spread_qty": 1,
            "expiry": "2026-07-17",
            "max_profit": 1000.0,
            "max_loss": 500.0,
        }
        base.update(overrides)
        return base

    def _snapshot(self, **overrides):
        base = {"stress_10_ratio": 0.02, "leverage_delta": 1.0, "leverage": 1.0}
        base.update(overrides)
        return base

    def test_low_risk_high_profit_spread_suggests_locking_in_gains(self):
        recs = build_recommendations(
            portfolios=[self._portfolio(pnl_pct=60.0)],
            risk_snapshot=self._snapshot(),
            iv_regime={"status": "NORMAL"},
            ai_scores={},
        )
        self.assertEqual(len(recs), 1)
        self.assertIn("已盈利50%+", recs[0]["行动建议"])
        self.assertEqual(recs[0]["标的"], "PLTR")

    def test_low_risk_moderate_profit_suggests_holding(self):
        recs = build_recommendations(
            portfolios=[self._portfolio(pnl_pct=20.0)],
            risk_snapshot=self._snapshot(),
            iv_regime={"status": "NORMAL"},
            ai_scores={},
        )
        self.assertIn("建议继续持有", recs[0]["行动建议"])

    def test_stress_hard_stop_adds_deleverage_warning_to_every_position(self):
        recs = build_recommendations(
            portfolios=[self._portfolio()],
            risk_snapshot=self._snapshot(stress_10_ratio=0.16),
            iv_regime={"status": "NORMAL"},
            ai_scores={},
        )
        self.assertIn("组合压力超限", recs[0]["行动建议"])

    def test_stress_de_risk_appends_qqq_hedge_row(self):
        recs = build_recommendations(
            portfolios=[self._portfolio()],
            risk_snapshot=self._snapshot(stress_10_ratio=0.13),
            iv_regime={"status": "NORMAL"},
            ai_scores={},
        )
        hedge_rows = [r for r in recs if r["标的"] == "QQQ"]
        self.assertEqual(len(hedge_rows), 1)
        self.assertEqual(hedge_rows[0]["组合"], "宏观对冲（建议）")

    def test_no_hedge_row_when_stress_below_de_risk_threshold(self):
        recs = build_recommendations(
            portfolios=[self._portfolio()],
            risk_snapshot=self._snapshot(stress_10_ratio=0.05),
            iv_regime={"status": "NORMAL"},
            ai_scores={},
        )
        self.assertFalse(any(r["标的"] == "QQQ" for r in recs))

    def test_new_opportunity_candidates_exclude_held_underlyings_and_cap_at_three(self):
        ai_scores = {"PLTR": 90, "AAAA": 85, "BBBB": 80, "CCCC": 75, "DDDD": 71, "EEEE": 60}
        recs = build_recommendations(
            portfolios=[self._portfolio(underlying="PLTR")],  # PLTR already held
            risk_snapshot=self._snapshot(leverage_delta=0.5),  # well under headroom limit
            iv_regime={"status": "NORMAL"},
            ai_scores=ai_scores,
        )
        candidate_rows = [r for r in recs if r["组合"] == "新开仓候选"]
        self.assertEqual(len(candidate_rows), 3)
        self.assertNotIn("PLTR", [r["标的"] for r in candidate_rows])
        # Highest scores first, EEEE (60) excluded for being below the 70 cutoff
        self.assertEqual([r["标的"] for r in candidate_rows], ["AAAA", "BBBB", "CCCC"])

    def test_no_new_candidates_when_leverage_exceeds_headroom(self):
        recs = build_recommendations(
            portfolios=[],
            risk_snapshot=self._snapshot(leverage_delta=3.5),  # >= 4.0 * 0.75
            iv_regime={"status": "NORMAL"},
            ai_scores={"AAAA": 90},
        )
        self.assertFalse(any(r["组合"] == "新开仓候选" for r in recs))

    def test_risk_snapshot_error_suppresses_new_candidates(self):
        recs = build_recommendations(
            portfolios=[],
            risk_snapshot={"error": "no_equity"},
            iv_regime={"status": "NORMAL"},
            ai_scores={"AAAA": 90},
        )
        self.assertEqual(recs, [])

    def test_high_iv_favors_credit_spread_for_new_candidates(self):
        recs = build_recommendations(
            portfolios=[],
            risk_snapshot=self._snapshot(),
            iv_regime={"status": "HIGH_IV"},
            ai_scores={"AAAA": 90},
        )
        self.assertIn("Put Credit Spread", recs[0]["行动建议"])


class RiskStatusClassificationTests(unittest.TestCase):
    def test_stress_status_thresholds(self):
        self.assertEqual(classify_stress_status(0.05), "GREEN")
        self.assertEqual(classify_stress_status(0.08), "YELLOW_WARNING")
        self.assertEqual(classify_stress_status(0.12), "ORANGE_DE_RISK")
        self.assertEqual(classify_stress_status(0.15), "RED_HARD_STOP")
        self.assertEqual(classify_stress_status(None), "GREEN")

    def test_stress_status_uses_magnitude_not_sign(self):
        self.assertEqual(
            classify_stress_status(-0.16), classify_stress_status(0.16)
        )
        self.assertEqual(classify_stress_status(-0.16), "RED_HARD_STOP")

    def test_drawdown_status_thresholds(self):
        self.assertEqual(classify_drawdown_status(0.10), "GREEN")
        self.assertEqual(classify_drawdown_status(0.20), "ORANGE_FREEZE_NEW_RISK")
        self.assertEqual(classify_drawdown_status(0.30), "RED_MANDATORY_DE_RISK")

    def test_custom_limits_override_defaults(self):
        tight = {**DEFAULT_RISK_LIMITS, "stress_warning": 0.01, "stress_de_risk": 0.02,
                 "stress_hard_stop": 0.03}
        self.assertEqual(classify_stress_status(0.015, tight), "YELLOW_WARNING")


if __name__ == "__main__":
    unittest.main()
