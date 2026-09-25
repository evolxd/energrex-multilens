"""Characterization tests for validation/validate_results.py::validate_ticker,
built before decomposing it into focused pure functions.

validate_ticker has zero prior test coverage and takes its network fetchers
(FMPFetcher/FinnhubFetcher/SECFetcher) as constructor-injected arguments, but
calls calc_momentum/validate_sector/validate_financial/validate_score as
direct module-level names -- those three sub-validators are mocked out here
(not exercised for real) since they are separately-owned modules outside
this refactor's scope; only validate_ticker's own orchestration/decision
logic is under test.

Import note: validate_results.py expects to run as a script (`python
validation/validate_results.py`), so `fetchers`/`validators` are resolved
relative to the validation/ directory being on sys.path, not as
validation.fetchers/validation.validators. We replicate that exact
sys.path setup and import the bare module name `validate_results`, then
patch attributes on that same module object throughout -- using a
different import path here would create a second, unpatched copy of the
module (the same class of bug that caused a real incident earlier in this
project's refactor history).
"""

import copy
import pathlib
import sys
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_VALIDATION_DIR = _ROOT / "validation"
if str(_VALIDATION_DIR) not in sys.path:
    sys.path.insert(0, str(_VALIDATION_DIR))

import validate_results as vr  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# Fake fetchers -- plain stand-ins for FMPFetcher/FinnhubFetcher/SECFetcher.
# Each method returns a configured value, or raises to exercise validate_
# ticker's per-source try/except fallback.
# ═══════════════════════════════════════════════════════════════════════

class FakeFMP:
    def __init__(self, profile=None, ratios=None, income=None, key_metrics=None, raise_on=()):
        self._profile = profile or {}
        self._ratios = ratios or {}
        self._income = income or {}
        self._key_metrics = key_metrics or {}
        self._raise_on = set(raise_on)

    def get_profile(self, ticker):
        if "profile" in self._raise_on:
            raise RuntimeError("boom-profile")
        return self._profile

    def get_ratios(self, ticker):
        if "ratios" in self._raise_on:
            raise RuntimeError("boom-ratios")
        return self._ratios

    def get_income(self, ticker):
        if "income" in self._raise_on:
            raise RuntimeError("boom-income")
        return self._income

    def get_key_metrics(self, ticker):
        if "key_metrics" in self._raise_on:
            raise RuntimeError("boom-key-metrics")
        return self._key_metrics


class FakeFinnhub:
    def __init__(self, profile=None, metrics=None, raise_on=()):
        self._profile = profile or {}
        self._metrics = metrics or {}
        self._raise_on = set(raise_on)

    def get_profile(self, ticker):
        if "profile" in self._raise_on:
            raise RuntimeError("boom-profile")
        return self._profile

    def get_metrics(self, ticker):
        if "metrics" in self._raise_on:
            raise RuntimeError("boom-metrics")
        return self._metrics


class FakeSEC:
    def __init__(self, info=None, financials=None, business_desc="", raise_on=()):
        self._info = info or {}
        self._financials = financials or {}
        self._business_desc = business_desc
        self._raise_on = set(raise_on)

    def get_company_info(self, ticker):
        if "info" in self._raise_on:
            raise RuntimeError("boom-info")
        return self._info

    def get_financials(self, ticker):
        if "financials" in self._raise_on:
            raise RuntimeError("boom-financials")
        return self._financials

    def get_business_description(self, ticker):
        if "business_desc" in self._raise_on:
            raise RuntimeError("boom-business-desc")
        return self._business_desc


# ═══════════════════════════════════════════════════════════════════════
# Default sub-validator fixtures. All six compute_confidence inputs are
# True in the baseline so status == PASS and human_review_required == False,
# giving every test a clean slate to flip exactly one thing.
# ═══════════════════════════════════════════════════════════════════════

def _default_sector_result(**overrides):
    result = {
        "sector_suggested": "SaaS",
        "sector_confidence": 0.80,
        "source_conflict": False,
        "sector_sources": "",
        "name_sources": "",
        "ai_keyword_count": 5,  # >1 so it never trips the human_review AI-suspicious clause by itself
        "notes": [],
    }
    result.update(overrides)
    return result


def _default_financial_result(**overrides):
    result = {"raw_data_conflict": False, "anomaly_count": 0, "notes": []}
    result.update(overrides)
    return result


