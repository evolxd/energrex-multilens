import copy
import datetime as dt
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
    source_type=None,
):
    return {
        "field": field,
        "value": value,
        "unit": unit,
        "source": f"{family} observation",
        "source_type": source_type
        or ("MARKET" if field == "current_price" else "PRIMARY"),
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


# ═══════════════════════════════════════════════════════════════════════
# Characterization tests for reconcile_field's internal branch structure,
# added ahead of decomposing it into focused pure functions (see
# scoring/valuation_evidence_pipeline.py). These lock CURRENT behavior,
# including two subtleties worth naming explicitly rather than "fixing":
#
# - A pair excluded from corroboration solely because both observations
#   share the same source_locator (group D) is NOT distinguished, in the
#   resulting status/reason text, from a pair that was compared and
#   genuinely disagreed (group E). Both surface as CONFLICTED with the
#   generic "independent observations disagree beyond X%" reason, even
#   though the locator-duplicate case was never actually compared.
# - The final tie-break in pair selection (group F) compares
#   (pair_left.source_family, pair_right.source_family) using whatever
#   left/right roles the pair happened to have from the admission-order
#   nested loop -- not a globally sorted family comparison. This is
#   deterministic given fixed input order, but the win condition is
#   easy to misdescribe as "alphabetically first family wins overall"
#   when it is really "alphabetically first among this pair's own
#   left/right slots".
# ═══════════════════════════════════════════════════════════════════════
class ReconcileFieldGroupATests(unittest.TestCase):
    """A group: reconciliation-parameter resolution and validation."""

    def test_unconfigured_tolerance_field_raises_value_error(self):
        with self.assertRaisesRegex(
            ValueError, "No reconciliation tolerance configured"
        ):
            reconcile_field(
                "totally_unconfigured_field_xyz",
                [],
                expected_unit="USD",
                as_of=AS_OF,
                max_age_days=30,
            )

    def test_tolerance_out_of_range_raises_value_error(self):
        for bad_tolerance in (0.30, -0.01):
            with self.subTest(tolerance=bad_tolerance):
                with self.assertRaisesRegex(
                    ValueError, "tolerance must be between 0 and 0.25"
                ):
                    reconcile_field(
                        "current_price",
                        [],
                        expected_unit="USD/share",
                        as_of=AS_OF,
                        max_age_days=3,
                        tolerance=bad_tolerance,
                    )

    def test_invalid_cross_check_mode_raises_value_error(self):
        with self.assertRaisesRegex(
            ValueError, "cross_check_mode must be one of"
        ):
            reconcile_field(
                "current_price",
                [],
                expected_unit="USD/share",
                as_of=AS_OF,
                max_age_days=3,
                cross_check_mode="BOGUS_MODE",
            )

    def test_naive_as_of_datetime_raises_value_error(self):
        with self.assertRaisesRegex(ValueError, "as_of must include a timezone"):
            reconcile_field(
                "current_price",
                [],
                expected_unit="USD/share",
                as_of=dt.datetime(2026, 7, 24, 20, 0, 0),
                max_age_days=3,
            )


class ReconcileFieldGroupBTests(unittest.TestCase):
    """B group: per-observation parse and admission filtering."""

    def test_field_mismatch_is_rejected(self):
        mismatched = observation("current_price", 100, "USD/share", "YAHOO")
        result = reconcile_field(
            "diluted_shares",
            [mismatched],
            expected_unit="shares",
            as_of=AS_OF,
            max_age_days=130,
        )
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("FIELD_MISMATCH", result["rejected"][0]["reasons"])

    def test_stale_observation_is_rejected(self):
        stale = observation(
            "revenue_ttm",
            1000,
            "USD",
            "SEC",
            available_at="2026-06-01T00:00:00+00:00",
            retrieved_at="2026-06-01T01:00:00+00:00",
        )
        stale["observed_at"] = "2026-05-31T23:00:00+00:00"
        result = reconcile_field(
            "revenue_ttm",
            [stale],
            expected_unit="USD",
            as_of=AS_OF,
            max_age_days=10,
        )
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("STALE", result["rejected"][0]["reasons"])

    def test_no_observations_at_all_yields_invalid_status(self):
        result = reconcile_field(
            "current_price",
            [],
            expected_unit="USD/share",
            as_of=AS_OF,
            max_age_days=3,
        )
        self.assertEqual(result["status"], "INVALID")
        self.assertIsNone(result["record"])
        self.assertEqual(result["accepted_count"], 0)
        self.assertEqual(result["rejected"], [])

    def test_all_observations_rejected_yields_invalid_status_with_reasons(self):
        wrong_unit = [
            observation("current_price", 100, "EUR/share", "YAHOO"),
            observation("current_price", 100.2, "EUR/share", "POLYGON"),
        ]
        result = reconcile_field(
            "current_price",
            wrong_unit,
            expected_unit="USD/share",
            as_of=AS_OF,
            max_age_days=3,
        )
        self.assertEqual(result["status"], "INVALID")
        self.assertIsNone(result["record"])
        self.assertEqual(len(result["rejected"]), 2)
        for entry in result["rejected"]:
            self.assertIn("UNIT_MISMATCH", entry["reasons"])


class ReconcileFieldGroupDEEdgeTests(unittest.TestCase):
    """D/E groups: pairing exclusions and the resulting degrade-to status."""

    def test_same_locator_pair_is_excluded_and_reported_as_conflicted(self):
        shared_locator = "https://vendor.test/shared-feed/revenue_ttm"
        left = observation(
            "revenue_ttm", 1000, "USD", "SEC", locator=shared_locator
        )
        right = observation(
            "revenue_ttm", 1000, "USD", "POLYGON", locator=shared_locator
        )
        result = reconcile_field(
            "revenue_ttm",
            [left, right],
            expected_unit="USD",
            as_of=AS_OF,
            max_age_days=130,
        )
        # Locked as characterized (not "correct") behavior: the pair is
        # never actually compared (same locator excludes it in group D),
        # yet with >=2 distinct families and extraction methods the
        # independence check in group E is satisfied, so this reports
        # CONFLICTED rather than NEEDS_EVIDENCE -- even though the values
        # are identical and were never really compared against each other.
        self.assertEqual(result["status"], "CONFLICTED")
        self.assertIsNone(result["record"])

    def test_single_accepted_observation_cannot_be_verified(self):
        result = reconcile_field(
            "current_price",
            [observation("current_price", 100, "USD/share", "YAHOO")],
            expected_unit="USD/share",
            as_of=AS_OF,
            max_age_days=3,
        )
        self.assertEqual(result["status"], "NEEDS_EVIDENCE")
        self.assertIsNone(result["record"])
        self.assertEqual(result["accepted_count"], 1)


class ReconcileFieldGroupFTests(unittest.TestCase):
    """F group: best-pair selection and primary/secondary ordering."""

    def test_best_pair_prefers_top_ranked_source_then_smaller_difference(self):
        # ALPHA (MARKET, rank 0) pairs validly with both BETA (PRIMARY,
        # rank 1) and GAMMA (CONSENSUS, rank 2). Any pair containing ALPHA
        # beats the ALPHA-free pair (BETA, GAMMA) on the rank key alone;
        # between the two ALPHA pairs, the smaller relative difference
        # (ALPHA-BETA, ~0.30%) must win over the larger one (ALPHA-GAMMA,
        # ~0.60%).
        alpha = observation(
            "current_price", 100.0, "USD/share", "ALPHA", source_type="MARKET"
        )
        beta = observation(
            "current_price", 100.3, "USD/share", "BETA", source_type="PRIMARY"
        )
        gamma = observation(
            "current_price", 100.6, "USD/share", "GAMMA", source_type="CONSENSUS"
        )
        result = reconcile_field(
            "current_price",
            [alpha, beta, gamma],
            expected_unit="USD/share",
            as_of=AS_OF,
            max_age_days=3,
        )
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["record"]["source_family"], "ALPHA")
        self.assertEqual(
            result["record"]["verification"]["secondary_source_family"], "BETA"
        )

    def test_best_pair_tiebreaks_by_family_name_when_rank_and_difference_tie(self):
        # ALPHA (MARKET) has an identical relative difference to both ZZZ
        # and AAA (both PRIMARY, both valued at 100.5 vs ALPHA's 100.0) --
        # the (source_rank, difference) key ties exactly between the two
        # candidate pairs. The remaining tie-break compares
        # (pair_left.source_family, pair_right.source_family); since ALPHA
        # is "left" in both candidate pairs (it is admitted first), the
        # decision reduces to "AAA" < "ZZZ", so AAA must be the winning
        # secondary, not ZZZ, even though ZZZ was admitted earlier.
        alpha = observation(
            "current_price", 100.0, "USD/share", "ALPHA", source_type="MARKET"
        )
        zzz = observation(
            "current_price", 100.5, "USD/share", "ZZZ", source_type="PRIMARY"
        )
        aaa = observation(
            "current_price", 100.5, "USD/share", "AAA", source_type="PRIMARY"
        )
        result = reconcile_field(
            "current_price",
            [alpha, zzz, aaa],
            expected_unit="USD/share",
            as_of=AS_OF,
            max_age_days=3,
        )
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["record"]["source_family"], "ALPHA")
        self.assertEqual(
            result["record"]["verification"]["secondary_source_family"], "AAA"
        )

    def test_primary_secondary_ordering_follows_source_rank_not_pair_order(self):
        # BETA (PRIMARY) is admitted first, ALPHA (MARKET) second, so the
        # only pair found has BETA as the tuple's "left" and ALPHA as
        # "right". Primary/secondary must still be decided by source_rank
        # (ALPHA/MARKET wins), not by which one happened to be "left".
        beta = observation(
            "current_price", 100.0, "USD/share", "BETA", source_type="PRIMARY"
        )
        alpha = observation(
            "current_price", 100.3, "USD/share", "ALPHA", source_type="MARKET"
        )
        result = reconcile_field(
            "current_price",
            [beta, alpha],
            expected_unit="USD/share",
            as_of=AS_OF,
            max_age_days=3,
        )
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["record"]["source_family"], "ALPHA")
        self.assertEqual(
            result["record"]["verification"]["secondary_source_family"], "BETA"
        )


if __name__ == "__main__":
    unittest.main()
