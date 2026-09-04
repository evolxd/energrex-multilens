import copy
import unittest

from scoring.mispricing_adapters import validate_valuation_output
from scoring.unified_valuation import run_unified_valuation


AS_OF = "2026-07-24T20:00:00+00:00"


def field(
    value,
    unit,
    *,
    age_days=0,
    cross_check=True,
    mode="INDEPENDENT_ORIGIN",
):
    day = 24 - age_days
    timestamp = f"2026-07-{day:02d}T20:00:00+00:00"
    return {
        "value": value,
        "unit": unit,
        "source": "engineering fixture",
        "source_type": "PRIMARY",
        "source_family": "FIXTURE_PRIMARY",
        "origin_family": (
            "FIXTURE_REPORTED_ORIGIN"
            if mode == "INDEPENDENT_EXTRACTION"
            else "FIXTURE_PRIMARY_ORIGIN"
        ),
        "lineage_id": f"fixture-primary-{unit}-{value}",
        "source_locator": f"fixture://primary/{unit}/{value}",
        "extraction_method": "primary fixture extraction",
        "observed_at": timestamp,
        "available_at": timestamp,
        "retrieved_at": timestamp,
        "verification": {
            "status": "VERIFIED",
            "secondary_source": "independent engineering fixture"
            if cross_check
            else "",
            "secondary_source_family": "FIXTURE_SECONDARY"
            if cross_check
            else "",
            "secondary_origin_family": (
                "FIXTURE_REPORTED_ORIGIN"
                if mode == "INDEPENDENT_EXTRACTION"
                else "FIXTURE_SECONDARY_ORIGIN"
            )
            if cross_check
            else "",
            "secondary_lineage_id": f"fixture-secondary-{unit}-{value}"
            if cross_check
            else "",
            "secondary_source_locator": f"fixture://secondary/{unit}/{value}"
            if cross_check
            else "",
            "secondary_extraction_method": "independent fixture extraction",
            "secondary_available_at": timestamp,
            "secondary_retrieved_at": timestamp,
            "secondary_value": value,
            "relative_difference": 0.0,
            "tolerance": 0.01,
            "cross_check_mode": mode,
        },
    }


def dcf_assumptions(growth, margin, *, eps=None, pe=None, revenue=None, evs=None):
    result = {
        "assumption_basis": "test-only explicit scenario assumptions",
        "revenue_growth": [growth] * 5,
        "fcf_margin": [margin] * 5,
        "discount_rate": 0.10,
        "terminal_growth": 0.03,
    }
    if eps is not None:
        result.update({"forward_eps": eps, "target_pe": pe})
    if revenue is not None:
        result.update({"forward_revenue": revenue, "target_ev_sales": evs})
    return result


def mature_case():
    return {
        "schema_version": "1.0",
        "evidence_mode": "LIVE",
        "evidence_snapshot_id": "fixture-evidence-snapshot",
        "valuation_case_id": "VAL-MATURE-1",
        "ticker": "MATURE",
        "profile": "MATURE_PROFITABLE",
        "as_of": AS_OF,
        "as_of_date": "2026-07-24",
        "fields": {
            "current_price": field(80, "USD/share"),
            "diluted_shares": field(
                100, "shares", mode="INDEPENDENT_EXTRACTION"
            ),
            "net_cash": field(1000, "USD", mode="INDEPENDENT_EXTRACTION"),
            "revenue_ttm": field(1000, "USD", mode="INDEPENDENT_EXTRACTION"),
            "forward_eps": field(5, "USD/share"),
        },
        "scenarios": {
            "bear": dcf_assumptions(0.02, 0.18, eps=4, pe=15),
            "base": dcf_assumptions(0.08, 0.22, eps=6, pe=20),
            "bull": dcf_assumptions(0.14, 0.25, eps=8, pe=25),
        },
        "realization_months": 24,
        "dispersion_reconciliation": "DCF includes excess cash; P/E cross-checks normalized earnings.",
    }


def growth_case():
    case = mature_case()
    case["valuation_case_id"] = "VAL-GROWTH-1"
    case["ticker"] = "GROWTH"
    case["profile"] = "HIGH_GROWTH"
    case["fields"].pop("forward_eps")
    case["fields"]["forward_revenue"] = field(1300, "USD")
    case["scenarios"] = {
        "bear": dcf_assumptions(0.10, 0.12, revenue=1300, evs=4),
        "base": dcf_assumptions(0.22, 0.18, revenue=1500, evs=7),
        "bull": dcf_assumptions(0.35, 0.24, revenue=1800, evs=10),
    }
    return case


