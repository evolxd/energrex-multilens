import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scoring"))
import macro_calendar as mc  # noqa: E402


def test_fomc_2026_has_eight_meetings_in_calendar_order():
    assert len(mc.FOMC_MEETINGS_2026) == 8
    starts = [lo for lo, hi in mc.FOMC_MEETINGS_2026]
    assert starts == sorted(starts)
    for lo, hi in mc.FOMC_MEETINGS_2026:
        assert lo <= hi
        assert lo.year == 2026


def test_cpi_2026_covers_every_month_and_release_is_after_reference_month():
    assert len(mc.CPI_RELEASES_2026) == 12
    for month_key, release_date in mc.CPI_RELEASES_2026.items():
        ref_year, ref_month = (int(x) for x in month_key.split("-"))
        ref_end = datetime.date(ref_year, ref_month, 28)
        assert release_date > ref_end   # a release always comes after the month it covers


def test_nfp_2026_covers_every_month_and_release_is_after_reference_month():
    assert len(mc.NFP_RELEASES_2026) == 12
    for month_key, (release_date, _confirmed) in mc.NFP_RELEASES_2026.items():
        ref_year, ref_month = (int(x) for x in month_key.split("-"))
        ref_end = datetime.date(ref_year, ref_month, 28)
        assert release_date > ref_end


def test_nfp_confirmed_exceptions_match_the_documented_2026_schedule_breaks():
    # These four are the ones this module's docstring claims a source
    # explicitly stated -- if this test fails, the docstring and the data
    # have drifted apart.
    confirmed_months = {m for m, (_, c) in mc.NFP_RELEASES_2026.items() if c}
    assert confirmed_months == {"2025-12", "2026-04", "2026-06", "2026-08"}
    assert mc.NFP_RELEASES_2026["2025-12"][0] == datetime.date(2026, 2, 11)
    assert mc.NFP_RELEASES_2026["2026-04"][0] == datetime.date(2026, 5, 8)
    assert mc.NFP_RELEASES_2026["2026-06"][0] == datetime.date(2026, 7, 2)
    assert mc.NFP_RELEASES_2026["2026-08"][0] == datetime.date(2026, 9, 4)


def test_releases_within_finds_known_september_and_october_releases():
    hits = mc.releases_within(datetime.date(2026, 9, 3), datetime.date(2026, 11, 2))
    types_and_dates = {(h["type"], h["date"]) for h in hits}
    assert ("NFP", datetime.date(2026, 9, 4)) in types_and_dates
    assert ("CPI", datetime.date(2026, 9, 11)) in types_and_dates
    assert ("FOMC", datetime.date(2026, 9, 15)) in types_and_dates
    assert ("CPI", datetime.date(2026, 10, 14)) in types_and_dates
    assert ("FOMC", datetime.date(2026, 10, 27)) in types_and_dates


def test_releases_within_is_sorted_chronologically():
    hits = mc.releases_within(datetime.date(2026, 1, 1), datetime.date(2026, 12, 31))
    dates = [h["date"] for h in hits]
    assert dates == sorted(dates)


def test_releases_within_empty_window_returns_nothing():
    assert mc.releases_within(datetime.date(2026, 9, 5), datetime.date(2026, 9, 6)) == []


def test_releases_within_respects_inclusive_boundaries():
    hits = mc.releases_within(datetime.date(2026, 9, 4), datetime.date(2026, 9, 4))
    assert len(hits) == 1
    assert hits[0]["type"] == "NFP"


def test_fomc_meeting_counts_if_either_boundary_day_is_in_window():
    # Window that only catches the second day of the Sep 15-16 meeting.
    hits = mc.releases_within(datetime.date(2026, 9, 16), datetime.date(2026, 9, 16))
    assert any(h["type"] == "FOMC" for h in hits)


# release_risk_label -- 2026-09-09 extracted from bull_put_spread_module.py /
# bull_call_spread_module.py, which had this exact formatting logic copied
# twice with no macro-calendar-specific reason for it to live in either page.

def test_release_risk_label_returns_dash_when_no_releases_in_window():
    assert mc.release_risk_label(datetime.date(2026, 9, 5), "2026-09-06") == "—"


def test_release_risk_label_includes_known_confirmed_nfp():
    label = mc.release_risk_label(datetime.date(2026, 9, 3), "2026-09-04")
    assert "非农 (2026-08)" in label
    assert "（日期未核实）" not in label  # 2026-08 NFP is one of the confirmed exceptions


def test_release_risk_label_marks_unconfirmed_release_dates():
    # 2026-09 NFP is not in the confirmed-exceptions set.
    label = mc.release_risk_label(datetime.date(2026, 9, 3), "2026-10-02")
    assert "（日期未核实）" in label


def test_release_risk_label_joins_multiple_hits_with_middle_dot():
    label = mc.release_risk_label(datetime.date(2026, 9, 3), "2026-09-15")
    assert " · " in label
