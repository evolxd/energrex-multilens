import unittest

from scoring.mispricing_adapters import (
    ExpressionRoute,
    build_valuation_request,
    route_portfolio_expression,
    validate_valuation_output,
)


def valuation_output(**overrides):
    result = {
        "valuation_case_id": "VAL-FUTU-20260723",
        "source_snapshot_id": "SNAP-FUTU-20260723",
        "ticker": "FUTU",
        "as_of_date": "2026-07-23",
        "data_quality_status": "PASS",
        "data_validity_rate": 0.98,
        "methods": ["DCF", "FORWARD_PE"],
        "method_results": {
            "DCF": {"status": "PASS", "fair_value": 122},
            "FORWARD_PE": {"status": "PASS", "fair_value": 130},
        },
        "current_price": 102,
        "bear_value": 60,
        "base_value": 126,
        "bull_value": 208,
        "probability_weighted_value": 130,
        "reverse_valuation_result": {"implied_growth": 0.08},
        "margin_of_safety": 0.19,
        "upside_downside_ratio": 2.1,
        "realization_months": 18,
        "annualized_expected_return": 0.15,
        "price_gate": "WAIT",
    }
    result.update(overrides)
    return result


def option_case(**overrides):
    result = {
        "defined_risk": True,
        "catalyst_date": "2027-03-01",
        "expiry": "2027-06-18",
        "maximum_loss": 500,
        "portfolio_loss_budget": 1000,
        "break_even": 115,
        "upside_cap": 140,
        "bid_ask_spread_pct": 0.05,
        "open_interest": 200,
        "portfolio_limits_pass": True,
    }
    result.update(overrides)
    return result


class MispricingAdapterTests(unittest.TestCase):
    def test_request_contains_assumptions_not_a_second_target_price(self):
        request = build_valuation_request(
            {
                "ticker": "FUTU",
                "primary_path": "GREAT_BUSINESS_STUMBLE",
                "thesis": {
                    "market_believes": "growth is permanently impaired",
                    "variant_view": "the event is substantially one-time",
                    "falsifiable_by": "core markets lose funded accounts",
                },
                "payer_economics": {"revenue_cyclicality": "HIGH"},
            }
        )
        self.assertNotIn("target_price", request)
        self.assertNotIn("valuation_score", request)

    def test_valuation_score_alone_cannot_close_p5(self):
        with self.assertRaisesRegex(ValueError, "valuation_score alone"):
            validate_valuation_output(
                {"ticker": "FUTU", "valuation_score": 80},
                expected_ticker="FUTU",
            )

    def test_one_method_is_not_a_formal_valuation(self):
        with self.assertRaisesRegex(ValueError, "at least two independent"):
            validate_valuation_output(
                valuation_output(methods=["DCF"]),
                expected_ticker="FUTU",
            )

    def test_method_labels_without_outputs_cannot_close_p5(self):
        output = valuation_output()
        del output["method_results"]
        with self.assertRaisesRegex(ValueError, "method_results"):
            validate_valuation_output(output, expected_ticker="FUTU")

    def test_large_method_disagreement_requires_reconciliation(self):
        output = valuation_output(
            method_results={
                "DCF": {"status": "PASS", "fair_value": 90},
                "FORWARD_PE": {"status": "PASS", "fair_value": 150},
            }
        )
        with self.assertRaisesRegex(ValueError, "dispersion_reconciliation"):
            validate_valuation_output(output, expected_ticker="FUTU")

    def test_valid_formal_valuation_is_accepted(self):
        result = validate_valuation_output(
            valuation_output(),
            expected_ticker="FUTU",
        )
        self.assertEqual(result.base_value, 126)

    def test_missing_evidence_cannot_be_routed_to_options(self):
        valuation = validate_valuation_output(
            valuation_output(),
            expected_ticker="FUTU",
        )
        decision = route_portfolio_expression(
            mispricing_decision="WATCH_P2",
            valuation=valuation,
            option=option_case(),
        )
        self.assertEqual(decision.route, ExpressionRoute.BLOCKED)

    def test_price_wait_without_option_stays_waiting(self):
        valuation = validate_valuation_output(
            valuation_output(),
            expected_ticker="FUTU",
        )
        decision = route_portfolio_expression(
            mispricing_decision="WAIT_FOR_PRICE",
            valuation=valuation,
        )
        self.assertEqual(decision.route, ExpressionRoute.WAIT)

    def test_defined_risk_option_can_enter_review_not_order(self):
        valuation = validate_valuation_output(
            valuation_output(),
            expected_ticker="FUTU",
        )
        decision = route_portfolio_expression(
            mispricing_decision="WAIT_FOR_PRICE",
            valuation=valuation,
            option=option_case(),
        )
        self.assertEqual(
            decision.route,
            ExpressionRoute.DEFINED_RISK_OPTION_REVIEW,
        )

    def test_option_expiry_and_loss_budget_are_hard_constraints(self):
        valuation = validate_valuation_output(
            valuation_output(),
            expected_ticker="FUTU",
        )
        decision = route_portfolio_expression(
            mispricing_decision="WAIT_FOR_PRICE",
            valuation=valuation,
            option=option_case(expiry="2027-03-15", maximum_loss=1500),
        )
        self.assertEqual(decision.route, ExpressionRoute.WAIT)
        self.assertEqual(len(decision.reasons), 2)

    def test_option_break_even_and_cap_must_match_base_value(self):
        valuation = validate_valuation_output(
            valuation_output(),
            expected_ticker="FUTU",
        )
        decision = route_portfolio_expression(
            mispricing_decision="WAIT_FOR_PRICE",
            valuation=valuation,
            option=option_case(break_even=130, upside_cap=120),
        )
        self.assertEqual(decision.route, ExpressionRoute.WAIT)
        self.assertEqual(len(decision.reasons), 2)

    def test_price_pass_routes_to_stock_review_only(self):
        valuation = validate_valuation_output(
            valuation_output(price_gate="PASS"),
            expected_ticker="FUTU",
        )
        decision = route_portfolio_expression(
            mispricing_decision="DEEP_RESEARCH_P0",
            valuation=valuation,
            option=option_case(),
        )
        self.assertEqual(decision.route, ExpressionRoute.STOCK_REVIEW)


if __name__ == "__main__":
    unittest.main()
