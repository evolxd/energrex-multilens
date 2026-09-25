"""Golden characterization tests for scoring/unified_valuation.py's
_field_values_and_quality(), written BEFORE any decomposition.

Unlike the account_monitor.py / bull_put / bull_call extractions earlier
this session, this function is NOT a closure trapped inside something else
-- it's already a pure, independently-callable module-level function (no
IO, no wall-clock, `as_of` is an explicit request field). So these ARE
genuine pre-refactor snapshots of real behavior, run against the CURRENT,
unmodified implementation.

Planned decomposition (Option A, approved 2026-09-24): split into
_check_presence_and_provenance, _check_timestamps,
_check_verification_status, _check_cross_check -- the cross-check block
(14 of the 29 reason codes below) stays ONE function, not further split,
specifically to avoid threading intermediate values (source_family,
origin_family, ...) through smaller pieces and to keep its own internal
early-exit (CROSS_CHECK_MISSING) intact without extra plumbing.

Coverage: all 29 distinct DQ reason codes the function can produce, each
isolated to exactly one broken input (proving the checks are independent,
not accidentally coupled), plus:
  - two "early exit" cases (MISSING, CROSS_CHECK_MISSING) that must
    suppress every reason that would otherwise follow them in the same
    field/branch,
  - one "independent reasons co-occur" case (checks in different sections
    fire together when both are broken),
  - the cross_check_mode elif-chain's mutual exclusivity (each mode test
    below asserts a SINGLE reason, which is only possible if the elif
    chain stops at the first branch that matches -- this is the same proof
    as the early-exit cases, just for a different control-flow shape),
  - aggregate status/validity_rate/critical_veto/missing_or_invalid_fields.

Known finding, not touched here (out of scope -- flagged for the user):
none of the four ValuationProfile field counts (3/4/5/5) can ever produce a
validity_rate in [0.85, 0.95), so the "PARTIAL" status is currently
unreachable in production. That's a pre-existing business-logic property,
not something this structural refactor should change.

Fixture note: deliberately NOT importing tests/test_unified_valuation.py's
`field()` helper (copied-and-trimmed version here instead, approved
2026-09-24) -- this file is refactoring production code, and keeping the
two test files' fixtures independent avoids coupling one refactor's test
infrastructure to another's.
"""
import unittest

from scoring.unified_valuation import (
    FIELD_CROSSCHECK_LIMITS,
    FIELD_CROSSCHECK_MODES,
    PROFILE_SPECS,
    ValuationProfile,
    _field_values_and_quality,
)

AS_OF = "2026-07-24T20:00:00+00:00"
_TS = "2026-07-24T20:00:00+00:00"


def _spec_for(name: str):
    for specs in PROFILE_SPECS.values():
        if name in specs:
            return specs[name]
    raise KeyError(name)


def _valid_field(name: str, value: float = 100.0) -> dict:
    """A field record that passes every check _field_values_and_quality
    runs for `name`, using that field's real required unit / cross-check
    mode / tolerance from the module's own constants (not re-typed here,
    so this fixture can't drift out of sync with them)."""
    spec = _spec_for(name)
    return {
        "value": value,
        "unit": spec.unit,
        "source": "fixture",
        "source_type": "PRIMARY",
        "source_family": "PRIMARY_FAMILY",
        "origin_family": "PRIMARY_ORIGIN",
        "lineage_id": f"lineage-{name}",
        "source_locator": f"locator://{name}",
        "extraction_method": "primary-extraction",
        "observed_at": _TS,
        "available_at": _TS,
        "retrieved_at": _TS,
        "verification": {
            "status": "VERIFIED",
            "secondary_source": "independent fixture",
            "secondary_source_family": "SECONDARY_FAMILY",
            "secondary_origin_family": "SECONDARY_ORIGIN",
            "secondary_lineage_id": f"lineage-secondary-{name}",
            "secondary_source_locator": f"locator://secondary/{name}",
            "secondary_extraction_method": "secondary-extraction",
            "secondary_available_at": _TS,
            "secondary_retrieved_at": _TS,
            "relative_difference": 0.0,
            "tolerance": FIELD_CROSSCHECK_LIMITS[name],
            "cross_check_mode": FIELD_CROSSCHECK_MODES[name],
        },
    }


