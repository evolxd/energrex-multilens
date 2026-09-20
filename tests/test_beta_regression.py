"""Beta measured from prices, for the symbols no vendor will price.

`_refresh_beta_spy()` used to give up whenever `yf.Ticker(sym).info["beta"]`
came back empty -- which it does for recent listings and many ETFs -- leaving
those names on a stale static value, or on the silent 1.0 default if they were
never in the table at all. This module regresses the returns instead, and these
tests pin the two judgement calls it makes: which opening sessions to discard,
and which estimates to refuse.
"""

import datetime as dt

import pytest

from account.beta_regression import (
    IPO_SKIP_SESSIONS,
    MIN_OBSERVATIONS,
    estimate_beta,
    looks_recently_listed,
    plausible_beta,
)


def _sessions(n: int, start: dt.date = dt.date(2026, 1, 5)) -> list[str]:
    """n weekday dates as ISO strings."""
    out, day = [], start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def _synthetic(dates, beta, noise=0.0, market_step=0.01):
    """Market series plus an asset that is exactly `beta` times it, and the
    matching market series. Alternating up/down keeps variance non-zero."""
    market, asset = {}, {}
    mp = ap = 100.0
    for i, d in enumerate(dates):
        market[d], asset[d] = mp, ap
        r = market_step if i % 2 == 0 else -market_step
        wobble = noise if i % 3 == 0 else -noise
        mp *= 1 + r
        ap *= 1 + beta * r + wobble
    return asset, market


def test_recovers_a_known_beta_from_clean_data():
    dates = _sessions(80)
    asset, market = _synthetic(dates, beta=2.5)
    fit = estimate_beta(asset, market, skip_initial=0)
    assert fit.beta == pytest.approx(2.5, abs=0.05)
    assert fit.r_squared == pytest.approx(1.0, abs=0.01)


def test_too_few_observations_returns_nothing_rather_than_a_number():
    dates = _sessions(MIN_OBSERVATIONS - 5)
    asset, market = _synthetic(dates, beta=1.5)
    assert estimate_beta(asset, market, skip_initial=0) is None


def test_a_listing_is_detected_by_starting_late_not_by_being_short():
    """The bug this guards: detection keyed off series length flagged ETHU --
    which has years of history -- as newly listed purely because the market
    series fetched alongside it happened to be truncated, cutting five good
    sessions and moving its beta from 2.88 to 2.17."""
    dates = _sessions(80)
    _, market = _synthetic(dates, beta=1.0)

    late_asset, _ = _synthetic(dates[40:], beta=2.0)          # 上市晚
    assert looks_recently_listed(late_asset, market)

    short_asset, _ = _synthetic(dates[:40], beta=2.0)         # 同期开始，只是取数窗口短
    assert not looks_recently_listed(short_asset, market)


def test_opening_sessions_are_dropped_whole_for_a_new_listing():
    dates = _sessions(80)
    _, market = _synthetic(dates, beta=1.0)
    asset, _ = _synthetic(dates[40:], beta=2.0)

    fit = estimate_beta(asset, market)
    assert fit.skipped_initial == IPO_SKIP_SESSIONS
    # No return may straddle the discarded period: the first return must start
    # after the skipped closes, not use the last of them as its base.
    assert fit.sample_start > sorted(set(asset) & set(market))[IPO_SKIP_SESSIONS - 1]


def test_an_established_name_keeps_all_its_sessions():
    dates = _sessions(80)
    asset, market = _synthetic(dates, beta=1.8)
    assert estimate_beta(asset, market).skipped_initial == 0


def test_beta_above_the_volatility_ratio_is_refused():
    """The Firstrade SPCX case: 25.14 against a 7.30 ceiling. Correlation
    cannot exceed 1, so beta cannot exceed the ratio of the two volatilities --
    any estimate that does is not a regression result."""
    dates = _sessions(80)
    asset, market = _synthetic(dates, beta=2.0)
    fit = estimate_beta(asset, market, skip_initial=0)
    assert fit.is_plausible and plausible_beta(fit)

    impossible = type(fit)(**{**fit.__dict__, "beta": fit.volatility_ratio * 3})
    assert not impossible.is_plausible
    assert not plausible_beta(impossible)


def test_nothing_is_plausible_when_there_is_no_fit():
    assert not plausible_beta(None)


def test_a_flat_market_yields_no_estimate():
    dates = _sessions(80)
    market = {d: 100.0 for d in dates}
    asset = {d: 100.0 + i for i, d in enumerate(dates)}
    assert estimate_beta(asset, market, skip_initial=0) is None


def test_only_dates_present_in_both_series_are_used():
    dates = _sessions(80)
    asset, market = _synthetic(dates, beta=2.0)
    for d in dates[::7]:
        del market[d]                                   # 行情缺几天
    fit = estimate_beta(asset, market, skip_initial=0)
    assert fit.observations == len(set(asset) & set(market)) - 1
    assert fit.beta == pytest.approx(2.0, abs=0.35)
