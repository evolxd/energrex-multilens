import datetime
import json
import math
import tempfile
import unittest
from pathlib import Path

from account.risk import (
    DEFAULT_OPTIONS_COST_RATIO_LIMIT,
    DEFAULT_RISK_LIMITS,
    bs_greeks,
    bs_price,
    build_recommendations,
    calculate_option_position_greeks,
    check_otm_spread_alerts,
    classify_drawdown_status,
    classify_stress_status,
    compute_exit_analysis,
    compute_portfolio_stress_test,
    compute_index_hedge_plan,
    compute_qqq_hedge_plan,
    compute_sim_impact,
    compute_twr_drawdown,
    delta_drift_trigger,
    load_options_cost_ratio_limit,
    new_opportunity_candidates,
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

    def test_bs_price_satisfies_put_call_parity(self):
        """C - P = S - K*e^(-rT) holds for any correct European BS pricer --
        an independent check that doesn't re-derive the same formula being
        tested (unlike comparing against a hand-copied expected number)."""
        from account.risk import RF_RATE, bs_price
        S, K, T, sigma = 142.0, 150.0, 45 / 365, 0.35
        call = bs_price(S, K, T, sigma, "call")
        put  = bs_price(S, K, T, sigma, "put")
        self.assertAlmostEqual(call - put, S - K * math.exp(-RF_RATE * T), places=6)

    def test_bs_price_deep_itm_call_approaches_intrinsic_value(self):
        from account.risk import bs_price
        price = bs_price(200, 100, 30 / 365, 0.20, "call")
        # Deep ITM with little time value left: close to but not below intrinsic.
        self.assertGreaterEqual(price, 100.0)
        self.assertAlmostEqual(price, 100.0, delta=2.0)

    def test_bs_price_zero_time_falls_back_to_intrinsic(self):
        from account.risk import bs_price
        self.assertEqual(bs_price(120, 100, 0, 0.30, "call"), 20.0)
        self.assertEqual(bs_price(80, 100, 0, 0.30, "put"), 20.0)

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
        # term feeds, per-position aggregation), not bs_greeks/bs_price
        # themselves, which their own dedicated tests already lock down.
        greeks = bs_greeks(150.0, 150.0, 30 / 365, 0.30, "call")
        d, g, th, vg = greeks["delta"], greeks["gamma"], greeks["theta"], greeks["vega"]
        opt_delta_notl = abs(1 * 100 * d * 150.0)

        # F-01 (2026-09-10): stress uses beta-scaled shocks (beta 1.2 ->
        # underlying itself moves 1.2x the index move) and full BS
        # repricing, not a delta/gamma Taylor expansion.
        price_now = bs_price(150.0, 150.0, 30 / 365, 0.30, "call")
        s10 = 150.0 * (1 + 1.2 * -0.10)
        s20 = 150.0 * (1 + 1.2 * -0.20)
        opt_pnl10 = 1 * 100 * (bs_price(s10, 150.0, 30 / 365, 0.30 + 0.08, "call") - price_now)
        opt_pnl20 = 1 * 100 * (bs_price(s20, 150.0, 30 / 365, 0.30 + 0.16, "call") - price_now)

        self.assertAlmostEqual(result["gross_notional"], 30000.0, places=2)
        self.assertAlmostEqual(result["delta_notional"], 15000.0 + opt_delta_notl, places=2)
        self.assertAlmostEqual(result["beta_delta"], 100 * 150 * 1.2 + 100 * d * 150.0 * 1.2, places=2)
        self.assertAlmostEqual(result["theta_per_day"], 100 * th, places=4)
        self.assertAlmostEqual(result["vega_per_pt"], 100 * vg, places=4)
        self.assertAlmostEqual(result["gamma_total"], 100 * g, places=4)
        self.assertAlmostEqual(result["stress_10"], 100 * (1.2 * -0.10 * 150.0) + opt_pnl10, places=2)
        self.assertAlmostEqual(result["stress_20"], 100 * (1.2 * -0.20 * 150.0) + opt_pnl20, places=2)
        self.assertEqual(result["nearest_expiry_date"], datetime.date(2026, 7, 17))
        self.assertEqual(result["nearest_expiry_sym"], "AAPL")

    def test_stress_scales_with_beta_not_just_raw_index_shock(self):
        """审计 F-01 的核心断言：同样是"指数跌10%"，beta=2的股票自己跌的
        钱应该是 beta=1 时的两倍——不是不管beta都按同样10%算。"""
        stocks_hi_beta  = [{"symbol": "NVDA", "quantity": 100, "market_value": 15000.0}]
        stocks_lo_beta  = [{"symbol": "KO",   "quantity": 100, "market_value": 15000.0}]
        hi = compute_portfolio_stress_test(
            stocks_hi_beta, [], underlying_prices={}, iv_map={},
            beta_map={"NVDA": 2.0})
        lo = compute_portfolio_stress_test(
            stocks_lo_beta, [], underlying_prices={}, iv_map={},
            beta_map={"KO": 1.0})
        self.assertAlmostEqual(hi["stress_10"], 2.0 * lo["stress_10"], places=6)

    def test_option_stress_uses_full_repricing_not_taylor_expansion(self):
        """深度虚值期权是泰勒展开在大幅冲击下失真最严重的地方——gamma
        在冲击后已经变了，展开却还在用冲击前的 gamma。全额重新定价没有
        这个偏差，价格差本身就是这个情景下真实的盈亏。"""
        options = [{
            "symbol": "TEST260717P00080000", "quantity": -1,
            "current_price": 1.0, "market_value": -100.0,
            "strike": 80.0, "expiry": "2026-07-17",
        }]
        result = compute_portfolio_stress_test(
            [], options, underlying_prices={"TEST": 150.0},
            iv_map={"TEST": {"iv": 0.40}}, beta_map={"TEST": 1.0},
            today=datetime.date(2026, 6, 17),
        )
        T = 30 / 365
        price_now = bs_price(150.0, 80.0, T, 0.40, "put")
        price_20  = bs_price(150.0 * 0.80, 80.0, T, 0.40 + 0.16, "put")
        expected_stress_20 = -1 * 100 * (price_20 - price_now)
        self.assertAlmostEqual(result["stress_20"], expected_stress_20, places=2)

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

    def test_new_opportunity_candidates_is_directly_callable(self):
        """2026-09-10：抽出来给 pre_trade_check.py（门④）单独调用，不用再
        经过 build_recommendations() 混进持仓管理那堆建议里。"""
        out = new_opportunity_candidates(
            risk_snapshot=self._snapshot(leverage_delta=0.5),
            iv_regime={"status": "NORMAL"},
            ai_scores={"NVDA": 88, "PLTR": 72},
            held_underlyings=set(),
        )
        self.assertEqual([r["标的"] for r in out], ["NVDA", "PLTR"])
        self.assertEqual(out[0]["序号"], 1)  # start_idx 默认从1开始

    def test_new_opportunity_candidates_start_idx_continues_numbering(self):
        out = new_opportunity_candidates(
            risk_snapshot=self._snapshot(leverage_delta=0.5),
            iv_regime={"status": "NORMAL"},
            ai_scores={"NVDA": 88},
            held_underlyings=set(),
            start_idx=5,
        )
        self.assertEqual(out[0]["序号"], 5)


class ComputeExitAnalysisTests(unittest.TestCase):
    def _portfolio(self, **overrides):
        base = {
            "underlying": "PLTR",
            "type": "Bull Call Debit Spread",
            "max_loss": 1000.0,
            "current_pnl": 100.0,
            "pnl_pct": None,
            "dte": 30,
            "legs": [
                {"qty": 1, "dte": 30, "strike": 100},
                {"qty": -1, "dte": 30, "strike": 110},
            ],
            "high_strike": 110,
            "low_strike": 100,
        }
        base.update(overrides)
        return base

    def test_empty_portfolios_returns_empty_result(self):
        result = compute_exit_analysis([], net_equity=100000, underlying_prices={})
        self.assertEqual(result, {"portfolios": [], "summary": {}})

    def test_missing_pnl_pct_is_filled_in_from_cost_basis(self):
        result = compute_exit_analysis(
            [self._portfolio(max_loss=1000.0, current_pnl=250.0, pnl_pct=None)],
            net_equity=100000, underlying_prices={},
        )
        self.assertAlmostEqual(result["portfolios"][0]["pnl_pct"], 25.0)

    def test_equity_pct_uses_cost_basis_over_net_equity(self):
        result = compute_exit_analysis(
            [self._portfolio(max_loss=20000.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertAlmostEqual(result["portfolios"][0]["equity_pct"], 20.0)

    def test_bull_call_thesis_broken_when_price_drops_below_low_strike(self):
        result = compute_exit_analysis(
            [self._portfolio(type="Bull Call Debit Spread", low_strike=100, high_strike=110)],
            net_equity=100000, underlying_prices={"PLTR": 90.0},
        )
        row = result["portfolios"][0]
        self.assertTrue(row["thesis_broken"])
        self.assertIn("看涨假设受挫", row["thesis_note"])

    def test_bull_call_thesis_intact_when_price_holds_above_low_strike(self):
        result = compute_exit_analysis(
            [self._portfolio(type="Bull Call Debit Spread", low_strike=100, high_strike=110)],
            net_equity=100000, underlying_prices={"PLTR": 105.0},
        )
        self.assertFalse(result["portfolios"][0]["thesis_broken"])

    def test_near_expiry_forces_immediate_action_regardless_of_pnl(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=5, pnl_pct=10.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["action"], "🚨 立即处理")

    def test_extreme_loss_forces_stop_loss_regardless_of_dte(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=-65.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["action"], "🛑 止损")

    def test_large_gain_triggers_take_profit(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=55.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["action"], "⚡ 止盈")

    def test_healthy_position_defaults_to_hold(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=5.0)],
            net_equity=100000, underlying_prices={},
        )
        row = result["portfolios"][0]
        self.assertEqual(row["action"], "✅ 持有")
        self.assertIn("当前安全区间", row["why"])

    def test_results_sorted_by_urgency_descending(self):
        calm = self._portfolio(underlying="AAA", dte=60, pnl_pct=5.0)
        urgent = self._portfolio(underlying="BBB", dte=3, pnl_pct=-70.0)
        result = compute_exit_analysis(
            [calm, urgent], net_equity=100000, underlying_prices={},
        )
        self.assertEqual([p["underlying"] for p in result["portfolios"]], ["BBB", "AAA"])

    def test_summary_aggregates_cost_and_broken_thesis_count(self):
        result = compute_exit_analysis(
            [
                self._portfolio(underlying="AAA", max_loss=1000.0),
                self._portfolio(underlying="BBB", max_loss=2000.0,
                                 type="Bull Call Debit Spread", low_strike=100, high_strike=110),
            ],
            net_equity=100000,
            underlying_prices={"BBB": 50.0},  # breaks BBB's bull call thesis
        )
        summary = result["summary"]
        self.assertAlmostEqual(summary["total_cost"], 3000.0)
        self.assertAlmostEqual(summary["cost_pct"], 3.0)
        self.assertEqual(summary["n_broken"], 1)
        self.assertEqual(summary["top_unds"][0], ("BBB", 2000.0))

    # ════════════════════════════════════════════════════════════════
    # Golden/characterization tests added ahead of decomposing
    # compute_exit_analysis into detect_thesis_broken / compute_urgency_score
    # / pick_action / build_why_text / summarize_exit_portfolios.
    #
    # Written against the CURRENT, unmodified implementation -- fill the
    # coverage gaps the pre-decomposition audit found: 5 of 6 thesis-broken
    # spread types, the exact urgency-score weights per factor, 5 of 9
    # action branches, and the 4 layered why-text sections. Also locks the
    # 3 discovered quirks as-is (approved: preserve, do not fix):
    #   - `today` is accepted but never read anywhere in the function.
    #   - Naked Long Put/Call index into legs[0] without a length check.
    #   - `(min_dte or 999) < 30` -- verified NOT actually reachable with a
    #     falsy min_dte=0 in practice, because `min_dte <= 7` (which 0
    #     always satisfies) is checked in an earlier, higher-priority
    #     branch and wins first. Corrected from the earlier diagnosis,
    #     which called this a live trap; it is not one under the current
    #     elif ordering. Locked as dead/unreachable defensive code, not a
    #     bug -- if a later refactor ever reorders the elif chain, this is
    #     exactly the kind of thing that could turn live, so it stays
    #     covered rather than removed.
    # ════════════════════════════════════════════════════════════════

    def test_today_parameter_has_no_effect_on_the_result(self):
        kwargs = dict(
            portfolios=[self._portfolio()], net_equity=100000, underlying_prices={},
        )
        self.assertEqual(
            compute_exit_analysis(**kwargs, today=None),
            compute_exit_analysis(**kwargs, today=datetime.date(2030, 1, 1)),
        )

    # ── B组：thesis_broken，5种剩余价差类型（Bull Call 已有覆盖）────────

    def test_bear_put_thesis_broken_when_price_rises_above_high_strike(self):
        result = compute_exit_analysis(
            [self._portfolio(type="Bear Put Spread", high_strike=110, low_strike=100)],
            net_equity=100000, underlying_prices={"PLTR": 120.0},
        )
        row = result["portfolios"][0]
        self.assertTrue(row["thesis_broken"])
        self.assertIn("看跌假设已被推翻", row["thesis_note"])

    def test_bear_put_thesis_intact_when_price_holds_below_high_strike(self):
        result = compute_exit_analysis(
            [self._portfolio(type="Bear Put Spread", high_strike=110, low_strike=100)],
            net_equity=100000, underlying_prices={"PLTR": 105.0},
        )
        self.assertFalse(result["portfolios"][0]["thesis_broken"])

    def test_bear_call_thesis_broken_when_price_rises_above_high_strike(self):
        result = compute_exit_analysis(
            [self._portfolio(type="Bear Call Credit Spread", high_strike=110, low_strike=100)],
            net_equity=100000, underlying_prices={"PLTR": 120.0},
        )
        row = result["portfolios"][0]
        self.assertTrue(row["thesis_broken"])
        self.assertIn("空头承压", row["thesis_note"])

    def test_bear_call_thesis_intact_when_price_holds_below_high_strike(self):
        result = compute_exit_analysis(
            [self._portfolio(type="Bear Call Credit Spread", high_strike=110, low_strike=100)],
            net_equity=100000, underlying_prices={"PLTR": 105.0},
        )
        self.assertFalse(result["portfolios"][0]["thesis_broken"])

    def test_bull_put_thesis_broken_when_price_drops_below_low_strike(self):
        result = compute_exit_analysis(
            [self._portfolio(type="Bull Put Credit Spread", high_strike=110, low_strike=100)],
            net_equity=100000, underlying_prices={"PLTR": 90.0},
        )
        row = result["portfolios"][0]
        self.assertTrue(row["thesis_broken"])
        self.assertIn("多头压力加大", row["thesis_note"])

    def test_bull_put_thesis_intact_when_price_holds_above_low_strike(self):
        result = compute_exit_analysis(
            [self._portfolio(type="Bull Put Credit Spread", high_strike=110, low_strike=100)],
            net_equity=100000, underlying_prices={"PLTR": 105.0},
        )
        self.assertFalse(result["portfolios"][0]["thesis_broken"])

    def test_naked_long_put_thesis_broken_when_price_rises_10pct_above_strike(self):
        result = compute_exit_analysis(
            [self._portfolio(type="Naked Long Put", legs=[{"qty": 1, "dte": 30, "strike": 100}])],
            net_equity=100000, underlying_prices={"PLTR": 115.0},  # > 100*1.1
        )
        row = result["portfolios"][0]
        self.assertTrue(row["thesis_broken"])
        self.assertIn("看跌假设未兑现", row["thesis_note"])

    def test_naked_long_put_thesis_intact_within_10pct_band(self):
        result = compute_exit_analysis(
            [self._portfolio(type="Naked Long Put", legs=[{"qty": 1, "dte": 30, "strike": 100}])],
            net_equity=100000, underlying_prices={"PLTR": 105.0},  # < 100*1.1
        )
        self.assertFalse(result["portfolios"][0]["thesis_broken"])

    def test_naked_long_call_thesis_broken_when_price_falls_10pct_below_strike(self):
        result = compute_exit_analysis(
            [self._portfolio(type="Naked Long Call", legs=[{"qty": 1, "dte": 30, "strike": 100}])],
            net_equity=100000, underlying_prices={"PLTR": 85.0},  # < 100*0.9
        )
        row = result["portfolios"][0]
        self.assertTrue(row["thesis_broken"])
        self.assertIn("看涨动能不足", row["thesis_note"])

    def test_naked_long_call_thesis_intact_within_10pct_band(self):
        result = compute_exit_analysis(
            [self._portfolio(type="Naked Long Call", legs=[{"qty": 1, "dte": 30, "strike": 100}])],
            net_equity=100000, underlying_prices={"PLTR": 95.0},  # > 100*0.9
        )
        self.assertFalse(result["portfolios"][0]["thesis_broken"])

    def test_unrecognized_spread_type_never_reports_thesis_broken(self):
        result = compute_exit_analysis(
            [self._portfolio(type="Iron Condor", high_strike=110, low_strike=100)],
            net_equity=100000, underlying_prices={"PLTR": 999.0},
        )
        self.assertFalse(result["portfolios"][0]["thesis_broken"])

    # ── C组：紧急度打分权重（每个因子单独隔离验证）──────────────────
    # equity_pct 固定用 max_loss=1000/net_equity=100000 -> equity_pct=1.0，
    # 不会触碰任何 equity_pct 档位，从而把其它三个因子隔离开单独测。

    def test_urgency_dte_tier_le_7_adds_five(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=5, pnl_pct=0.0, max_loss=1000.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["urgency"], 5)

    def test_urgency_dte_tier_le_14_adds_three(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=10, pnl_pct=0.0, max_loss=1000.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["urgency"], 3)

    def test_urgency_dte_tier_le_21_adds_one(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=18, pnl_pct=0.0, max_loss=1000.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["urgency"], 1)

    def test_urgency_dte_beyond_21_adds_nothing(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=25, pnl_pct=0.0, max_loss=1000.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["urgency"], 0)

    def test_urgency_pnl_tier_le_minus_60_adds_six(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=-65.0, max_loss=1000.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["urgency"], 6)

    def test_urgency_pnl_tier_le_minus_45_adds_four(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=-50.0, max_loss=1000.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["urgency"], 4)

    def test_urgency_pnl_tier_le_minus_30_adds_two(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=-35.0, max_loss=1000.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["urgency"], 2)

    def test_urgency_pnl_tier_ge_45_adds_two(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=50.0, max_loss=1000.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["urgency"], 2)

    def test_urgency_pnl_tier_ge_30_adds_one(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=35.0, max_loss=1000.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["urgency"], 1)

    def test_urgency_equity_pct_over_30_adds_three(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=0.0, max_loss=35000.0)],  # equity_pct=35
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["urgency"], 3)

    def test_urgency_equity_pct_over_20_adds_one(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=0.0, max_loss=25000.0)],  # equity_pct=25
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["urgency"], 1)

    def test_urgency_thesis_broken_adds_four(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=0.0, max_loss=1000.0,
                              type="Bull Call Debit Spread", low_strike=100, high_strike=110)],
            net_equity=100000, underlying_prices={"PLTR": 90.0},  # breaks bull call thesis
        )
        self.assertEqual(result["portfolios"][0]["urgency"], 4)

    def test_urgency_factors_are_additive_not_mutually_exclusive(self):
        """dte<=7 (+5) + pnl<=-60 (+6) + equity_pct>30 (+3) + thesis_broken
        (+4) must sum to 18 -- proving these four checks are independent
        accumulators, not an elif chain like the action-picking logic."""
        result = compute_exit_analysis(
            [self._portfolio(dte=5, pnl_pct=-65.0, max_loss=35000.0,
                              type="Bull Call Debit Spread", low_strike=100, high_strike=110)],
            net_equity=100000, underlying_prices={"PLTR": 90.0},
        )
        self.assertEqual(result["portfolios"][0]["urgency"], 18)

    # ── D组：剩余 5 种 action 分支 + 优先级互斥验证 ──────────────────

    def test_action_reassess_when_thesis_broken_and_loss_at_least_20pct(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=-25.0,
                              type="Bull Call Debit Spread", low_strike=100, high_strike=110)],
            net_equity=100000, underlying_prices={"PLTR": 90.0},
        )
        self.assertEqual(result["portfolios"][0]["action"], "📉 重新评估")

    def test_action_second_stop_loss_branch_pnl_le_minus_50_and_dte_under_30(self):
        """Distinct from the `pnl<=-60` stop-loss branch above it: this one
        fires at the shallower -50% threshold, but only when DTE < 30."""
        result = compute_exit_analysis(
            [self._portfolio(dte=25, pnl_pct=-55.0)],  # >-60 so the first stop-loss branch doesn't fire
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["action"], "🛑 止损")

    def test_action_second_stop_loss_branch_does_not_fire_when_dte_is_30_or_more(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=30, pnl_pct=-55.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertNotEqual(result["portfolios"][0]["action"], "🛑 止损")

    def test_action_roll_when_short_leg_expires_within_21_days(self):
        result = compute_exit_analysis(
            [self._portfolio(
                dte=30, pnl_pct=0.0,
                legs=[{"qty": 1, "dte": 30, "strike": 100}, {"qty": -1, "dte": 18, "strike": 110}],
            )],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["action"], "🔄 滚仓")

    def test_action_direction_reversed_when_thesis_broken_without_a_big_loss(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=5.0,
                              type="Bull Call Debit Spread", low_strike=100, high_strike=110)],
            net_equity=100000, underlying_prices={"PLTR": 90.0},
        )
        self.assertEqual(result["portfolios"][0]["action"], "⚠️ 方向反转")

    def test_action_watch_when_loss_exceeds_40pct_without_other_triggers(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=-42.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["action"], "👀 关注")

    def test_action_roll_takes_priority_over_direction_reversed(self):
        """Both the roll condition (short leg DTE<=21) and thesis_broken are
        true here -- roll is checked earlier in the elif chain and must
        win, proving the priority order survives extraction."""
        result = compute_exit_analysis(
            [self._portfolio(
                dte=30, pnl_pct=0.0,
                type="Bull Call Debit Spread", low_strike=100, high_strike=110,
                legs=[{"qty": 1, "dte": 30, "strike": 100}, {"qty": -1, "dte": 18, "strike": 110}],
            )],
            net_equity=100000, underlying_prices={"PLTR": 90.0},  # also breaks thesis
        )
        self.assertEqual(result["portfolios"][0]["action"], "🔄 滚仓")

    def test_min_dte_zero_hits_immediate_action_not_the_falsy_zero_branch(self):
        """Locks the corrected understanding of the `(min_dte or 999) < 30`
        line: with min_dte=0, the earlier `min_dte <= 7` branch always
        wins first (0 <= 7), so this scenario can never actually reach
        the `or 999` fallback in practice -- even with a deep loss that
        would otherwise hit the second stop-loss branch."""
        result = compute_exit_analysis(
            [self._portfolio(dte=0, pnl_pct=-55.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["action"], "🚨 立即处理")

    # ── E组：why 文案的 4 段分层逻辑 ──────────────────────────────────

    def test_why_includes_direction_note_when_thesis_broken(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=5.0,
                              type="Bull Call Debit Spread", low_strike=100, high_strike=110)],
            net_equity=100000, underlying_prices={"PLTR": 90.0},
        )
        why = result["portfolios"][0]["why"]
        self.assertIn("【方向】", why)
        self.assertIn("看涨假设受挫", why)

    def test_why_take_profit_triggered_message(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=55.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertIn("已触发止盈线 +50%", result["portfolios"][0]["why"])

    def test_why_stop_loss_triggered_message(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=-65.0)],
            net_equity=100000, underlying_prices={},
        )
        why = result["portfolios"][0]["why"]
        self.assertIn("已触发止损线", why)
        self.assertIn("65", why)

    def test_why_warns_when_close_to_stop_loss_line(self):
        # dist_stop = pnl - (-50) = -40 - (-50) = 10 < 15, and pnl > -50 so
        # the "already triggered" branch above it doesn't fire first.
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=-40.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertIn("距止损线 -50% 仅剩", result["portfolios"][0]["why"])

    def test_why_notes_progress_toward_take_profit_line(self):
        # dist_tp = 50 - 42 = 8 < 12, and pnl < 50 so it doesn't hit the
        # "already triggered" branch first.
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=42.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertIn("距止盈线 +50% 还差", result["portfolios"][0]["why"])

    def test_why_risk_concentration_warning_above_25pct(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=0.0, max_loss=30000.0)],  # equity_pct=30
            net_equity=100000, underlying_prices={},
        )
        self.assertIn("集中度偏高", result["portfolios"][0]["why"])

    def test_why_risk_note_between_15_and_25pct_without_concentration_warning(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=0.0, max_loss=18000.0)],  # equity_pct=18
            net_equity=100000, underlying_prices={},
        )
        why = result["portfolios"][0]["why"]
        self.assertIn("【风险】", why)
        self.assertNotIn("集中度偏高", why)

    def test_why_no_risk_note_at_or_below_15pct(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=60, pnl_pct=0.0, max_loss=10000.0)],  # equity_pct=10
            net_equity=100000, underlying_prices={},
        )
        self.assertNotIn("【风险】", result["portfolios"][0]["why"])

    def test_why_time_pressure_tiers(self):
        cases = [
            (5, "立即决策"),
            (10, "本周决策"),
            (18, "2周内决策"),
        ]
        for dte, expected_phrase in cases:
            with self.subTest(dte=dte):
                result = compute_exit_analysis(
                    [self._portfolio(dte=dte, pnl_pct=0.0, max_loss=1000.0)],
                    net_equity=100000, underlying_prices={},
                )
                self.assertIn(expected_phrase, result["portfolios"][0]["why"])

    def test_why_no_time_note_beyond_21_days(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=25, pnl_pct=0.0, max_loss=1000.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertNotIn("【时间】", result["portfolios"][0]["why"])

    def test_why_defaults_to_all_clear_when_nothing_applies(self):
        result = compute_exit_analysis(
            [self._portfolio(dte=100, pnl_pct=None, max_loss=0.0, current_pnl=0.0)],
            net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["why"], "各项指标正常，无需立即行动")

    # ── A组边界：net_equity<=0、cost_basis 从 net_total 兜底 ──────────

    def test_zero_net_equity_does_not_divide_by_zero(self):
        result = compute_exit_analysis(
            [self._portfolio(max_loss=1000.0)],
            net_equity=0, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["equity_pct"], 0.0)
        self.assertEqual(result["summary"]["cost_pct"], 0.0)

    def test_cost_basis_falls_back_to_net_total_when_max_loss_absent(self):
        portfolio = self._portfolio(max_loss=None, **{"net_total": 750.0})
        result = compute_exit_analysis(
            [portfolio], net_equity=100000, underlying_prices={},
        )
        self.assertEqual(result["portfolios"][0]["cost_basis"], 750.0)


class ComputeQqqHedgePlanTests(unittest.TestCase):
    def test_bd_to_hedge_and_reference_strike_with_no_existing_legs(self):
        result = compute_qqq_hedge_plan(
            equity=500000, current_bd=900000, current_bdr=1.8, target_bd_ratio=1.50,
            qqq_price=500.0, qqq_iv_pct=20.0, beta_qqq=1.31,
            existing_legs=[], existing_bd=0.0, n_existing=0,
            current_option_cost=10000.0, today=datetime.date(2026, 6, 1),
        )
        self.assertAlmostEqual(result["bd_to_hedge"], 150000.0)
        # No existing long puts -> reference strike falls back to 97% of spot,
        # rounded to the nearest $5.
        self.assertAlmostEqual(result["plan_a"]["buy_strike"], 485.0)
        self.assertAlmostEqual(result["plan_a"]["sell_strike"], 450.0)
        self.assertAlmostEqual(result["plan_b"]["sell_strike"], 425.0)
        self.assertEqual(result["plan_exp_str"], "2026-08-30")

    def test_no_hedge_needed_when_already_under_target(self):
        result = compute_qqq_hedge_plan(
            equity=500000, current_bd=600000, current_bdr=1.2, target_bd_ratio=1.50,
            qqq_price=500.0, qqq_iv_pct=20.0, beta_qqq=1.31,
            existing_legs=[], existing_bd=0.0, n_existing=0,
            current_option_cost=0.0, today=datetime.date(2026, 6, 1),
        )
        self.assertLess(result["bd_to_hedge"], 0)
        self.assertEqual(result["plan_a"]["n_total"], 0)
        self.assertEqual(result["plan_b"]["n_total"], 0)

    def test_existing_long_put_strike_becomes_the_reference_strike(self):
        legs = [{
            "sym": "QQQ260830P00470000", "qty": 2, "type": "P",
            "strike": 470.0, "expiry": "2026-08-30",
            "delta": -0.3, "price": 10.0, "market_value": 2000.0,
        }]
        result = compute_qqq_hedge_plan(
            equity=500000, current_bd=900000, current_bdr=1.8, target_bd_ratio=1.50,
            qqq_price=500.0, qqq_iv_pct=20.0, beta_qqq=1.31,
            existing_legs=legs, existing_bd=-50000.0, n_existing=2,
            current_option_cost=2000.0, today=datetime.date(2026, 6, 1),
        )
        self.assertAlmostEqual(result["plan_a"]["buy_strike"], 470.0)
        self.assertEqual(result["plan_c"]["n_existing"], 2)
        self.assertAlmostEqual(result["existing_bd"], -50000.0)

    def test_plan_c_reduces_additional_contracts_when_existing_hedge_already_helps(self):
        legs = [{
            "sym": "QQQ260830P00470000", "qty": 2, "type": "P",
            "strike": 470.0, "expiry": "2026-08-30",
            "delta": -0.3, "price": 10.0, "market_value": 2000.0,
        }]
        with_existing = compute_qqq_hedge_plan(
            equity=500000, current_bd=900000, current_bdr=1.8, target_bd_ratio=1.50,
            qqq_price=500.0, qqq_iv_pct=20.0, beta_qqq=1.31,
            existing_legs=legs, existing_bd=-50000.0, n_existing=2,
            current_option_cost=2000.0, today=datetime.date(2026, 6, 1),
        )
        without_existing = compute_qqq_hedge_plan(
            equity=500000, current_bd=900000, current_bdr=1.8, target_bd_ratio=1.50,
            qqq_price=500.0, qqq_iv_pct=20.0, beta_qqq=1.31,
            existing_legs=[], existing_bd=0.0, n_existing=0,
            current_option_cost=0.0, today=datetime.date(2026, 6, 1),
        )
        # Existing short-Delta hedge already offsets some beta-weighted Delta,
        # so fewer additional spreads should be needed to reach the same target.
        self.assertLess(
            with_existing["plan_c"]["n_additional"],
            without_existing["plan_c"]["n_additional"],
        )

    def test_result_includes_hedge_governance_evaluation(self):
        result = compute_qqq_hedge_plan(
            equity=500000, current_bd=900000, current_bdr=1.8, target_bd_ratio=1.50,
            qqq_price=500.0, qqq_iv_pct=20.0, beta_qqq=1.31,
            existing_legs=[], existing_bd=0.0, n_existing=0,
            current_option_cost=0.0, today=datetime.date(2026, 6, 1),
        )
        self.assertIn("hedge_governance", result)
        self.assertIsInstance(result["hedge_governance"], dict)

    def test_vix_event_trend_flags_reach_hedge_governance_trigger_reasons(self):
        """2026-09-14: these three kwargs used to not exist at all, so every
        call site left hedge_governance's trigger check depending solely on
        BETA_DELTA_EXCESS. Confirms they now actually reach
        evaluate_protective_put_hedges() rather than being accepted and
        silently dropped."""
        legs = [{
            "sym": "QQQ260830P00470000", "qty": 1, "type": "P",
            "strike": 470.0, "expiry": "2026-08-30",
            "delta": -0.3, "price": 10.0, "market_value": 1000.0,
        }]
        baseline = compute_qqq_hedge_plan(
            equity=500000, current_bd=600000, current_bdr=1.2, target_bd_ratio=1.50,
            qqq_price=500.0, qqq_iv_pct=20.0, beta_qqq=1.31,
            existing_legs=legs, existing_bd=-30000.0, n_existing=1,
            current_option_cost=1000.0, today=datetime.date(2026, 6, 1),
        )
        # current_bdr (1.2) is under target (1.5): with every trigger left at
        # its default False, no trigger should be active.
        self.assertFalse(baseline["hedge_governance"]["trigger_active"])

        with_trend_break = compute_qqq_hedge_plan(
            equity=500000, current_bd=600000, current_bdr=1.2, target_bd_ratio=1.50,
            qqq_price=500.0, qqq_iv_pct=20.0, beta_qqq=1.31,
            existing_legs=legs, existing_bd=-30000.0, n_existing=1,
            current_option_cost=1000.0, today=datetime.date(2026, 6, 1),
            trend_break=True,
        )
        self.assertTrue(with_trend_break["hedge_governance"]["trigger_active"])
        self.assertIn("TREND_BREAK", with_trend_break["hedge_governance"]["trigger_reasons"])

        with_vix_and_event = compute_qqq_hedge_plan(
            equity=500000, current_bd=600000, current_bdr=1.2, target_bd_ratio=1.50,
            qqq_price=500.0, qqq_iv_pct=20.0, beta_qqq=1.31,
            existing_legs=legs, existing_bd=-30000.0, n_existing=1,
            current_option_cost=1000.0, today=datetime.date(2026, 6, 1),
            vix_spike=True, event_risk=True,
        )
        reasons = with_vix_and_event["hedge_governance"]["trigger_reasons"]
        self.assertIn("VIX_SPIKE", reasons)
        self.assertIn("EVENT_RISK", reasons)


class ComputeIndexHedgePlanTests(unittest.TestCase):
    """2026-09-20: QQQ 的对冲方案函数推广到任意指数 ETF，因为账户需要用 SMH
    对冲半导体那一段，而 SMH 此前一张方案都算不出来。"""

    def _smh(self, **overrides):
        kwargs = dict(
            underlying="SMH", equity=500000, current_bd=900000, current_bdr=1.8,
            target_bd_ratio=1.50, spot=300.0, iv_pct=32.0, beta=1.75,
            existing_legs=[], existing_bd=0.0, n_existing=0,
            current_option_cost=10000.0, today=datetime.date(2026, 6, 1),
        )
        kwargs.update(overrides)
        return compute_index_hedge_plan(**kwargs)

    def test_qqq_wrapper_output_is_unchanged_by_the_generalisation(self):
        """包装函数必须跟拆分之前逐字一致——门⑥账户监控的对冲卡片直接读
        这些键，宽度和行权价对齐一变，它显示的下单指令就变了。"""
        result = compute_qqq_hedge_plan(
            equity=500000, current_bd=900000, current_bdr=1.8, target_bd_ratio=1.50,
            qqq_price=500.0, qqq_iv_pct=20.0, beta_qqq=1.31,
            existing_legs=[], existing_bd=0.0, n_existing=0,
            current_option_cost=10000.0, today=datetime.date(2026, 6, 1),
        )
        self.assertAlmostEqual(result["plan_a"]["buy_strike"], 485.0)
        self.assertAlmostEqual(result["plan_a"]["sell_strike"], 450.0)
        self.assertAlmostEqual(result["plan_b"]["sell_strike"], 425.0)
        self.assertAlmostEqual(result["qqq_price"], 500.0)
        self.assertAlmostEqual(result["b_qqq"], 1.31)

    def test_spread_width_scales_with_spot_instead_of_staying_at_qqq_dollars(self):
        """$35 宽在 $600 的 QQQ 上是常规结构，在 $300 的 SMH 上是两倍宽——
        照搬会把 SMH 的方案算成一个完全不同性质的结构。"""
        plan = self._smh()["plan_a"]
        width = plan["buy_strike"] - plan["sell_strike"]
        self.assertAlmostEqual(width, 15.0)      # 300 × 5.8% → 对齐 $5
        self.assertAlmostEqual(plan["buy_strike"], 290.0)   # 300 × 97%

    def test_occ_symbols_carry_the_right_root(self):
        plan = self._smh()["plan_a"]
        self.assertEqual(plan["buy_occ"], "SMH260830P00290000")
        self.assertEqual(plan["sell_occ"], "SMH260830P00275000")

    def test_hedging_only_a_sleeve_needs_fewer_contracts_than_the_whole_book(self):
        """半导体那一段只占超出量的一部分；按全额算 SMH 的张数就是重复对冲。"""
        whole = self._smh()["plan_a"]["n_total"]
        sleeve = self._smh(bd_to_hedge=60000.0)["plan_a"]["n_total"]
        self.assertGreater(whole, 0)
        self.assertLess(sleeve, whole)

    def test_an_overridden_hedge_need_is_the_one_reported_back(self):
        result = self._smh(bd_to_hedge=60000.0)
        self.assertAlmostEqual(result["bd_to_hedge"], 60000.0)

    def test_plan_c_tops_up_with_plan_a_width_not_a_hardcoded_thirty_five(self):
        plan = self._smh()
        self.assertAlmostEqual(
            plan["plan_c"]["buy_strike"] - plan["plan_c"]["sell_strike"],
            plan["plan_a"]["buy_strike"] - plan["plan_a"]["sell_strike"],
        )

    def test_max_loss_is_the_debit_and_max_payoff_is_the_width_less_the_debit(self):
        plan = self._smh()["plan_a"]
        n, width = plan["n_total"], plan["buy_strike"] - plan["sell_strike"]
        self.assertAlmostEqual(plan["max_loss"], plan["total_cost"])
        self.assertAlmostEqual(
            plan["max_payoff"],
            round(n * width * 100 - n * plan["cost_per_spread"], 0),
        )

    def test_a_low_priced_etf_still_gets_a_usable_width(self):
        # 5.8% of $20 rounds to $0 on a $5 grid -- one strike step, not a
        # zero-width "spread" that would divide by zero downstream.
        plan = self._smh(spot=20.0)["plan_a"]
        self.assertGreater(plan["buy_strike"] - plan["sell_strike"], 0)


class CheckOtmSpreadAlertsTests(unittest.TestCase):
    def _leg(self, symbol, qty, unit_cost, market_value, strike=None, current_price=None):
        return {
            "symbol": symbol, "quantity": qty, "strike": strike,
            "expiry": None, "unit_cost": unit_cost,
            "market_value": market_value, "current_price": current_price,
        }

    def test_bear_put_debit_spread_near_zero_triggers_an_alert(self):
        rows = [
            self._leg("PLTR260717P00110000", 1, 8.0, 15.0),   # long, higher strike
            self._leg("PLTR260717P00100000", -1, 3.0, -5.0),  # short, lower strike
        ]
        alerts = check_otm_spread_alerts(rows, today=datetime.date(2026, 6, 1))
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert["spread_type"], "Bear Put Spread")
        self.assertEqual(alert["underlying"], "PLTR")
        self.assertAlmostEqual(alert["original_cost"], 500.0)
        self.assertAlmostEqual(alert["current_value"], 10.0)
        self.assertAlmostEqual(alert["pct_remaining"], 2.0)

    def test_credit_spread_never_alerts_regardless_of_value(self):
        # Bull Put Spread: long the lower strike, short the higher strike --
        # sold for a credit, so it losing value is the expected profitable
        # outcome, not something to warn about.
        rows = [
            self._leg("PLTR260717P00090000", 1, 1.0, 0.5),
            self._leg("PLTR260717P00100000", -1, 3.0, -0.2),
        ]
        alerts = check_otm_spread_alerts(rows, today=datetime.date(2026, 6, 1))
        self.assertEqual(alerts, [])

    def test_debit_spread_still_healthy_does_not_alert(self):
        rows = [
            self._leg("AAPL260717C00100000", 1, 5.0, 400.0),   # long call, well above 10%
            self._leg("AAPL260717C00110000", -1, 2.0, -150.0),
        ]
        alerts = check_otm_spread_alerts(rows, today=datetime.date(2026, 6, 1))
        self.assertEqual(alerts, [])

    def test_single_leg_without_a_pair_produces_no_alert(self):
        rows = [self._leg("PLTR260717P00110000", 1, 8.0, 15.0)]
        self.assertEqual(check_otm_spread_alerts(rows), [])

    def test_missing_market_value_falls_back_to_current_price(self):
        rows = [
            self._leg("PLTR260717P00110000", 1, 8.0, None, current_price=0.10),
            self._leg("PLTR260717P00100000", -1, 3.0, None, current_price=0.05),
        ]
        alerts = check_otm_spread_alerts(rows, today=datetime.date(2026, 6, 1))
        # long mv = 0.10*1*100=10; short mv = 0.05*1*100*(-1)=-5; current=5
        self.assertEqual(len(alerts), 1)
        self.assertAlmostEqual(alerts[0]["current_value"], 5.0)

    def test_alerts_sorted_by_lowest_remaining_percent_first(self):
        rows = [
            # Spread A: 8% remaining
            self._leg("AAAA260717P00110000", 1, 8.0, 40.0),
            self._leg("AAAA260717P00100000", -1, 3.0, 0.0),
            # Spread B: 2% remaining (more urgent)
            self._leg("BBBB260717P00110000", 1, 8.0, 10.0),
            self._leg("BBBB260717P00100000", -1, 3.0, 0.0),
        ]
        alerts = check_otm_spread_alerts(rows, today=datetime.date(2026, 6, 1))
        self.assertEqual([a["underlying"] for a in alerts], ["BBBB", "AAAA"])


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

    def test_stress_20_ratio_not_checked_when_omitted(self):
        """Old call sites/tests that don't pass stress_20_ratio keep the
        original -10%-only behavior -- adding the new dimension must not
        change anything for callers that don't opt into it."""
        self.assertEqual(classify_stress_status(0.05), "GREEN")

    def test_stress_20_breach_is_red_even_when_stress_10_is_green(self):
        """-20% 情景独立判断——2026-09-11 用户确认这是一条独立的线，不是
        -10%那条线的延伸：-10%只有5%(GREEN)，但-20%已经冲到25%（新增的
        stress_20_hard_stop），照样要报 RED_HARD_STOP。"""
        self.assertEqual(
            classify_stress_status(0.05, stress_20_ratio=0.25), "RED_HARD_STOP")

    def test_stress_20_below_its_own_line_does_not_escalate(self):
        self.assertEqual(
            classify_stress_status(0.05, stress_20_ratio=0.20), "GREEN")

    def test_stress_20_uses_magnitude_not_sign(self):
        self.assertEqual(
            classify_stress_status(0.0, stress_20_ratio=-0.30), "RED_HARD_STOP")

    def test_stress_20_custom_limit_overrides_default(self):
        custom = {**DEFAULT_RISK_LIMITS, "stress_20_hard_stop": 0.10}
        self.assertEqual(
            classify_stress_status(0.05, custom, stress_20_ratio=0.12),
            "RED_HARD_STOP")

    def test_collapsing_stress_10_tiers_to_a_single_line_is_a_data_change(self):
        """2026-09-11 用户确认 -10% 情景只要 0%/硬止损两档，不要中间的
        warning/de_risk 早期预警。这不需要改这个函数——把 warning 和
        de_risk 的限额值设成跟 hard_stop 一样，比较逻辑天然收缩成二档。"""
        binary = {**DEFAULT_RISK_LIMITS, "stress_warning": 0.15, "stress_de_risk": 0.15,
                  "stress_hard_stop": 0.15}
        self.assertEqual(classify_stress_status(0.10, binary), "GREEN")
        self.assertEqual(classify_stress_status(0.15, binary), "RED_HARD_STOP")


