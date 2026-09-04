import copy
import unittest

from scoring.sec_fundamental_evidence import (
    extract_sec_companyfacts_observations,
    issuer_report_observation,
)
from scoring.valuation_evidence_pipeline import reconcile_field


AS_OF = "2026-01-01T12:00:00+00:00"
RETRIEVED = "2025-12-31T12:00:00+00:00"


def fact(val, start, end, accn, *, form="10-Q"):
    result = {"val": val, "end": end, "accn": accn, "form": form}
    if start is not None:
        result["start"] = start
    return result


def fixture():
    accessions = ["0001-25-000001", "0001-25-000002", "0001-25-000003", "0001-25-000004"]
    accepted = [
        "2025-02-10T16:00:00+00:00",
        "2025-05-10T16:00:00+00:00",
        "2025-08-10T16:00:00+00:00",
        "2025-11-10T16:00:00+00:00",
    ]
    quarters = [
        ("2024-10-01", "2024-12-31", 100),
        ("2025-01-01", "2025-03-31", 110),
        ("2025-04-01", "2025-06-30", 120),
        ("2025-07-01", "2025-09-30", 130),
    ]
    revenue = [
        fact(value, start, end, accn)
        for (start, end, value), accn in zip(quarters, accessions)
    ]
    # A six-month cumulative value must never be counted as a discrete quarter.
    revenue.append(
        fact(230, "2025-01-01", "2025-06-30", "0001-25-000003")
    )
    # A later restatement accepted after the point-in-time cutoff must be ignored.
    revenue.append(
        fact(999, "2025-07-01", "2025-09-30", "0001-26-000001")
    )
    shares = [
        fact(50, start, end, accn)
        for (start, end, _), accn in zip(quarters, accessions)
    ]
    net_debt = [
        fact(-20, None, "2025-09-30", "0001-25-000004"),
        fact(-999, None, "2025-09-30", "0001-26-000001"),
    ]
    companyfacts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {"USD": revenue}
                },
                "WeightedAverageNumberOfDilutedSharesOutstanding": {
                    "units": {"shares": shares}
                },
                "NetDebt": {"units": {"USD": net_debt}},
            }
        }
    }
    submissions = {
        "filings": {
            "recent": {
                "accessionNumber": accessions + ["0001-26-000001"],
                "acceptanceDateTime": accepted + ["2026-02-10T16:00:00+00:00"],
            }
        }
    }
    return companyfacts, submissions