def financial_case():
    return {
        "schema_version": "1.0",
        "evidence_mode": "LIVE",
        "evidence_snapshot_id": "fixture-evidence-snapshot",
        "valuation_case_id": "VAL-FIN-1",
        "ticker": "BANK",
        "profile": "FINANCIAL",
        "as_of": AS_OF,
        "as_of_date": "2026-07-24",
        "fields": {
            "current_price": field(40, "USD/share"),
            "diluted_shares": field(
                100, "shares", mode="INDEPENDENT_EXTRACTION"
            ),
            "book_value_per_share": field(
                30, "USD/share", mode="INDEPENDENT_EXTRACTION"
            ),
        },
        "scenarios": {
            "bear": {
                "assumption_basis": "test-only bear assumptions",
                "normalized_roe": 0.10,
                "cost_of_equity": 0.12,
                "terminal_growth": 0.02,
                "roe_path": [0.10] * 5,
                "payout_ratio": 0.45,
                "terminal_roe": 0.09,
            },
            "base": {
                "assumption_basis": "test-only base assumptions",
                "normalized_roe": 0.16,
                "cost_of_equity": 0.10,
                "terminal_growth": 0.03,
                "roe_path": [0.16] * 5,
                "payout_ratio": 0.40,
                "terminal_roe": 0.13,
            },
            "bull": {
                "assumption_basis": "test-only bull assumptions",
                "normalized_roe": 0.21,
                "cost_of_equity": 0.09,
                "terminal_growth": 0.035,
                "roe_path": [0.21] * 5,
                "payout_ratio": 0.35,
                "terminal_roe": 0.17,
            },
        },
        "realization_months": 24,
        "dispersion_reconciliation": "Residual income checks the justified P/B steady-state result.",
    }


def nav_case():
    return {
        "schema_version": "1.0",
        "evidence_mode": "LIVE",
        "evidence_snapshot_id": "fixture-evidence-snapshot",
        "valuation_case_id": "VAL-NAV-1",
        "ticker": "ASSET",
        "profile": "ASSET_NAV",
        "as_of": AS_OF,
        "as_of_date": "2026-07-24",
        "fields": {
            "current_price": field(20, "USD/share"),
            "diluted_shares": field(
                10, "shares", mode="INDEPENDENT_EXTRACTION"
            ),
            "gross_asset_value": field(
                500, "USD", mode="INDEPENDENT_EXTRACTION"
            ),
            "total_claims": field(
                150, "USD", mode="INDEPENDENT_EXTRACTION"
            ),
        },
        "scenarios": {
            "bear": {
                "assumption_basis": "test-only bear recovery assumptions",
                "nav_recovery_rate": 0.60,
                "operating_business_value": 20,
                "hidden_asset_value": 0,
                "transaction_cost": 30,
                "liquidation_recovery_rate": 0.45,
                "hidden_liquidation_value": 0,
                "wind_down_cost": 35,
                "cash_burn": 20,
            },
            "base": {
                "assumption_basis": "test-only base recovery assumptions",
                "nav_recovery_rate": 0.85,
                "operating_business_value": 50,
                "hidden_asset_value": 20,
                "transaction_cost": 25,
                "liquidation_recovery_rate": 0.70,
                "hidden_liquidation_value": 10,
                "wind_down_cost": 30,
                "cash_burn": 15,
            },
            "bull": {
                "assumption_basis": "test-only bull recovery assumptions",
                "nav_recovery_rate": 1.00,
                "operating_business_value": 80,
                "hidden_asset_value": 40,
                "transaction_cost": 20,
                "liquidation_recovery_rate": 0.90,
                "hidden_liquidation_value": 30,
                "wind_down_cost": 20,
                "cash_burn": 10,
            },
        },
        "realization_months": 18,
        "dispersion_reconciliation": "Going-concern NAV and liquidation recovery bound the outcome.",
    }