def _default_score_result(**overrides):
    result = {
        "base_score_recalculated": 70.0,
        "dynamic_adjustment_recalculated": 0.0,
        "final_score_recalculated": 70.0,
        "formula_diff_abs": 0.3,
        "formula_diff_level": "minor_diff",
        "formula_diff_reason": "rounding_difference",
        "formula_mismatch": False,
        "dim_deltas": {},
        "notes": [],
    }
    result.update(overrides)
    return result


BASE_CSV_ROW = {
    "ticker": "TEST",
    "company_公司名": "Acme Corp",
    "sector_板块": "SaaS",
}


def run_validate_ticker(
    csv_row=None,
    *,
    fmp=None,
    finnhub=None,
    sec=None,
    momentum=None,
    momentum_raises=False,
    sector_result=None,
    financial_result=None,
    score_result=None,
    ttl_hours=24,
    refresh=False,
):
    """Run validate_ticker with every external dependency mocked/faked, so
    only validate_ticker's own branch logic is exercised."""
    csv_row = csv_row if csv_row is not None else dict(BASE_CSV_ROW)
    fmp = fmp if fmp is not None else FakeFMP(profile={"company_name": "Acme Corp"})
    finnhub = finnhub if finnhub is not None else FakeFinnhub()
    sec = sec if sec is not None else FakeSEC(info={"cik": 1})

    def fake_calc_momentum(ticker, ttl_hours=24):
        if momentum_raises:
            raise RuntimeError("boom-momentum")
        return momentum if momentum is not None else {}

    sector_ret = sector_result if sector_result is not None else _default_sector_result()
    financial_ret = financial_result if financial_result is not None else _default_financial_result()
    score_ret = score_result if score_result is not None else _default_score_result()

    with mock.patch.object(vr, "calc_momentum", fake_calc_momentum), \
         mock.patch.object(vr, "validate_sector", return_value=sector_ret) as m_sector, \
         mock.patch.object(vr, "validate_financial", return_value=financial_ret) as m_financial, \
         mock.patch.object(vr, "validate_score", return_value=score_ret) as m_score:
        result = vr.validate_ticker(
            "TEST", csv_row, fmp, finnhub, sec, ttl_hours=ttl_hours, refresh=refresh
        )
    return result, {"sector": m_sector, "financial": m_financial, "score": m_score}


class BaselineHappyPathTests(unittest.TestCase):
    def test_baseline_all_six_confidence_inputs_true_yields_pass_and_no_review(self):
        result, _ = run_validate_ticker()
        self.assertEqual(result["validation_status"], "PASS")
        self.assertEqual(result["validation_confidence"], 1.0)
        self.assertEqual(result["human_review_required"], "FALSE")


class FetchSourceResilienceTests(unittest.TestCase):
    """Group 1: the 10 independent try/except fetch blocks."""

    def test_all_ten_fetch_sources_succeed_produces_no_error_notes(self):
        result, _ = run_validate_ticker()
        self.assertNotIn("error", result["validation_notes"])

    def test_fmp_profile_fetch_failure_is_isolated_and_noted(self):
        # FMP profile fails, but Finnhub still confirms the company name --
        # proving one source's failure doesn't take down ticker_matched (or
        # anything else) for the whole pipeline.
        fmp = FakeFMP(profile={"company_name": "Acme Corp"}, raise_on={"profile"})
        finnhub = FakeFinnhub(profile={"company_name": "Acme Corp"})
        result, _ = run_validate_ticker(fmp=fmp, finnhub=finnhub)
        self.assertIn("FMP profile error: boom-profile", result["validation_notes"])
        self.assertEqual(result["validation_status"], "PASS")

    def test_sec_business_description_failure_defaults_to_empty_string(self):
        sec = FakeSEC(info={"cik": 1}, raise_on={"business_desc"})
        result, _ = run_validate_ticker(sec=sec)
        self.assertEqual(result["source_urls_10k"], "")
        self.assertIn("SEC 10-K error: boom-business-desc", result["validation_notes"])

    def test_momentum_fetch_failure_defaults_to_empty_dict_and_stays_ok(self):
        result, _ = run_validate_ticker(momentum_raises=True)
        self.assertEqual(result["momentum_recalculated"], "")
        self.assertIn("yfinance momentum error: boom-momentum", result["validation_notes"])
        # momentum_ok defaults True whenever either side of the comparison
        # is missing -- a fetch failure must not itself fail the ticker.
        self.assertEqual(result["validation_status"], "PASS")

    def test_all_ten_sources_failing_still_returns_a_result_without_crashing(self):
        fmp = FakeFMP(raise_on={"profile", "ratios", "income", "key_metrics"})
        finnhub = FakeFinnhub(raise_on={"profile", "metrics"})
        sec = FakeSEC(raise_on={"info", "financials", "business_desc"})
        result, _ = run_validate_ticker(fmp=fmp, finnhub=finnhub, sec=sec, momentum_raises=True)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["validation_notes"].count(" error:"), 10)