class SecFundamentalEvidenceTests(unittest.TestCase):
    def test_extracts_only_point_in_time_discrete_facts(self):
        companyfacts, submissions = fixture()
        result = extract_sec_companyfacts_observations(
            ticker="TEST",
            cik="0000000001",
            companyfacts=companyfacts,
            submissions=submissions,
            as_of=AS_OF,
            retrieved_at=RETRIEVED,
        )
        by_field = {item["field"]: item for item in result["observations"]}
        self.assertEqual(result["status"], "EXTRACTED")
        self.assertEqual(by_field["revenue_ttm"]["value"], 460)
        self.assertEqual(by_field["diluted_shares"]["value"], 50)
        self.assertEqual(by_field["net_cash"]["value"], 20)
        self.assertEqual(
            by_field["revenue_ttm"]["origin_family"],
            "ISSUER_REPORTED_FINANCIALS",
        )

    def test_missing_discrete_quarter_blocks_revenue_instead_of_deriving_it(self):
        companyfacts, submissions = fixture()
        revenue = companyfacts["facts"]["us-gaap"][
            "RevenueFromContractWithCustomerExcludingAssessedTax"
        ]["units"]["USD"]
        revenue.pop(0)
        result = extract_sec_companyfacts_observations(
            ticker="TEST",
            cik="0000000001",
            companyfacts=companyfacts,
            submissions=submissions,
            as_of=AS_OF,
            retrieved_at=RETRIEVED,
        )
        self.assertEqual(result["status"], "PARTIAL")
        self.assertNotIn(
            "revenue_ttm",
            {item["field"] for item in result["observations"]},
        )

    def test_reported_fiscal_year_is_valid_ttm_evidence(self):
        companyfacts, submissions = fixture()
        submissions["filings"]["recent"]["acceptanceDateTime"][3] = (
            "2026-02-10T16:00:00+00:00"
        )
        revenue = companyfacts["facts"]["us-gaap"][
            "RevenueFromContractWithCustomerExcludingAssessedTax"
        ]["units"]["USD"]
        revenue[:] = [
            fact(
                500,
                "2025-01-01",
                "2025-12-31",
                "0001-25-000004",
                form="20-F",
            )
        ]
        result = extract_sec_companyfacts_observations(
            ticker="TEST",
            cik="0000000001",
            companyfacts=companyfacts,
            submissions=submissions,
            as_of="2026-02-12T12:00:00+00:00",
            retrieved_at="2026-02-11T12:00:00+00:00",
        )
        by_field = {item["field"]: item for item in result["observations"]}
        self.assertEqual(by_field["revenue_ttm"]["value"], 500)
        self.assertIn("fiscal-year revenue used as TTM", by_field["revenue_ttm"]["extraction_method"])

    def test_q4_can_be_derived_only_from_fiscal_year_minus_nine_months(self):
        companyfacts, submissions = fixture()
        submissions["filings"]["recent"]["acceptanceDateTime"][3] = (
            "2026-02-10T16:00:00+00:00"
        )
        revenue = companyfacts["facts"]["us-gaap"][
            "RevenueFromContractWithCustomerExcludingAssessedTax"
        ]["units"]["USD"]
        revenue[:] = [
            fact(100, "2024-10-01", "2024-12-31", "0001-25-000001"),
            fact(110, "2025-01-01", "2025-03-31", "0001-25-000002"),
            fact(120, "2025-04-01", "2025-06-30", "0001-25-000003"),
            fact(130, "2025-07-01", "2025-09-30", "0001-25-000004"),
            fact(360, "2025-01-01", "2025-09-30", "0001-25-000004"),
            fact(500, "2025-01-01", "2025-12-31", "0001-25-000004", form="10-K"),
        ]
        result = extract_sec_companyfacts_observations(
            ticker="TEST",
            cik="0000000001",
            companyfacts=companyfacts,
            submissions=submissions,
            as_of="2026-02-12T12:00:00+00:00",
            retrieved_at="2026-02-11T12:00:00+00:00",
        )
        revenue_observation = next(
            item for item in result["observations"] if item["field"] == "revenue_ttm"
        )
        # Latest TTM is Q1 + Q2 + Q3 + derived Q4 (500 - 360).
        self.assertEqual(revenue_observation["value"], 500)
        self.assertIn("Q4 derived", revenue_observation["extraction_method"])

    def test_futu_diluted_shares_are_normalized_to_ads_equivalent(self):
        companyfacts, submissions = fixture()
        shares = companyfacts["facts"]["us-gaap"][
            "WeightedAverageNumberOfDilutedSharesOutstanding"
        ]["units"]["shares"]
        shares[-1]["val"] = 1_120_000_000
        result = extract_sec_companyfacts_observations(
            ticker="FUTU",
            cik="0001754581",
            companyfacts=companyfacts,
            submissions=submissions,
            as_of=AS_OF,
            retrieved_at=RETRIEVED,
        )
        share_observation = next(
            item for item in result["observations"] if item["field"] == "diluted_shares"
        )
        self.assertEqual(share_observation["value"], 140_000_000)
        self.assertIn("8 ordinary shares per ADS", share_observation["extraction_method"])
        self.assertTrue(share_observation["source_locator"].startswith("https://www.sec.gov/"))

    def test_native_currency_is_not_silently_treated_as_usd(self):
        companyfacts, submissions = fixture()
        revenue_tag = companyfacts["facts"]["us-gaap"][
            "RevenueFromContractWithCustomerExcludingAssessedTax"
        ]
        revenue_tag["units"] = {
            "HKD": [
                fact(
                    22_800_000_000,
                    "2025-01-01",
                    "2025-12-31",
                    "0001-25-000004",
                    form="20-F",
                )
            ]
        }
        result = extract_sec_companyfacts_observations(
            ticker="FUTU",
            cik="0001754581",
            companyfacts=companyfacts,
            submissions=submissions,
            as_of=AS_OF,
            retrieved_at=RETRIEVED,
        )
        self.assertNotIn(
            "revenue_ttm",
            {item["field"] for item in result["observations"]},
        )
        self.assertTrue(any("explicit FX normalization" in issue for issue in result["issues"]))

    def test_retrieval_after_cutoff_is_rejected(self):
        companyfacts, submissions = fixture()
        with self.assertRaisesRegex(ValueError, "retrieved_at"):
            extract_sec_companyfacts_observations(
                ticker="TEST",
                cik="0000000001",
                companyfacts=companyfacts,
                submissions=submissions,
                as_of=AS_OF,
                retrieved_at="2026-01-02T12:00:00+00:00",
            )

    def test_sec_and_issuer_report_are_independent_extractions_not_origins(self):
        companyfacts, submissions = fixture()
        sec = extract_sec_companyfacts_observations(
            ticker="TEST",
            cik="0000000001",
            companyfacts=companyfacts,
            submissions=submissions,
            as_of=AS_OF,
            retrieved_at=RETRIEVED,
        )
        sec_revenue = next(
            item for item in sec["observations"] if item["field"] == "revenue_ttm"
        )
        issuer = issuer_report_observation(
            ticker="TEST",
            field="revenue_ttm",
            value=460,
            unit="USD",
            period_end="2025-09-30",
            published_at="2025-11-09T12:00:00+00:00",
            retrieved_at=RETRIEVED,
            report_id="2025-Q3",
            source_locator="https://ir.example.test/2025-q3.pdf",
            extraction_method="issuer PDF table extraction",
        )
        reconciled = reconcile_field(
            "revenue_ttm",
            [sec_revenue, issuer],
            expected_unit="USD",
            as_of=AS_OF,
            max_age_days=130,
        )
        self.assertEqual(reconciled["status"], "VERIFIED")
        self.assertEqual(
            reconciled["record"]["verification"]["cross_check_mode"],
            "INDEPENDENT_EXTRACTION",
        )
        self.assertEqual(
            reconciled["record"]["origin_family"],
            reconciled["record"]["verification"]["secondary_origin_family"],
        )

    def test_same_extraction_method_does_not_count_twice(self):
        companyfacts, submissions = fixture()
        sec = extract_sec_companyfacts_observations(
            ticker="TEST",
            cik="0000000001",
            companyfacts=companyfacts,
            submissions=submissions,
            as_of=AS_OF,
            retrieved_at=RETRIEVED,
        )
        sec_revenue = next(
            item for item in sec["observations"] if item["field"] == "revenue_ttm"
        )
        issuer = issuer_report_observation(
            ticker="TEST",
            field="revenue_ttm",
            value=460,
            unit="USD",
            period_end="2025-09-30",
            published_at="2025-11-09T12:00:00+00:00",
            retrieved_at=RETRIEVED,
            report_id="2025-Q3",
            source_locator="https://ir.example.test/2025-q3.pdf",
            extraction_method=sec_revenue["extraction_method"],
        )
        reconciled = reconcile_field(
            "revenue_ttm",
            [sec_revenue, issuer],
            expected_unit="USD",
            as_of=AS_OF,
            max_age_days=130,
        )
        self.assertEqual(reconciled["status"], "NEEDS_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
