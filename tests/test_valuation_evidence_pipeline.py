import copy
import unittest

from scoring.unified_valuation import run_unified_valuation
from scoring.valuation_evidence_pipeline import (
    audit_legacy_valuation_row,
    build_evidence_bundle,
    build_live_valuation_request,
    parse_observation,
    reconcile_field,
)


AS_OF = "2026-07-24T20:00:00+00:00"


def observation(
    field,
    value,
    unit,
    family,
    *,
    locator=None,
    available_at="2026-07-24T19:00:00+00:00",
    retrieved_at="2026-07-24T19:30:00+00:00",
):
    return {
        "field": field,
        "value": value,
        "unit": unit,
        "source": f"{family} observation",
        "source_type": "MARKET" if field == "current_price" else "PRIMARY",
        "source_family": family,
        "origin_family": (
            f"{family}_ORIGIN"
            if field in {"current_price", "forward_eps", "forward_revenue"}
            else "ISSUER_REPORTED"
        ),
        "lineage_id": f"TEST:{field}:2026Q2:{family}",
        "source_locator": locator or f"https://example.test/{family}/{field}",
        "observed_at": "2026-07-24T18:59:00+00:00",
        "available_at": available_at,
        "retrieved_at": retrieved_at,
        "extraction_method": f"{family} test fixture extraction",
    }


def mature_observations():
    specs = {
        "current_price": (80, "USD/share"),
        "diluted_shares": (100, "shares"),
        "net_cash": (1000, "USD"),
        "revenue_ttm": (1000, "USD"),
        "forward_eps": (5, "USD/share"),
    }
    result = []
    for field, (value, unit) in specs.items():
        result.extend(
            [
                observation(field, value, unit, "PROVIDER_A"),
                observation(field, value * 1.001, unit, "PROVIDER_B"),
            ]
        )
    return result


def mature_scenarios():
    result = {}
    for name, growth, margin, eps, pe in (
        ("bear", 0.02, 0.18, 4, 15),
        ("base", 0.08, 0.22, 6, 20),
        ("bull", 0.14, 0.25, 8, 25),
    ):
        result[name] = {
            "assumption_basis": f"{name} test assumptions",
            "revenue_growth": [growth] * 5,
            "fcf_margin": [margin] * 5,
            "discount_rate": 0.10,
            "terminal_growth": 0.03,
            "forward_eps": eps,
            "target_pe": pe,
        }
    return result


