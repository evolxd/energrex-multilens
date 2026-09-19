"""Find the trading days missing from the NAV curve.

`daily_nav` gets one row per day, and only when a Firstrade sync actually
succeeds that day (`save_balance()` -> `record_daily_nav()`). Any day the app
was closed, Chrome was down, or the session had expired leaves a permanent
hole -- positions exports cannot backfill it, because they carry no cash or
equity figure.

Until now nothing looked for those holes. The war room warned "持仓数据已 N 天
未同步", which only measures the age of the newest row; a curve with four
missing days in the middle and a fresh row today reported as healthy. This
module answers the different question: between the first record and today,
which trading days have no NAV at all.

Holidays are computed from the NYSE rules rather than hard-coded, so the
detector does not silently rot at the turn of the year.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th `weekday` of a month (weekday: Monday=0). n is 1-based."""
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    """Last `weekday` of a month."""
    if month == 12:
        next_month = dt.date(year + 1, 1, 1)
    else:
        next_month = dt.date(year, month + 1, 1)
    last = next_month - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> dt.date:
    """Anonymous Gregorian algorithm -- needed only to locate Good Friday."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _observed(day: dt.date) -> dt.date:
    """NYSE shifts a weekend holiday to the adjacent weekday."""
    if day.weekday() == 5:          # Saturday -> Friday before
        return day - dt.timedelta(days=1)
    if day.weekday() == 6:          # Sunday -> Monday after
        return day + dt.timedelta(days=1)
    return day


def us_market_holidays(year: int) -> set[dt.date]:
    """NYSE full-day closures for `year`.

    Rule-derived, not a transcribed list: every date is computed from the
    statutory rule (n-th weekday / fixed date + weekend observance), so no
    table needs updating each January. Early closes (e.g. the half day after
    Thanksgiving) are deliberately excluded -- the market is open, so a missing
    NAV on such a day is a real gap.
    """
    days = {
        _observed(dt.date(year, 1, 1)),                 # New Year's Day
        _nth_weekday(year, 1, 0, 3),                    # MLK Jr. Day
        _nth_weekday(year, 2, 0, 3),                    # Washington's Birthday
        _easter(year) - dt.timedelta(days=2),           # Good Friday
        _last_weekday(year, 5, 0),                      # Memorial Day
        _observed(dt.date(year, 6, 19)),                # Juneteenth
        _observed(dt.date(year, 7, 4)),                 # Independence Day
        _nth_weekday(year, 9, 0, 1),                    # Labor Day
        _nth_weekday(year, 11, 3, 4),                   # Thanksgiving
        _observed(dt.date(year, 12, 25)),               # Christmas
    }
    return days


def is_trading_day(day: dt.date) -> bool:
    return day.weekday() < 5 and day not in us_market_holidays(day.year)


def trading_days_between(start: dt.date, end: dt.date) -> list[dt.date]:
    """Inclusive on both ends."""
    out: list[dt.date] = []
    day = start
    while day <= end:
        if is_trading_day(day):
            out.append(day)
        day += dt.timedelta(days=1)
    return out


@dataclass(frozen=True)
class NavGap:
    """A run of consecutive missing trading days."""
    start: dt.date
    end: dt.date
    days: int

    def label(self) -> str:
        if self.start == self.end:
            return self.start.isoformat()
        return f"{self.start.isoformat()} ~ {self.end.isoformat()}（{self.days} 个交易日）"


@dataclass(frozen=True)
class NavContinuity:
    recorded: int
    expected: int
    gaps: list[NavGap]
    first: dt.date | None
    last: dt.date | None

    @property
    def missing(self) -> int:
        return sum(g.days for g in self.gaps)

    @property
    def coverage_pct(self) -> float:
        if not self.expected:
            return 100.0
        return round(self.recorded / self.expected * 100.0, 1)

    @property
    def is_complete(self) -> bool:
        return not self.gaps


def _to_date(value) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def analyse(recorded_dates: Iterable, today: dt.date | None = None) -> NavContinuity:
    """Compare the dates actually stored against the trading days that existed.

    The window runs from the earliest stored date to `today`, so a curve that
    simply stopped updating shows the trailing gap too -- that is the failure
    mode that went unnoticed for four and a half days.
    """
    today = today or dt.date.today()
    dates = {d for d in (_to_date(v) for v in recorded_dates) if d is not None}
    if not dates:
        return NavContinuity(recorded=0, expected=0, gaps=[], first=None, last=None)

    first, last = min(dates), max(dates)
    expected = trading_days_between(first, max(last, today))
    missing = [d for d in expected if d not in dates]

    gaps: list[NavGap] = []
    run: list[dt.date] = []
    expected_index = {d: i for i, d in enumerate(expected)}
    for day in missing:
        if run and expected_index[day] == expected_index[run[-1]] + 1:
            run.append(day)
        else:
            if run:
                gaps.append(NavGap(run[0], run[-1], len(run)))
            run = [day]
    if run:
        gaps.append(NavGap(run[0], run[-1], len(run)))

    return NavContinuity(
        recorded=len([d for d in dates if d in set(expected)]),
        expected=len(expected),
        gaps=gaps,
        first=first,
        last=last,
    )


def load_nav_dates(conn, account_id: str) -> list[str]:
    """Read the stored NAV dates for an account. Caller owns the connection."""
    rows = conn.execute(
        "SELECT date FROM daily_nav WHERE account_id=? ORDER BY date",
        (account_id,),
    ).fetchall()
    return [r[0] for r in rows]