def _evaluate(
    profile: ValuationProfile,
    field: str,
    *,
    field_overrides: dict | None = None,
    verification_overrides: dict | None = None,
    delete_field: bool = False,
):
    """Builds a request where every field required by `profile` is fully
    valid except `field`, applies the given override(s) to it, and returns
    (reasons_for_field, full_quality_dict)."""
    fields = {name: _valid_field(name) for name in PROFILE_SPECS[profile]}
    if delete_field:
        del fields[field]
    else:
        if field_overrides:
            fields[field] = {**fields[field], **field_overrides}
        if verification_overrides:
            fields[field]["verification"] = {
                **fields[field]["verification"],
                **verification_overrides,
            }
    request = {"fields": fields, "as_of": AS_OF}
    _, quality = _field_values_and_quality(request, profile)
    return quality["field_results"][field]["reasons"], quality


def _others_all_valid(quality, profile: ValuationProfile, field: str) -> bool:
    return all(
        quality["field_results"][name]["valid"]
        for name in PROFILE_SPECS[profile]
        if name != field
    )


PROFILE = ValuationProfile.FINANCIAL  # 3 fields: current_price (ORIGIN mode),
# diluted_shares / book_value_per_share (EXTRACTION mode) -- smallest
# profile, and covers both cross-check modes.


class PresenceAndProvenanceTests(unittest.TestCase):
    def test_missing_field_produces_only_missing(self):
        reasons, quality = _evaluate(PROFILE, "current_price", delete_field=True)
        self.assertEqual(reasons, ["MISSING"])
        self.assertTrue(_others_all_valid(quality, PROFILE, "current_price"))

    def test_invalid_value(self):
        reasons, _ = _evaluate(PROFILE, "current_price", field_overrides={"value": "not-a-number"})
        self.assertEqual(reasons, ["INVALID_VALUE"])

    def test_unit_mismatch(self):
        reasons, _ = _evaluate(PROFILE, "current_price", field_overrides={"unit": "EUR"})
        self.assertEqual(reasons, ["UNIT_MISMATCH"])

    def test_source_missing(self):
        reasons, _ = _evaluate(PROFILE, "current_price", field_overrides={"source": "   "})
        self.assertEqual(reasons, ["SOURCE_MISSING"])

    def test_source_family_missing(self):
        reasons, _ = _evaluate(PROFILE, "current_price", field_overrides={"source_family": ""})
        self.assertEqual(reasons, ["SOURCE_FAMILY_MISSING"])

    def test_origin_family_missing(self):
        reasons, _ = _evaluate(PROFILE, "current_price", field_overrides={"origin_family": ""})
        self.assertEqual(reasons, ["ORIGIN_FAMILY_MISSING"])

    def test_lineage_id_missing(self):
        reasons, _ = _evaluate(PROFILE, "current_price", field_overrides={"lineage_id": ""})
        self.assertEqual(reasons, ["LINEAGE_ID_MISSING"])

    def test_source_locator_missing(self):
        reasons, _ = _evaluate(PROFILE, "current_price", field_overrides={"source_locator": ""})
        self.assertEqual(reasons, ["SOURCE_LOCATOR_MISSING"])

    def test_source_type_invalid(self):
        reasons, _ = _evaluate(PROFILE, "current_price", field_overrides={"source_type": "BOGUS"})
        self.assertEqual(reasons, ["SOURCE_TYPE_INVALID"])


