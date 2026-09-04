import datetime as dt
import unittest

from scoring.valuation_source_adapters import collect_current_price_evidence


AS_OF = dt.datetime(2026, 7, 24, 20, 0, tzinfo=dt.timezone.utc)


def price_observation(family, value, *, locator=None):
    return {
        "field": "current_price",
        "value": value,
        "unit": "USD/share",
        "source": f"{family} quote",
        "source_type": "MARKET",
        "source_family": family,
        "origin_family": f"{family}_ORIGIN",
        "lineage_id": f"{family}:TEST:2026-07-24T19:00:00Z",
        "source_locator": locator or f"https://example.test/{family}/TEST",
        "observed_at": "2026-07-24T19:00:00+00:00",
        "available_at": "2026-07-24T19:00:00+00:00",
        "retrieved_at": "2026-07-24T19:01:00+00:00",
        "extraction_method": "test quote",
    }


class ValuationSourceAdapterTests(unittest.TestCase):
    def test_two_real_provider_families_verify_price(self):
        result = collect_current_price_evidence(
            "test",
            fetchers={
                "YAHOO_FINANCE": lambda _: price_observation(
                    "YAHOO_FINANCE",
                    100.0,
                ),
                "POLYGON": lambda _: price_observation("POLYGON", 100.4),
            },
            as_of=AS_OF,
        )
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["record"]["unit"], "USD/share")
        self.assertEqual(len(result["observations"]), 2)

    def test_provider_cannot_claim_a_different_configured_family(self):
        result = collect_current_price_evidence(
            "TEST",
            fetchers={
                "WRAPPER_A": lambda _: price_observation("YAHOO_FINANCE", 100),
                "WRAPPER_B": lambda _: price_observation(
                    "YAHOO_FINANCE",
                    100,
                    locator="https://other-wrapper.test/yahoo",
                ),
            },
            as_of=AS_OF,
        )
        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(len(result["observations"]), 0)
        self.assertTrue(
            all(item["status"] == "INVALID" for item in result["providers"])
        )

    def test_one_unavailable_provider_does_not_default_to_verified(self):
        result = collect_current_price_evidence(
            "TEST",
            fetchers={
                "YAHOO_FINANCE": lambda _: price_observation(
                    "YAHOO_FINANCE",
                    100,
                ),
                "POLYGON": lambda _: {"_evidence_error": "API key missing"},
            },
            as_of=AS_OF,
        )
        self.assertEqual(result["status"], "NEEDS_EVIDENCE")
        self.assertIsNone(result["record"])

    def test_disagreement_is_exposed_not_averaged(self):
        result = collect_current_price_evidence(
            "TEST",
            fetchers={
                "YAHOO_FINANCE": lambda _: price_observation(
                    "YAHOO_FINANCE",
                    100,
                ),
                "POLYGON": lambda _: price_observation("POLYGON", 110),
            },
            as_of=AS_OF,
        )
        self.assertEqual(result["status"], "CONFLICTED")
        self.assertIsNone(result["record"])

    def test_family_name_is_normalized_but_not_rewritten(self):
        result = collect_current_price_evidence(
            "TEST",
            fetchers={
                "yahoo_finance": lambda _: price_observation(
                    "YAHOO_FINANCE",
                    100,
                ),
                "polygon": lambda _: price_observation("POLYGON", 100),
            },
            as_of=AS_OF,
        )
        self.assertEqual(result["status"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()
