"""Known 2026 US macro release dates (FOMC / CPI / Nonfarm Payrolls).

Used by the Bull Put Spread page to flag "this expiration window contains a
scheduled macro release" -- elevated realized-vol risk around a print,
independent of whether the number itself beats or misses consensus (this
project has no free source for consensus/expected values -- see the
2026-09-03 conversation; this calendar only tells you WHEN a release
happens, not what the market expects from it).

⚠️ Sourcing caveat: this sandbox's WebFetch/HTTP access to bls.gov and
federalreserve.gov is blocked by the egress proxy, so nothing here was
read directly off the primary source's HTML. Everything was gathered via
WebSearch (2026-09-03), which returns a search engine's own summary of
those pages, not the raw page. Cross-checked where possible (see each
list's docstring for exactly what that means), but this should be treated
as a best-effort snapshot to re-verify against bls.gov/schedule and
federalreserve.gov/monetarypolicy/fomccalendars.htm before relying on it
for anything with real money behind it -- especially the Nonfarm Payrolls
dates flagged confirmed=False below.
"""
from __future__ import annotations

import datetime

# FOMC meeting date ranges (first day, last day) for 2026. Jan/Mar/Apr/Jun/
# Jul/Oct were read off a WebSearch result quoting federalreserve.gov
# directly; Sep and Dec were not found on the Fed's own page in this
# search session, but two independent secondary aggregators (fedratecalc.com,
# marketclock.net) agree on the same two dates, and 8 total meetings matches
# the FOMC's standard annual cadence.
FOMC_MEETINGS_2026: list[tuple[datetime.date, datetime.date]] = [
    (datetime.date(2026, 1, 27), datetime.date(2026, 1, 28)),
    (datetime.date(2026, 3, 17), datetime.date(2026, 3, 18)),
    (datetime.date(2026, 4, 28), datetime.date(2026, 4, 29)),
    (datetime.date(2026, 6, 16), datetime.date(2026, 6, 17)),
    (datetime.date(2026, 7, 28), datetime.date(2026, 7, 29)),
    (datetime.date(2026, 9, 15), datetime.date(2026, 9, 16)),
    (datetime.date(2026, 10, 27), datetime.date(2026, 10, 28)),
    (datetime.date(2026, 12, 8), datetime.date(2026, 12, 9)),
]

# CPI release dates for 2026, keyed by the calendar month the release covers
# (a release always reports on the PRIOR month -- e.g. the 2026-09-11 entry
# is the release covering August 2026 data). The 2026-09-11 date for August
# was independently stated in two separate WebSearch queries in this
# session (once quoting bls.gov's CPI schedule page, once from a general
# schedule search); the rest of the year comes from a single search-engine
# summary aggregating usinflationcalculator.com/BLS mirrors and was not
# independently re-verified month by month.
CPI_RELEASES_2026: dict[str, datetime.date] = {
    "2025-12": datetime.date(2026, 1, 13),
    "2026-01": datetime.date(2026, 2, 13),
    "2026-02": datetime.date(2026, 3, 11),
    "2026-03": datetime.date(2026, 4, 10),
    "2026-04": datetime.date(2026, 5, 12),
    "2026-05": datetime.date(2026, 6, 10),
    "2026-06": datetime.date(2026, 7, 14),
    "2026-07": datetime.date(2026, 8, 12),
    "2026-08": datetime.date(2026, 9, 11),
    "2026-09": datetime.date(2026, 10, 14),
    "2026-10": datetime.date(2026, 11, 10),
    "2026-11": datetime.date(2026, 12, 10),
}

# Nonfarm Payrolls (Employment Situation) release dates for 2026, keyed by
# the month of data covered. Normally the first Friday of the FOLLOWING
# month, but 2026 has already broken that rule three times: the report
# covering December 2025 was delayed to 2026-02-11 by a government
# shutdown, April's report moved to the second Friday (2026-05-08), and
# June's report moved to Thursday 2026-07-02 ahead of the July 4th holiday.
# confirmed=True means a source explicitly stated that date; confirmed=False
# means it's this module's own "first Friday of the following month"
# arithmetic, included so the calendar has no gaps but NOT verified against
# any source -- given three exceptions already this year, do not treat an
# unconfirmed date as reliable without checking bls.gov/schedule first.
NFP_RELEASES_2026: dict[str, tuple[datetime.date, bool]] = {
    "2025-12": (datetime.date(2026, 2, 11), True),   # confirmed: shutdown delay
    "2026-01": (datetime.date(2026, 2, 6), False),
    "2026-02": (datetime.date(2026, 3, 6), False),
    "2026-03": (datetime.date(2026, 4, 3), False),
    "2026-04": (datetime.date(2026, 5, 8), True),    # confirmed: 2nd Friday
    "2026-05": (datetime.date(2026, 6, 5), False),
    "2026-06": (datetime.date(2026, 7, 2), True),    # confirmed: moved to Thursday
    "2026-07": (datetime.date(2026, 8, 7), False),
    "2026-08": (datetime.date(2026, 9, 4), True),    # confirmed
    "2026-09": (datetime.date(2026, 10, 2), False),
    "2026-10": (datetime.date(2026, 11, 6), False),
    "2026-11": (datetime.date(2026, 12, 4), False),
}


def releases_within(start: datetime.date, end: datetime.date) -> list[dict]:
    """Every known macro release whose date falls within [start, end]
    (inclusive), sorted chronologically. Each item is
    {"date", "type", "label", "confirmed"} -- FOMC/CPI entries are always
    confirmed=True at the "this is a real scheduled event" level (their
    sourcing caveats are about the exact date, not whether it happens);
    NFP entries carry the per-month confirmed flag from NFP_RELEASES_2026.
    """
    hits: list[dict] = []
    for lo, hi in FOMC_MEETINGS_2026:
        if start <= lo <= end or start <= hi <= end:
            hits.append({
                "date": lo, "type": "FOMC",
                "label": f"FOMC {lo:%m-%d}~{hi:%m-%d}",
                "confirmed": True,
            })
    for month, date in CPI_RELEASES_2026.items():
        if start <= date <= end:
            hits.append({"date": date, "type": "CPI", "label": f"CPI ({month})", "confirmed": True})
    for month, (date, confirmed) in NFP_RELEASES_2026.items():
        if start <= date <= end:
            hits.append({"date": date, "type": "NFP", "label": f"非农 ({month})", "confirmed": confirmed})
    return sorted(hits, key=lambda h: h["date"])