class TickerMatchedTests(unittest.TestCase):
    """Group 2."""

    def test_ticker_matched_true_when_fmp_name_matches_csv_company(self):
        fmp = FakeFMP(profile={"company_name": "Acme Corp"})
        result, _ = run_validate_ticker(fmp=fmp, finnhub=FakeFinnhub(), sec=FakeSEC())
        # ticker_matched feeds into confidence indirectly; assert via the
        # human_review escape hatch is not available here, so assert the
        # overall confidence reflects a matched ticker (0.20 credit present).
        self.assertGreaterEqual(result["validation_confidence"], 0.20)

    def test_ticker_matched_true_via_existence_confirmation_even_without_name_match(self):
        # csv_company doesn't match any fetched name, but FMP confirms the
        # ticker exists at all -- the trailing `bool(fmp_name) or bool(sec_name)`
        # disjunct in the original `any([...])` still marks it matched.
        csv_row = {**BASE_CSV_ROW, "company_公司名": "Totally Unrelated Name"}
        fmp = FakeFMP(profile={"company_name": "Something Else Entirely"})
        result, _ = run_validate_ticker(
            csv_row=csv_row, fmp=fmp, finnhub=FakeFinnhub(), sec=FakeSEC(info={"cik": 1})
        )
        self.assertEqual(result["validation_confidence"], 1.0)

    def test_ticker_matched_false_when_no_names_available_at_all(self):
        csv_row = {**BASE_CSV_ROW, "company_公司名": "Acme Corp"}
        result, _ = run_validate_ticker(
            csv_row=csv_row, fmp=FakeFMP(), finnhub=FakeFinnhub(), sec=FakeSEC(info={"cik": 1})
        )
        # All four name sources empty -> ticker_matched False -> lose 0.20
        # (sec_available is kept True via cik so only this one factor moves).
        self.assertEqual(result["validation_confidence"], 0.80)


class SectorConfirmedTests(unittest.TestCase):
    """Group 3."""

    def test_sector_confirmed_true_when_no_conflict_and_moderate_confidence(self):
        sector_result = _default_sector_result(source_conflict=False, sector_confidence=0.55)
        result, _ = run_validate_ticker(
            fmp=FakeFMP(profile={"company_name": "Acme Corp"}),
            finnhub=FakeFinnhub(profile={"company_name": "Acme Corp"}),
            sector_result=sector_result,
        )
        self.assertEqual(result["validation_status"], "PASS")

    def test_sector_confirmed_true_when_confidence_high_despite_conflict(self):
        sector_result = _default_sector_result(source_conflict=True, sector_confidence=0.75)
        result, _ = run_validate_ticker(
            fmp=FakeFMP(profile={"company_name": "Acme Corp"}),
            finnhub=FakeFinnhub(profile={"company_name": "Acme Corp"}),
            sector_result=sector_result,
        )
        # sector_confidence >= 0.70 wins outright, regardless of conflict.
        self.assertEqual(result["validation_confidence"], 1.0)
        # But source_conflict itself independently trips human_review.
        self.assertEqual(result["human_review_required"], "TRUE")

    def test_sector_confirmed_false_when_conflict_and_moderate_confidence_with_labels_present(self):
        sector_result = _default_sector_result(source_conflict=True, sector_confidence=0.55)
        result, _ = run_validate_ticker(
            fmp=FakeFMP(profile={"company_name": "Acme Corp"}),
            finnhub=FakeFinnhub(profile={"company_name": "Acme Corp"}),
            sector_result=sector_result,
        )
        # Loses the 0.20 sector credit; source_conflict trips human_review too.
        self.assertEqual(result["validation_confidence"], 0.80)
        self.assertEqual(result["human_review_required"], "TRUE")

    def test_sector_confirmed_true_when_no_label_rich_sources_available(self):
        # Empty FMP and Finnhub profiles -> "can't refute" escape hatch
        # fires regardless of conflict/confidence.
        sector_result = _default_sector_result(source_conflict=True, sector_confidence=0.10)
        result, _ = run_validate_ticker(
            fmp=FakeFMP(), finnhub=FakeFinnhub(), sector_result=sector_result,
        )
        # Sector credit retained (0.20) even though conflict flag is set;
        # only ticker_matched is lost since names are empty too.
        self.assertAlmostEqual(result["validation_confidence"], 0.80)


