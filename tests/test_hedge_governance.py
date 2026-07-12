import datetime
import unittest

from account.hedge_governance import evaluate_protective_put_hedges, generate_qqq_protection_plan


class HedgeGovernanceTests(unittest.TestCase):
    def test_valid_put_spread_when_trigger_active(self):
        result = evaluate_protective_put_hedges(
            [
                {"symbol": "QQQ260918P00550000", "quantity": 1, "current_price": 18.0},
                {"symbol": "QQQ260918P00515000", "quantity": -1, "current_price": 9.0},
            ],
            equity=350000,
            beta_delta_pct=180,
            target_beta_delta_pct=150,
            today=datetime.date(2026, 7, 11),
        )

        self.assertEqual(result["status"], "VALID_HEDGE")
        self.assertEqual(result["rows"][0]["structure"], "PUT_SPREAD")
        self.assertEqual(result["trigger_reasons"], ["BETA_DELTA_EXCESS"])

    def test_idle_long_put_is_violation(self):
        result = evaluate_protective_put_hedges(
            [{"symbol": "QQQ260918P00550000", "quantity": 1, "current_price": 18.0}],
            equity=350000,
            beta_delta_pct=120,
            target_beta_delta_pct=150,
            today=datetime.date(2026, 7, 11),
        )

        self.assertEqual(result["status"], "VIOLATION")
        codes = {i["code"] for i in result["rows"][0]["issues"]}
        self.assertIn("IDLE_HEDGE", codes)
        self.assertIn("NAKED_LONG_PUT_TOO_LONG", codes)

    def test_trigger_without_hedge_is_missing_hedge(self):
        result = evaluate_protective_put_hedges(
            [],
            equity=350000,
            beta_delta_pct=190,
            target_beta_delta_pct=150,
            today=datetime.date(2026, 7, 11),
        )

        self.assertEqual(result["status"], "MISSING_HEDGE")

    def test_hedge_budget_violation(self):
        result = evaluate_protective_put_hedges(
            [
                {"symbol": "QQQ260918P00550000", "quantity": 1, "current_price": 50.0},
                {"symbol": "QQQ260918P00515000", "quantity": -1, "current_price": 5.0},
            ],
            equity=100000,
            beta_delta_pct=180,
            target_beta_delta_pct=150,
            today=datetime.date(2026, 7, 11),
            max_campaign_cost_pct=1.0,
        )

        self.assertEqual(result["status"], "VIOLATION")
        self.assertEqual(result["portfolio_issues"][0]["code"], "HEDGE_COST_OVER_BUDGET")

    def test_qqq_plan_does_not_hedge_on_low_vix_alone(self):
        plan = generate_qqq_protection_plan(
            qqq_price=730,
            vix_value=13.5,
            ma50=745,
            ma200=690,
            beta_delta_pct=120,
            target_beta_delta_pct=150,
        )

        self.assertEqual(plan["action"], "NO_TRADE")
        self.assertEqual(plan["status"], "NO_HEDGE_NEEDED")

    def test_qqq_plan_light_protection_requires_portfolio_trigger(self):
        plan = generate_qqq_protection_plan(
            qqq_price=730,
            vix_value=16,
            ma50=745,
            ma200=690,
            beta_delta_pct=180,
            target_beta_delta_pct=150,
        )

        self.assertEqual(plan["action"], "LIGHT_PROTECTION")
        self.assertEqual(plan["coverage_pct"], 15)
        self.assertEqual(plan["buy_put"], 700)
        self.assertEqual(plan["sell_put"], 640)

    def test_qqq_plan_heavy_protection_on_200dma_break(self):
        plan = generate_qqq_protection_plan(
            qqq_price=650,
            vix_value=24,
            ma50=710,
            ma200=690,
            beta_delta_pct=140,
            target_beta_delta_pct=150,
        )

        self.assertEqual(plan["action"], "HEAVY_PROTECTION")
        self.assertEqual(plan["coverage_pct"], 30)
        self.assertEqual(plan["max_cost_pct"], 1.0)


if __name__ == "__main__":
    unittest.main()