class ComputeSimImpactTests(unittest.TestCase):
    """13 scenarios ported 1:1 from tests/golden/test_sim_impact_golden.py's
    SCENARIOS table -- same inputs, expected values pulled from the golden
    snapshots captured off the original, unmodified
    account_monitor.py._compute_sim_impact. This class calls
    account.risk.compute_sim_impact directly with plain dict/list literals
    (no sqlite, no account_monitor.py AST-slice/exec) for a fast, DB-free
    unit layer; tests/golden/test_sim_impact_golden.py is the byte-for-byte
    cross-validation against the real DB-backed shell."""

    BASE_SNAP = {
        "equity": 100_000.0, "beta_delta": 5000.0, "theta_per_day": -50.0,
        "stress_10": -2000.0, "stress_20": -4000.0, "vega_per_pt": 300.0,
    }
    PRICES = {"NVDA": 120.0, "AMD": 150.0, "QQQ": 560.0}
    BETAS = {"NVDA": 1.8, "AMD": 2.1}
    NVDA_ROW = {"quantity": 2, "current_price": 10.0, "delta": 0.5, "gamma": 0.01,
                "theta": -0.05, "vega": 0.2, "market_value": 2000.0}
    ZZZZ_ROW = {"quantity": 3, "current_price": 2.5, "delta": 0.4, "gamma": 0.02,
                "theta": -0.03, "vega": 0.15, "market_value": 750.0}
    HPLAN_ZERO_N = {
        "plan_a": {"n_total": 0, "buy_strike": 550.0, "sell_strike": 530.0, "total_cost": 1000.0},
        "qqq_price": 560.0, "qqq_iv": 18.0, "b_qqq": 1.0, "plan_dte": 30,
    }
    HPLAN_NORMAL = {
        "plan_a": {"n_total": 3, "buy_strike": 550.0, "sell_strike": 530.0, "total_cost": 900.0},
        "qqq_price": 560.0, "qqq_iv": 18.0, "b_qqq": 1.0, "plan_dte": 30,
    }

    def _call(self, sim_actions, base_snap=None, hplan=None, options_by_underlying=None):
        return compute_sim_impact(
            sim_actions, base_snap if base_snap is not None else self.BASE_SNAP, hplan,
            underlying_prices=self.PRICES, beta_map=self.BETAS,
            options_by_underlying=options_by_underlying or {},
        )

    def test_empty_actions(self):
        result = self._call([])
        self.assertEqual(result, {
            "bd_delta": 0.0, "theta_delta": 0.0, "s10_delta": 0.0, "s20_delta": 0.0,
            "cash_delta": 0.0, "beta_delta": 5000.0, "beta_delta_ratio": 5.0,
            "theta_per_day": -50.0, "stress_10": -2000.0, "stress_10_ratio": -2.0,
            "stress_20": -4000.0, "stress_20_ratio": -4.0, "actions": [],
        })

    def test_unknown_type_default_label(self):
        result = self._call([{"type": "no_sim"}])
        self.assertEqual(result["actions"], ["持有（不变）"])
        self.assertEqual(result["bd_delta"], 0.0)

    def test_unknown_type_custom_label(self):
        result = self._call([{"type": "hold", "label": "部分平仓观察"}])
        self.assertEqual(result["actions"], ["部分平仓观察"])

    def test_close_underlying_normal(self):
        result = self._call(
            [{"type": "close_underlying", "underlying": "NVDA"}],
            options_by_underlying={"NVDA": [self.NVDA_ROW]},
        )
        self.assertEqual(result, {
            "bd_delta": -21600.0, "theta_delta": 10.0, "s10_delta": 736.0,
            "s20_delta": 1184.0, "cash_delta": 2000.0, "beta_delta": -16600.0,
            "beta_delta_ratio": -16.6, "theta_per_day": -40.0, "stress_10": -1264.0,
            "stress_10_ratio": -1.3, "stress_20": -2816.0, "stress_20_ratio": -2.8,
            "actions": ["关闭 NVDA 期权（收回约$+2,000）"],
        })

    def test_close_underlying_no_matching_positions(self):
        result = self._call(
            [{"type": "close_underlying", "underlying": "AMD"}],
            options_by_underlying={},
        )
        self.assertEqual(result["cash_delta"], 0.0)
        self.assertEqual(result["actions"], ["关闭 AMD 期权（收回约$+0）"])
        self.assertEqual(result["beta_delta"], 5000.0)

    def test_close_underlying_missing_price(self):
        """ZZZZ absent from the price map -> S falls back to 0.0. bd_delta
        and the two stress deltas are gated on S>0 and stay at zero, but
        theta/vega/cash deltas are NOT gated on S and still apply -- an
        easy-to-miss asymmetry in the original code (see
        account.risk.compute_sim_impact's docstring)."""
        result = self._call(
            [{"type": "close_underlying", "underlying": "ZZZZ"}],
            options_by_underlying={"ZZZZ": [self.ZZZZ_ROW]},
        )
        self.assertEqual(result, {
            "bd_delta": 0.0, "theta_delta": 9.0, "s10_delta": 0.0, "s20_delta": 0.0,
            "cash_delta": 750.0, "beta_delta": 5000.0, "beta_delta_ratio": 5.0,
            "theta_per_day": -41.0, "stress_10": -2000.0, "stress_10_ratio": -2.0,
            "stress_20": -4000.0, "stress_20_ratio": -4.0,
            "actions": ["关闭 ZZZZ 期权（收回约$+750）"],
        })

    def test_close_underlying_duplicate_underlying_applies_twice(self):
        """Two actions on the same underlying both draw from the SAME
        (deduplicated, single-query) row list and each add their own
        contribution -- deduplicating the DB round-trip must not deduplicate
        the computed effect."""
        result = self._call(
            [{"type": "close_underlying", "underlying": "NVDA"},
             {"type": "close_underlying", "underlying": "NVDA"}],
            options_by_underlying={"NVDA": [self.NVDA_ROW]},
        )
        self.assertEqual(result, {
            "bd_delta": -43200.0, "theta_delta": 20.0, "s10_delta": 1472.0,
            "s20_delta": 2368.0, "cash_delta": 4000.0, "beta_delta": -38200.0,
            "beta_delta_ratio": -38.2, "theta_per_day": -30.0, "stress_10": -528.0,
            "stress_10_ratio": -0.5, "stress_20": -1632.0, "stress_20_ratio": -1.6,
            "actions": ["关闭 NVDA 期权（收回约$+2,000）", "关闭 NVDA 期权（收回约$+2,000）"],
        })

    def test_qqq_hedge_none_hplan_skips(self):
        result = self._call([{"type": "qqq_hedge"}], hplan=None)
        self.assertEqual(result["actions"], ["QQQ对冲（数据不足，跳过）"])
        self.assertEqual(result["bd_delta"], 0.0)

    def test_qqq_hedge_error_hplan_skips(self):
        result = self._call([{"type": "qqq_hedge"}], hplan={"error": "insufficient_data"})
        self.assertEqual(result["actions"], ["QQQ对冲（数据不足，跳过）"])

    def test_qqq_hedge_zero_n_skips(self):
        result = self._call([{"type": "qqq_hedge"}], hplan=self.HPLAN_ZERO_N)
        self.assertEqual(result["actions"], ["QQQ对冲（现有对冲已足够）"])
        self.assertEqual(result["bd_delta"], 0.0)

    def test_qqq_hedge_normal(self):
        result = self._call([{"type": "qqq_hedge"}], hplan=self.HPLAN_NORMAL)
        self.assertEqual(result, {
            "bd_delta": -34514.0, "theta_delta": -18.48, "s10_delta": 6643.0,
            "s20_delta": 18448.0, "cash_delta": -900.0, "beta_delta": -29514.0,
            "beta_delta_ratio": -29.5, "theta_per_day": -68.48, "stress_10": 4643.0,
            "stress_10_ratio": 4.6, "stress_20": 14448.0, "stress_20_ratio": 14.4,
            "actions": ["QQQ Put Spread×3张（方案A，成本$900）"],
        })

    def test_equity_zero_division_guard(self):
        """equity=0 must not raise ZeroDivisionError -- all three ratio
        fields fall back to the bare int 0 (via `if equity else 0`), not
        0.0, matching the original's exact quirk."""
        zero_equity_snap = {**self.BASE_SNAP, "equity": 0.0}
        result = self._call([], base_snap=zero_equity_snap)
        self.assertEqual(result["beta_delta_ratio"], 0)
        self.assertEqual(result["stress_10_ratio"], 0)
        self.assertEqual(result["stress_20_ratio"], 0)

    def test_multi_action_stacking(self):
        result = self._call(
            [{"type": "close_underlying", "underlying": "NVDA"},
             {"type": "qqq_hedge"}],
            hplan=self.HPLAN_NORMAL,
            options_by_underlying={"NVDA": [self.NVDA_ROW]},
        )
        self.assertEqual(result, {
            "bd_delta": -56114.0, "theta_delta": -8.48, "s10_delta": 7379.0,
            "s20_delta": 19632.0, "cash_delta": 1100.0, "beta_delta": -51114.0,
            "beta_delta_ratio": -51.1, "theta_per_day": -58.48, "stress_10": 5379.0,
            "stress_10_ratio": 5.4, "stress_20": 15632.0, "stress_20_ratio": 15.6,
            "actions": ["关闭 NVDA 期权（收回约$+2,000）", "QQQ Put Spread×3张（方案A，成本$900）"],
        })


if __name__ == "__main__":
    unittest.main()
