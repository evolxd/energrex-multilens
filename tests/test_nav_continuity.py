"""The NAV curve must be able to say which days it is missing.

`daily_nav` only gets a row when a sync succeeds that day. The war room's
existing warning measures the age of the newest row, so a curve with holes in
the middle but a fresh row today looked perfectly healthy. These tests pin the
gap detector, including the part that makes it usable at all: weekends and
market holidays must not be reported as missing.
"""

import datetime as dt

import pytest

from account.nav_continuity import (
    NavGap,
    analyse,
    is_trading_day,
    trading_days_between,
    us_market_holidays,
)


# ── holiday rules ────────────────────────────────────────────────────────────

def test_holiday_rules_resolve_to_the_expected_weekday():
    # Derived, not transcribed: check each against the rule it encodes.
    holidays_2026 = us_market_holidays(2026)
    mlk = next(d for d in holidays_2026 if d.month == 1 and d.day > 10)
    assert mlk.weekday() == 0 and 15 <= mlk.day <= 21      # 3rd Monday of Jan

    thanksgiving = next(d for d in holidays_2026 if d.month == 11)
    assert thanksgiving.weekday() == 3 and 22 <= thanksgiving.day <= 28

    memorial = next(d for d in holidays_2026 if d.month == 5)
    assert memorial.weekday() == 0 and memorial.day >= 25   # last Monday of May

    labor = next(d for d in holidays_2026 if d.month == 9)
    assert labor.weekday() == 0 and labor.day <= 7          # 1st Monday of Sep


def test_good_friday_is_two_days_before_easter_sunday():
    good_friday = next(d for d in us_market_holidays(2026)
                       if d.weekday() == 4 and d.month in (3, 4))
    assert (good_friday + dt.timedelta(days=2)).weekday() == 6  # Easter Sunday


@pytest.mark.parametrize("year", [2024, 2025, 2026, 2027, 2028])
def test_every_year_yields_ten_distinct_closures(year):
    assert len(us_market_holidays(year)) == 10


def test_weekend_holidays_shift_to_an_adjacent_weekday():
    for year in range(2024, 2031):
        for day in us_market_holidays(year):
            assert day.weekday() < 5, f"{day} fell on a weekend"


# ── trading-day arithmetic ───────────────────────────────────────────────────

def test_weekends_are_not_trading_days():
    saturday = dt.date(2026, 9, 19)
    assert saturday.weekday() == 5
    assert not is_trading_day(saturday)
    assert not is_trading_day(saturday + dt.timedelta(days=1))


def test_trading_days_between_skips_the_weekend():
    days = trading_days_between(dt.date(2026, 9, 18), dt.date(2026, 9, 21))
    assert days == [dt.date(2026, 9, 18), dt.date(2026, 9, 21)]


# ── gap detection ────────────────────────────────────────────────────────────

def test_complete_run_reports_no_gaps():
    days = trading_days_between(dt.date(2026, 9, 7), dt.date(2026, 9, 18))
    result = analyse([d.isoformat() for d in days], today=dt.date(2026, 9, 18))
    assert result.is_complete
    assert result.coverage_pct == 100.0


def test_a_hole_in_the_middle_is_found_even_when_today_has_data():
    # The failure the age-based warning cannot see: newest row is today, but
    # three trading days in the middle never got written.
    days = trading_days_between(dt.date(2026, 9, 7), dt.date(2026, 9, 18))
    missing = {dt.date(2026, 9, 9), dt.date(2026, 9, 10), dt.date(2026, 9, 11)}
    recorded = [d.isoformat() for d in days if d not in missing]

    result = analyse(recorded, today=dt.date(2026, 9, 18))

    assert not result.is_complete
    assert result.missing == 3
    assert result.gaps == [NavGap(dt.date(2026, 9, 9), dt.date(2026, 9, 11), 3)]


def test_the_trailing_gap_counts_up_to_today():
    # The real incident: last successful sync 09-14, nothing since.
    days = trading_days_between(dt.date(2026, 9, 7), dt.date(2026, 9, 14))
    result = analyse([d.isoformat() for d in days], today=dt.date(2026, 9, 18))

    assert result.missing == 4                      # 15,16,17,18 are all weekdays
    assert result.gaps[-1].end == dt.date(2026, 9, 18)


def test_separate_holes_are_reported_as_separate_runs():
    days = trading_days_between(dt.date(2026, 9, 1), dt.date(2026, 9, 18))
    missing = {dt.date(2026, 9, 3), dt.date(2026, 9, 10), dt.date(2026, 9, 11)}
    recorded = [d.isoformat() for d in days if d not in missing]

    gaps = analyse(recorded, today=dt.date(2026, 9, 18)).gaps

    assert [g.days for g in gaps] == [1, 2]
    assert gaps[0].start == gaps[0].end == dt.date(2026, 9, 3)


def test_a_weekend_only_pause_is_not_a_gap():
    friday, monday = dt.date(2026, 9, 18), dt.date(2026, 9, 21)
    result = analyse([friday.isoformat(), monday.isoformat()], today=monday)
    assert result.is_complete


def test_empty_history_is_reported_without_crashing():
    result = analyse([], today=dt.date(2026, 9, 18))
    assert result.recorded == 0 and result.gaps == [] and result.first is None


def test_datetime_and_date_inputs_are_both_accepted():
    friday = dt.date(2026, 9, 18)
    as_datetime = dt.datetime(2026, 9, 18, 16, 30)
    assert analyse([as_datetime], today=friday).recorded == 1
    assert analyse([friday], today=friday).recorded == 1


def test_coverage_percentage_reflects_the_hole():
    days = trading_days_between(dt.date(2026, 9, 7), dt.date(2026, 9, 18))
    recorded = [d.isoformat() for d in days[:-2]]
    result = analyse(recorded, today=dt.date(2026, 9, 18))
    assert 0 < result.coverage_pct < 100


def test_gap_label_reads_as_a_single_day_or_a_range():
    assert NavGap(dt.date(2026, 9, 3), dt.date(2026, 9, 3), 1).label() == "2026-09-03"
    assert "~" in NavGap(dt.date(2026, 9, 9), dt.date(2026, 9, 11), 3).label()
