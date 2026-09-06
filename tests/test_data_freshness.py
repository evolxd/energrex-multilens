from datetime import date

from scoring.data_freshness import (
    FreshnessStatus,
    check_ticker_freshness,
    extract_latest_date,
    manual_refresh_fields,
)


def test_extract_latest_date_picks_the_most_recent_of_several():
    vintage = (
        "2026-08-18 price/ev_sales/ps_ratio/market_cap verified via web_search; "
        "all other fields still 2026-06-11 initial estimate, unverified this pass"
    )
    assert extract_latest_date(vintage) == date(2026, 8, 18)


def test_extract_latest_date_returns_none_when_no_iso_date_present():
    assert extract_latest_date("FY2027-Q1 (ended 2026-04-30) + 2026-06-10 market") == date(2026, 6, 10)
    assert extract_latest_date("initial estimate, no date") is None
    assert extract_latest_date("") is None


def test_check_ticker_freshness_fresh_within_cycle():
    result = check_ticker_freshness(
        "TEST", "2026-06-01 verified", today=date(2026, 8, 1), cycle_days=90
    )
    assert result.status == FreshnessStatus.FRESH
    assert result.days_since == 61


def test_check_ticker_freshness_stale_past_one_earnings_cycle():
    result = check_ticker_freshness(
        "TEST", "2026-01-01 verified", today=date(2026, 8, 1), cycle_days=90
    )
    assert result.status == FreshnessStatus.STALE
    assert result.days_since == 212


def test_check_ticker_freshness_needs_review_without_a_date():
    result = check_ticker_freshness("TEST", "initial estimate", today=date(2026, 8, 1))
    assert result.status == FreshnessStatus.NEEDS_REVIEW
    assert result.last_touched is None
    assert result.days_since is None


def test_manual_refresh_fields_excludes_auto_refresh_market_fields():
    fields = manual_refresh_fields()
    assert "ai_revenue_exposure_pct" in fields
    assert "valuation_risk" in fields
    assert "current_price" not in fields
    assert "beta" not in fields