class TimestampTests(unittest.TestCase):
    def test_timestamp_invalid(self):
        reasons, _ = _evaluate(PROFILE, "current_price", field_overrides={"observed_at": "not-a-date"})
        self.assertEqual(reasons, ["TIMESTAMP_INVALID"])

    def test_observed_after_available(self):
        reasons, _ = _evaluate(PROFILE, "current_price", field_overrides={
            "observed_at": "2026-07-24T20:00:00+00:00",
            "available_at": "2026-07-23T20:00:00+00:00",
            "retrieved_at": "2026-07-24T20:00:00+00:00",
        })
        self.assertEqual(reasons, ["OBSERVED_AFTER_AVAILABLE"])

    def test_available_after_retrieved(self):
        reasons, _ = _evaluate(PROFILE, "current_price", field_overrides={
            "observed_at": "2026-07-22T20:00:00+00:00",
            "available_at": "2026-07-24T20:00:00+00:00",
            "retrieved_at": "2026-07-23T20:00:00+00:00",
        })
        self.assertEqual(reasons, ["AVAILABLE_AFTER_RETRIEVED"])

    def test_lookahead(self):
        reasons, _ = _evaluate(PROFILE, "current_price", field_overrides={
            "observed_at": "2026-07-25T20:00:00+00:00",
            "available_at": "2026-07-25T20:00:00+00:00",
            "retrieved_at": "2026-07-25T20:00:00+00:00",
        })
        self.assertEqual(reasons, ["LOOKAHEAD"])

    def test_stale(self):
        # current_price's max_age_days is 3; 14 days before as_of exceeds it.
        reasons, _ = _evaluate(PROFILE, "current_price", field_overrides={
            "observed_at": "2026-07-10T20:00:00+00:00",
            "available_at": "2026-07-10T20:00:00+00:00",
            "retrieved_at": "2026-07-10T20:00:00+00:00",
        })
        self.assertEqual(reasons, ["STALE"])


class VerificationStatusTests(unittest.TestCase):
    def test_unverified(self):
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={"status": "PENDING"})
        self.assertEqual(reasons, ["UNVERIFIED"])


class CrossCheckTests(unittest.TestCase):
    def test_cross_check_missing_suppresses_every_later_cross_check_reason(self):
        """Early-exit gate: an empty secondary_source skips the whole
        `else` branch, so none of the other 13 cross-check reason codes can
        also fire even though this fixture's verification dict still has
        (now-irrelevant) values for lineage/mode/etc."""
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={"secondary_source": ""})
        self.assertEqual(reasons, ["CROSS_CHECK_MISSING"])

    def test_cross_check_not_independent(self):
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={
            "secondary_source_family": "PRIMARY_FAMILY",  # == source_family
        })
        self.assertEqual(reasons, ["CROSS_CHECK_NOT_INDEPENDENT"])

    def test_cross_check_locator_not_independent(self):
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={
            "secondary_source_locator": "locator://current_price",  # == source_locator
        })
        self.assertEqual(reasons, ["CROSS_CHECK_LOCATOR_NOT_INDEPENDENT"])

    def test_cross_check_lineage_missing_via_empty_origin(self):
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={"secondary_origin_family": ""})
        self.assertEqual(reasons, ["CROSS_CHECK_LINEAGE_MISSING"])

    def test_cross_check_lineage_missing_via_empty_lineage_id(self):
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={"secondary_lineage_id": ""})
        self.assertEqual(reasons, ["CROSS_CHECK_LINEAGE_MISSING"])

    def test_cross_check_mode_invalid(self):
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={"cross_check_mode": "BOGUS"})
        self.assertEqual(reasons, ["CROSS_CHECK_MODE_INVALID"])

    def test_cross_check_mode_mismatch_excludes_origin_and_extraction_checks(self):
        """current_price requires INDEPENDENT_ORIGIN; a validly-spelled but
        wrong mode must stop at MISMATCH -- the elif chain never reaches
        the origin/extraction independence checks below it."""
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={
            "cross_check_mode": "INDEPENDENT_EXTRACTION",
        })
        self.assertEqual(reasons, ["CROSS_CHECK_MODE_MISMATCH"])

    def test_cross_check_origin_not_independent(self):
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={
            "secondary_origin_family": "PRIMARY_ORIGIN",  # == origin_family
        })
        self.assertEqual(reasons, ["CROSS_CHECK_ORIGIN_NOT_INDEPENDENT"])

    def test_cross_check_extraction_not_independent_same_method(self):
        reasons, _ = _evaluate(PROFILE, "diluted_shares", verification_overrides={
            "secondary_extraction_method": "primary-extraction",  # == extraction_method
        })
        self.assertEqual(reasons, ["CROSS_CHECK_EXTRACTION_NOT_INDEPENDENT"])

    def test_cross_check_extraction_not_independent_empty_method(self):
        reasons, _ = _evaluate(PROFILE, "diluted_shares", verification_overrides={
            "secondary_extraction_method": "",
        })
        self.assertEqual(reasons, ["CROSS_CHECK_EXTRACTION_NOT_INDEPENDENT"])

    def test_secondary_timestamp_invalid(self):
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={
            "secondary_available_at": "not-a-date",
        })
        self.assertEqual(reasons, ["SECONDARY_TIMESTAMP_INVALID"])

    def test_secondary_available_after_retrieved(self):
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={
            "secondary_available_at": "2026-07-24T20:00:00+00:00",
            "secondary_retrieved_at": "2026-07-23T20:00:00+00:00",
        })
        self.assertEqual(reasons, ["SECONDARY_AVAILABLE_AFTER_RETRIEVED"])

    def test_secondary_lookahead(self):
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={
            "secondary_available_at": "2026-07-25T20:00:00+00:00",
            "secondary_retrieved_at": "2026-07-25T20:00:00+00:00",
        })
        self.assertEqual(reasons, ["SECONDARY_LOOKAHEAD"])

    def test_cross_check_metrics_invalid(self):
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={"tolerance": -1})
        self.assertEqual(reasons, ["CROSS_CHECK_METRICS_INVALID"])

    def test_cross_check_tolerance_too_wide(self):
        # current_price's tolerance limit is 0.01; difference stays at 0.0
        # so CROSS_CHECK_CONFLICT must NOT also fire.
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={"tolerance": 0.5})
        self.assertEqual(reasons, ["CROSS_CHECK_TOLERANCE_TOO_WIDE"])

    def test_cross_check_conflict(self):
        # tolerance stays within the 0.01 limit, only the observed
        # difference exceeds it -- TOLERANCE_TOO_WIDE must NOT also fire.
        reasons, _ = _evaluate(PROFILE, "current_price", verification_overrides={
            "tolerance": 0.01, "relative_difference": 0.5,
        })
        self.assertEqual(reasons, ["CROSS_CHECK_CONFLICT"])