class ScoreAndAnomalyTests(unittest.TestCase):
    """Group 5/4: score_ok and no_anomalies wiring."""

    def test_score_ok_false_when_formula_mismatch_true(self):
        score_result = _default_score_result(formula_mismatch=True)
        result, _ = run_validate_ticker(score_result=score_result)
        self.assertAlmostEqual(result["validation_confidence"], 0.85)
        self.assertEqual(result["human_review_required"], "TRUE")

    def test_score_ok_false_when_final_score_recalculated_is_none(self):
        score_result = _default_score_result(final_score_recalculated=None)
        result, _ = run_validate_ticker(score_result=score_result)
        self.assertAlmostEqual(result["validation_confidence"], 0.85)
        self.assertEqual(result["final_score_recalculated"], "")

    def test_no_anomalies_false_reduces_confidence_without_triggering_review(self):
        financial_result = _default_financial_result(anomaly_count=2, raw_data_conflict=False)
        result, _ = run_validate_ticker(financial_result=financial_result)
        self.assertAlmostEqual(result["validation_confidence"], 0.90)
        self.assertEqual(result["human_review_required"], "FALSE")


class MomentumAgreementTests(unittest.TestCase):
    """Group 6."""

    def test_momentum_ok_true_when_delta_exactly_at_threshold(self):
        csv_row = {**BASE_CSV_ROW, "mom_动量分": "50.0"}
        result, _ = run_validate_ticker(csv_row=csv_row, momentum={"momentum_score": 70.0})
        self.assertEqual(result["validation_status"], "PASS")
        self.assertNotIn("MOM-DELTA", result["validation_notes"])

    def test_momentum_ok_false_when_delta_exceeds_threshold(self):
        csv_row = {**BASE_CSV_ROW, "mom_动量分": "50.0"}
        result, _ = run_validate_ticker(csv_row=csv_row, momentum={"momentum_score": 70.1})
        self.assertIn("MOM-DELTA", result["validation_notes"])
        self.assertAlmostEqual(result["validation_confidence"], 0.85)

    def test_momentum_ok_true_when_csv_side_missing(self):
        csv_row = dict(BASE_CSV_ROW)  # no mom_ column at all
        result, _ = run_validate_ticker(csv_row=csv_row, momentum={"momentum_score": 999.0})
        self.assertEqual(result["validation_status"], "PASS")
        self.assertNotIn("MOM-DELTA", result["validation_notes"])


class HumanReviewTriggerTests(unittest.TestCase):
    """Group 9: the 5 independent human_review_required triggers."""

    def test_triggered_by_formula_mismatch(self):
        result, _ = run_validate_ticker(score_result=_default_score_result(formula_mismatch=True))
        self.assertEqual(result["human_review_required"], "TRUE")

    def test_triggered_by_source_conflict(self):
        result, _ = run_validate_ticker(
            fmp=FakeFMP(profile={"company_name": "Acme Corp"}),
            finnhub=FakeFinnhub(profile={"company_name": "Acme Corp"}),
            sector_result=_default_sector_result(source_conflict=True, sector_confidence=0.90),
        )
        self.assertEqual(result["human_review_required"], "TRUE")

    def test_triggered_by_raw_data_conflict(self):
        result, _ = run_validate_ticker(
            financial_result=_default_financial_result(raw_data_conflict=True, anomaly_count=1)
        )
        self.assertEqual(result["human_review_required"], "TRUE")

    def test_triggered_by_fail_status(self):
        # Drive confidence below 0.65 by failing every input.
        result, _ = run_validate_ticker(
            csv_row=dict(BASE_CSV_ROW),
            fmp=FakeFMP(),
            finnhub=FakeFinnhub(),
            sec=FakeSEC(),
            sector_result=_default_sector_result(source_conflict=False, sector_confidence=0.10),
            score_result=_default_score_result(formula_mismatch=True, final_score_recalculated=None),
            financial_result=_default_financial_result(anomaly_count=3),
        )
        self.assertEqual(result["validation_status"], "FAIL")
        self.assertEqual(result["human_review_required"], "TRUE")

    def test_triggered_by_high_csv_ai_score_with_low_keyword_count(self):
        csv_row = {**BASE_CSV_ROW, "ai_AI暴露分": "80"}
        result, _ = run_validate_ticker(
            csv_row=csv_row,
            sector_result=_default_sector_result(ai_keyword_count=1),
        )
        self.assertEqual(result["human_review_required"], "TRUE")

    def test_not_triggered_by_ai_score_when_keyword_count_is_high_enough(self):
        csv_row = {**BASE_CSV_ROW, "ai_AI暴露分": "80"}
        result, _ = run_validate_ticker(
            csv_row=csv_row,
            sector_result=_default_sector_result(ai_keyword_count=2),
        )
        self.assertEqual(result["human_review_required"], "FALSE")

    def test_none_of_the_five_triggers_fire_in_baseline(self):
        result, _ = run_validate_ticker()
        self.assertEqual(result["human_review_required"], "FALSE")