class ValuationEvidencePipelineTests(unittest.TestCase):
    def test_two_independent_families_inside_tolerance_verify(self):
        result = reconcile_field(
            "current_price",
            [
                observation("current_price", 100, "USD/share", "YAHOO"),
                observation("current_price", 100.5, "USD/share", "POLYGON"),
            ],
            expected_unit="USD/share",
            as_of=AS_OF,
            max_age_days=3,
        )
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(
            result["record"]["verification"]["secondary_source_family"],
            "POLYGON",
        )

    def test_same_provider_through_two_wrappers_is_not_independent(self):
        result = reconcile_field(
            "current_price",
            [
                observation(
                    "current_price",
                    100,
                    "USD/share",
                    "YAHOO_FINANCE",
                    locator="wrapper://one/yahoo",
                ),
                observation(
                    "current_price",
                    100,
                    "USD/share",
                    "YAHOO_FINANCE",
                    locator="wrapper://two/yahoo",
                ),
            ],
            expected_unit="USD/share",
            as_of=AS_OF,
            max_age_days=3,
        )
        self.assertEqual(result["status"], "NEEDS_EVIDENCE")
        self.assertIsNone(result["record"])

    def test_price_wrappers_with_same_underlying_origin_are_not_independent(self):
        left = observation("current_price", 100, "USD/share", "VENDOR_A")
        right = observation("current_price", 100, "USD/share", "VENDOR_B")
        right["origin_family"] = left["origin_family"]
        result = reconcile_field(
            "current_price",
            [left, right],
            expected_unit="USD/share",
            as_of=AS_OF,
            max_age_days=3,
        )
        self.assertEqual(result["status"], "NEEDS_EVIDENCE")
        self.assertIsNone(result["record"])

    def test_fetch_time_cannot_replace_missing_available_time(self):
        payload = observation("revenue_ttm", 1000, "USD", "SEC")
        payload.pop("available_at")
        with self.assertRaisesRegex(ValueError, "available_at"):
            parse_observation(payload)

    def test_observation_retrieved_after_cutoff_is_lookahead(self):
        result = reconcile_field(
            "current_price",
            [
                observation(
                    "current_price",
                    100,
                    "USD/share",
                    "YAHOO",
                    retrieved_at="2026-07-25T00:00:00+00:00",
                ),
                observation("current_price", 100, "USD/share", "POLYGON"),
            ],
            expected_unit="USD/share",
            as_of=AS_OF,
            max_age_days=3,
        )
        self.assertEqual(result["status"], "NEEDS_EVIDENCE")
        self.assertIn("LOOKAHEAD", result["rejected"][0]["reasons"])

    def test_conflict_is_not_averaged(self):
        result = reconcile_field(
            "diluted_shares",
            [
                observation("diluted_shares", 100, "shares", "SEC"),
                observation("diluted_shares", 130, "shares", "POLYGON"),
            ],
            expected_unit="shares",
            as_of=AS_OF,
            max_age_days=130,
        )
        self.assertEqual(result["status"], "CONFLICTED")
        self.assertIsNone(result["record"])

    def test_missing_required_field_blocks_request(self):
        observations = [
            item for item in mature_observations() if item["field"] != "forward_eps"
        ]
        envelope = build_live_valuation_request(
            valuation_case_id="VAL-BLOCKED",
            ticker="TEST",
            profile="MATURE_PROFITABLE",
            as_of=AS_OF,
            observations=observations,
            scenarios=mature_scenarios(),
            realization_months=24,
        )
        self.assertEqual(envelope["status"], "BLOCKED")
        self.assertIsNone(envelope["request"])
        self.assertTrue(
            any("forward_eps" in reason for reason in envelope["evidence"]["blocking_reasons"])
        )

    def test_verified_bundle_builds_formal_live_request(self):
        envelope = build_live_valuation_request(
            valuation_case_id="VAL-READY",
            ticker="TEST",
            profile="MATURE_PROFITABLE",
            as_of=AS_OF,
            observations=mature_observations(),
            scenarios=mature_scenarios(),
            realization_months=24,
            dispersion_reconciliation="DCF and P/E use different normalized bases.",
        )
        self.assertEqual(envelope["status"], "READY")
        output = run_unified_valuation(envelope["request"])
        self.assertEqual(output["data_quality_status"], "PASS")
        self.assertEqual(
            output["evidence_snapshot_id"],
            envelope["evidence"]["evidence_snapshot_id"],
        )

    def test_snapshot_changes_when_source_value_changes(self):
        first = build_evidence_bundle(
            "MATURE_PROFITABLE",
            mature_observations(),
            as_of=AS_OF,
        )
        changed = copy.deepcopy(mature_observations())
        changed[0]["value"] = 80.2
        second = build_evidence_bundle(
            "MATURE_PROFITABLE",
            changed,
            as_of=AS_OF,
        )
        self.assertNotEqual(
            first["evidence_snapshot_id"],
            second["evidence_snapshot_id"],
        )

    def test_legacy_flat_row_is_never_promoted(self):
        result = audit_legacy_valuation_row(
            {
                "raw_current_price_yf": "100 [yf]",
                "source_urls_yf": "https://finance.yahoo.com/quote/TEST",
                "source_urls_sec": "https://sec.gov/example",
                "last_refreshed": "2026-07-24 12:00",
            }
        )
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertFalse(result["eligible_for_live_valuation"])


if __name__ == "__main__":
    unittest.main()