class CooccurrenceTests(unittest.TestCase):
    def test_independent_reasons_from_different_sections_coexist(self):
        """UNIT_MISMATCH (provenance section) and UNVERIFIED (verification
        section) are separate `if`s, not elif -- both must appear, in
        source order."""
        reasons, _ = _evaluate(
            PROFILE, "current_price",
            field_overrides={"unit": "EUR"},
            verification_overrides={"status": "PENDING"},
        )
        self.assertEqual(reasons, ["UNIT_MISMATCH", "UNVERIFIED"])


class AggregateStatusTests(unittest.TestCase):
    def test_all_valid_is_pass_with_full_validity_and_no_veto(self):
        request = {"fields": {name: _valid_field(name) for name in PROFILE_SPECS[PROFILE]}, "as_of": AS_OF}
        values, quality = _field_values_and_quality(request, PROFILE)
        self.assertEqual(quality["status"], "PASS")
        self.assertEqual(quality["validity_rate"], 1.0)
        self.assertFalse(quality["critical_veto"])
        self.assertEqual(quality["missing_or_invalid_fields"], [])
        self.assertEqual(set(values), set(PROFILE_SPECS[PROFILE]))

    def test_one_invalid_field_forces_review_required_and_veto(self):
        _, quality = _evaluate(PROFILE, "current_price", delete_field=True)
        self.assertEqual(quality["status"], "REVIEW_REQUIRED")
        self.assertAlmostEqual(quality["validity_rate"], 2 / 3, places=4)
        self.assertTrue(quality["critical_veto"])
        self.assertEqual(quality["missing_or_invalid_fields"], ["current_price"])


if __name__ == "__main__":
    unittest.main()