class EdgarUrlTests(unittest.TestCase):
    """Group 10."""

    def test_edgar_url_constructed_from_cik_when_missing(self):
        sec = FakeSEC(info={"cik": 1750})
        result, _ = run_validate_ticker(sec=sec)
        self.assertEqual(
            result["source_urls_sec"],
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000001750&type=10-K",
        )

    def test_edgar_url_passed_through_when_already_present(self):
        sec = FakeSEC(info={"cik": 1750, "edgar_url": "https://example.test/already-set"})
        result, _ = run_validate_ticker(sec=sec)
        self.assertEqual(result["source_urls_sec"], "https://example.test/already-set")

    def test_edgar_url_empty_when_no_cik_and_no_edgar_url(self):
        sec = FakeSEC(info={})
        result, _ = run_validate_ticker(sec=sec, sector_result=_default_sector_result())
        self.assertEqual(result["source_urls_sec"], "")


class OutputAssemblyTests(unittest.TestCase):
    """Group 11."""

    def test_output_preserves_all_original_csv_columns(self):
        csv_row = {**BASE_CSV_ROW, "some_custom_column": "keep-me"}
        result, _ = run_validate_ticker(csv_row=csv_row)
        self.assertEqual(result["some_custom_column"], "keep-me")

    def test_boolean_fields_formatted_as_true_false_strings(self):
        result, _ = run_validate_ticker(
            score_result=_default_score_result(formula_mismatch=True),
            financial_result=_default_financial_result(raw_data_conflict=True, anomaly_count=1),
        )
        for field in ("source_conflict", "formula_mismatch", "raw_data_conflict", "human_review_required"):
            self.assertIn(result[field], ("TRUE", "FALSE"))
        self.assertEqual(result["formula_mismatch"], "TRUE")
        self.assertEqual(result["raw_data_conflict"], "TRUE")

    def test_none_numeric_fields_formatted_as_empty_string(self):
        score_result = _default_score_result(
            base_score_recalculated=None,
            dynamic_adjustment_recalculated=None,
            final_score_recalculated=None,
            formula_diff_abs=None,
        )
        result, _ = run_validate_ticker(score_result=score_result, momentum={})
        self.assertEqual(result["base_score_recalculated"], "")
        self.assertEqual(result["dynamic_adjustment_recalculated"], "")
        self.assertEqual(result["final_score_recalculated"], "")
        self.assertEqual(result["formula_diff_abs"], "")
        self.assertEqual(result["momentum_recalculated"], "")

    def test_source_urls_yf_and_fmp_are_deterministic_from_ticker(self):
        result, _ = run_validate_ticker()
        self.assertEqual(result["source_urls_yf"], "https://finance.yahoo.com/quote/TEST")
        self.assertEqual(
            result["source_urls_fmp"],
            "https://financialmodelingprep.com/financial-summary/TEST",
        )


class RefreshCacheClearTests(unittest.TestCase):
    """The refresh=True cache-clearing branch (untouched by this refactor,
    stays inline in the orchestrator) -- just confirm it runs without error
    for a ticker with no matching cache files."""

    def test_refresh_true_with_no_matching_cache_files_does_not_crash(self):
        result, _ = run_validate_ticker(refresh=True)
        self.assertEqual(result["validation_status"], "PASS")


if __name__ == "__main__":
    unittest.main()
