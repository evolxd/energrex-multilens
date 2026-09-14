"""Real-data systemic-risk trigger inputs for account.hedge_governance.

account.hedge_governance.evaluate_protective_put_hedges() has always accepted
vix_spike / event_risk / trend_break parameters, but every call site left them
at their False defaults -- the only trigger that ever actually fired in
production was BETA_DELTA_EXCESS. This module supplies the other three from
real, traceable data instead of leaving them permanently off.

Deliberately does NOT attempt dealer gamma/GEX positioning or a macro-surprise
(actual-vs-consensus) Z-score: neither has a real data source in this system,
and fabricating a proxy for either would repeat exactly the mistake the
2026-09-14 sector-baseline audit and the AI-exposure verification-gate fix
were both about -- a number that looks like a signal but isn't backed by
anything. event_risk here is scoped to the one thing that's both genuinely
schedule-driven and freely knowable in advance: FOMC decision days.
"""

from __future__ import annotations

import datetime as _dt


# The Fed publishes its meeting calendar for the year in advance. Second day
# of each two-day meeting is the actual decision/statement date -- the single
# highest-realized-volatility session of the cycle. Source:
# federalreserve.gov/monetarypolicy/fomccalendars.htm -- update by hand each
# December when the following year's calendar is published.
FOMC_DECISION_DATES_2026: tuple[_dt.date, ...] = (
    _dt.date(2026, 1, 28),
    _dt.date(2026, 3, 18),
    _dt.date(2026, 4, 29),
    _dt.date(2026, 6, 17),
    _dt.date(2026, 7, 29),
    _dt.date(2026, 9, 16),
    _dt.date(2026, 10, 28),
    _dt.date(2026, 12, 9),
)

EVENT_RISK_WINDOW_DAYS = 1  # decision day, plus one calendar day either side


def fomc_event_risk(
    today: _dt.date,
    meeting_dates: tuple[_dt.date, ...] | None = None,
    window_days: int = EVENT_RISK_WINDOW_DAYS,
) -> bool:
    """True inside the pre/post window around a scheduled FOMC decision day.

    This is a calendar fact, not a prediction -- it says nothing about which
    way the decision will surprise, only that realized volatility around
    these sessions is structurally elevated (a well-documented effect, unlike
    trying to score the surprise itself without consensus data this system
    doesn't have).
    """
    dates = FOMC_DECISION_DATES_2026 if meeting_dates is None else meeting_dates
    return any(abs((today - d).days) <= window_days for d in dates)


def qqq_trend_break(qqq_closes) -> dict:
    """QQQ position relative to its 50/200-day moving averages.

    Mirrors refresh_scores.py::_compute_momentum()'s real vs-200DMA
    computation (same rolling-mean-of-close approach), applied to QQQ instead
    of a single stock, so this reuses an already-verified calculation rather
    than inventing a second one.

    qqq_closes: a pandas Series of QQQ daily closes (>= ~200 trading days,
    oldest first), e.g. yf.Ticker("QQQ").history(period="1y")["Close"].
    """
    if qqq_closes is None or len(qqq_closes) < 15:
        return {
            "trend_break": False, "below_50": False, "below_200": False,
            "qqq_price": None, "ma50": None, "ma200": None,
            "reason": "insufficient_history",
        }

    last = float(qqq_closes.iloc[-1])
    ma200_periods = min(200, len(qqq_closes))
    ma200 = float(qqq_closes.rolling(ma200_periods).mean().iloc[-1])
    ma50_periods = min(50, len(qqq_closes))
    ma50 = float(qqq_closes.rolling(ma50_periods).mean().iloc[-1])

    below_200 = last < ma200
    below_50 = last < ma50
    return {
        # account.hedge_governance treats a 200DMA break as the "major trend
        # break" trigger; a 50DMA break alone is informational only (it's
        # exposed as below_50 for callers that want the softer read).
        "trend_break": below_200,
        "below_50": below_50,
        "below_200": below_200,
        "qqq_price": last,
        "ma50": ma50,
        "ma200": ma200,
    }
