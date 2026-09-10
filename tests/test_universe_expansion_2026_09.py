"""Tests for the 2026-09-10 ticker universe expansion.

Ten tickers added to TICKER_CATEGORY: CRWV, ALAB, CRDO, NBIS, MPWR, TER,
QLYS, RPD, TENB, GTM. Each was verified via WebSearch to still be actively
traded and correctly described (this sandbox's yfinance/WebFetch access is
blocked, so live financials could not be checked -- see the comment block
above these entries in scoring_engine.py). This file locks in the two
things that verification actually covers: correct category/sector_tag
classification, and that no financial data was fabricated to fill the gap
-- new tickers get real blank rows for refresh_scores.py to populate live,
not guessed numbers.
"""
import csv
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "scoring"))
from scoring_engine import CompanyCategory, TICKER_CATEGORY, get_category
from quant_data import QUANT_META

ROOT = Path(__file__).parents[1]
RESULTS = ROOT / "results_validated.csv"

NEW_BATCH = {
    "CRWV": CompanyCategory.AI_CHIP,
    "ALAB": CompanyCategory.AI_CHIP,
    "CRDO": CompanyCategory.AI_CHIP,
    "NBIS": CompanyCategory.AI_CHIP,
    "MPWR": CompanyCategory.AI_CHIP,
    "TER":  CompanyCategory.SEMI_EQUIP,
    "QLYS": CompanyCategory.CYBERSECURITY,
    "RPD":  CompanyCategory.CYBERSECURITY,
    "TENB": CompanyCategory.CYBERSECURITY,
    "GTM":  CompanyCategory.AI_SOFTWARE,
}

# quant_engine.SECTOR_BASELINES' three keys -- QUANT_META's sector_tag must
# be one of these or score_ticker() silently falls back to "Hardware".
VALID_SECTOR_TAGS = {"Hardware", "SaaS", "Cybersecurity"}


@pytest.mark.parametrize("ticker,expected_category", NEW_BATCH.items())
def test_new_ticker_is_classified_as_expected(ticker, expected_category):
    assert TICKER_CATEGORY[ticker] is expected_category
    assert get_category(ticker) is expected_category


@pytest.mark.parametrize("ticker", NEW_BATCH)
def test_new_ticker_has_a_valid_quant_meta_sector_tag(ticker):
    assert ticker in QUANT_META, f"{ticker} missing from QUANT_META -- score_ticker() needs sector_tag"
    assert QUANT_META[ticker]["sector_tag"] in VALID_SECTOR_TAGS


def _load_results_row(ticker: str) -> dict:
    with RESULTS.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("ticker", "").strip() == ticker:
                return row
    raise AssertionError(f"{ticker} not found in {RESULTS.name}")


@pytest.mark.parametrize("ticker", NEW_BATCH)
def test_new_ticker_has_a_real_blank_row_not_fabricated_data(ticker):
    """The row must exist (satisfies test_every_classified_ticker_is_actually_scored
    in test_universe.py) but every field besides ticker/company/sector must be
    empty -- proving no financial figures were guessed to fill the gap."""
    row = _load_results_row(ticker)
    for column, value in row.items():
        lowered = column.lower()
        if lowered == "ticker" or lowered.startswith("company") or lowered.startswith("sector"):
            continue
        assert value in (None, ""), f"{ticker}.{column} = {value!r}, expected blank (unscored)"


def test_new_batch_is_exactly_ten_tickers():
    assert len(NEW_BATCH) == 10
    assert NEW_BATCH.keys() <= TICKER_CATEGORY.keys()