class UnifiedValuationTests(unittest.TestCase):
    def test_mature_profile_produces_two_methods_and_reverse_dcf(self):
        output = run_unified_valuation(mature_case())
        self.assertEqual(output["methods"], ["DCF", "FORWARD_PE"])
        self.assertEqual(output["reverse_valuation_result"]["method"], "REVERSE_DCF")
        validate_valuation_output(output, expected_ticker="MATURE")

    def test_high_growth_profile_routes_to_dcf_and_ev_sales(self):
        output = run_unified_valuation(growth_case())
        self.assertEqual(output["methods"], ["DCF", "EV_SALES"])
        validate_valuation_output(output, expected_ticker="GROWTH")

    def test_financial_profile_avoids_ev_sales(self):
        output = run_unified_valuation(financial_case())
        self.assertEqual(output["methods"], ["JUSTIFIED_PB", "RESIDUAL_INCOME"])
        self.assertEqual(
            output["reverse_valuation_result"]["method"],
            "REVERSE_JUSTIFIED_PB",
        )
        validate_valuation_output(output, expected_ticker="BANK")

    def test_asset_profile_values_common_equity_after_claims(self):
        output = run_unified_valuation(nav_case())
        self.assertEqual(output["methods"], ["ADJUSTED_NAV", "LIQUIDATION_RECOVERY"])
        self.assertLess(output["bear_value"], output["base_value"])
        self.assertLess(output["base_value"], output["bull_value"])
        validate_valuation_output(output, expected_ticker="ASSET")

    def test_missing_cross_check_is_a_critical_veto(self):
        case = mature_case()
        case["fields"]["current_price"]["verification"]["secondary_source"] = ""
        output = run_unified_valuation(case)
        self.assertEqual(output["data_quality_status"], "REVIEW_REQUIRED")
        self.assertEqual(output["price_gate"], "FAIL")

    def test_same_provider_family_cannot_masquerade_as_cross_check(self):
        case = mature_case()
        case["fields"]["current_price"]["verification"][
            "secondary_source_family"
        ] = "FIXTURE_PRIMARY"
        output = run_unified_valuation(case)
        self.assertEqual(output["data_quality_status"], "REVIEW_REQUIRED")
        self.assertIn(
            "CROSS_CHECK_NOT_INDEPENDENT",
            output["data_quality"]["field_results"]["current_price"]["reasons"],
        )

    def test_financial_fact_cannot_claim_independent_origin_mode(self):
        case = mature_case()
        case["fields"]["revenue_ttm"]["verification"][
            "cross_check_mode"
        ] = "INDEPENDENT_ORIGIN"
        output = run_unified_valuation(case)
        self.assertEqual(output["data_quality_status"], "REVIEW_REQUIRED")
        self.assertIn(
            "CROSS_CHECK_MODE_MISMATCH",
            output["data_quality"]["field_results"]["revenue_ttm"]["reasons"],
        )

    def test_lookahead_data_is_rejected(self):
        case = mature_case()
        case["fields"]["current_price"]["available_at"] = "2026-07-25T20:00:00+00:00"
        output = run_unified_valuation(case)
        self.assertIn(
            "LOOKAHEAD",
            output["data_quality"]["field_results"]["current_price"]["reasons"],
        )

    def test_unreconciled_large_dispersion_cannot_pass(self):
        case = mature_case()
        case["dispersion_reconciliation"] = ""
        case["scenarios"]["base"]["target_pe"] = 60
        output = run_unified_valuation(case)
        self.assertEqual(output["data_quality_status"], "REVIEW_REQUIRED")

    def test_scenario_order_violation_cannot_pass(self):
        case = mature_case()
        case["scenarios"]["bear"], case["scenarios"]["bull"] = (
            case["scenarios"]["bull"],
            case["scenarios"]["bear"],
        )
        output = run_unified_valuation(case)
        self.assertEqual(output["data_quality_status"], "REVIEW_REQUIRED")
        self.assertIn(
            "scenario values do not satisfy bear <= base <= bull",
            output["review_reasons"],
        )

    def test_source_snapshot_is_content_addressed(self):
        first = run_unified_valuation(mature_case())
        changed = mature_case()
        changed["fields"]["current_price"]["value"] = 81
        second = run_unified_valuation(changed)
        self.assertNotEqual(first["source_snapshot_id"], second["source_snapshot_id"])

    def test_engineering_only_fixture_cannot_pass_formal_gate(self):
        case = mature_case()
        case["evidence_mode"] = "ENGINEERING_ONLY"
        output = run_unified_valuation(case)
        self.assertEqual(output["data_quality_status"], "REVIEW_REQUIRED")
        self.assertEqual(output["price_gate"], "FAIL")

    def test_live_request_without_evidence_snapshot_is_rejected(self):
        case = mature_case()
        case.pop("evidence_snapshot_id")
        with self.assertRaisesRegex(ValueError, "evidence_snapshot_id"):
            run_unified_valuation(case)

    def test_symbolic_second_method_weight_is_rejected(self):
        case = mature_case()
        case["scenarios"]["base"]["method_weights"] = {
            "DCF": 0.99,
            "FORWARD_PE": 0.01,
        }
        with self.assertRaisesRegex(ValueError, "at least 20%"):
            run_unified_valuation(case)

    def test_negative_nav_claims_are_rejected(self):
        case = nav_case()
        case["fields"]["total_claims"]["value"] = -10
        with self.assertRaisesRegex(ValueError, "non-negative"):
            run_unified_valuation(case)


if __name__ == "__main__":
    unittest.main()
