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
    estimate_downside_beta,
    precise_enough,
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


# ── 下跌日 beta ─────────────────────────────────────────────────────────
# 压力测试算的是 beta × 负的冲击，要的是下跌日的斜率。它跟全样本 beta 是
# 两个不同的参数，不是同一个数的两种精度。

def _series(returns, start=100.0):
    """把收益率序列变成收盘价字典，日期从 2026-01-01 起顺排。"""
    import datetime as _dt
    out, px, d = {}, start, _dt.date(2026, 1, 1)
    out[d.isoformat()] = px
    for r in returns:
        d += _dt.timedelta(days=1)
        px *= (1.0 + r)
        out[d.isoformat()] = px
    return out


def test_downside_beta_only_uses_days_the_market_fell():
    # 上涨日给一个夸张的斜率，下跌日给 2.0。全样本会被上涨日拉高，
    # 下跌日口径必须只看到 2.0。
    #
    # 下跌日的幅度必须有变化：如果每个下跌日大盘都正好跌 1%，那个子样本里
    # 自变量方差为 0，回归无解——这是构造测试数据时很容易踩的坑，真实行情
    # 里不会发生，但它会让这条测试量到的是浮点噪声而不是斜率。
    mkt, ast = [], []
    for i in range(60):
        m = (0.008 + 0.004 * (i % 3)) if i % 2 == 0 else -(0.006 + 0.005 * (i % 4))
        mkt.append(m)
        ast.append(m * (8.0 if m > 0 else 2.0))
    full = estimate_beta(_series(ast), _series(mkt), skip_initial=0)
    down = estimate_downside_beta(_series(ast), _series(mkt), skip_initial=0)
    assert down.beta == pytest.approx(2.0, abs=0.01)
    assert full.beta > down.beta + 1.0
    assert down.observations == 30


def test_the_two_kinds_are_labelled_so_they_cannot_be_confused():
    mkt = [0.01 if i % 2 == 0 else -0.01 for i in range(80)]
    ast = [m * 1.5 for m in mkt]
    assert estimate_beta(_series(ast), _series(mkt), skip_initial=0).kind == "full"
    assert estimate_downside_beta(_series(ast), _series(mkt), skip_initial=0).kind == "downside"


def test_too_few_down_days_is_none_rather_than_a_number():
    # 全样本够 30 个，但下跌日只有 5 个——后者必须拒绝出数。
    mkt = [-0.01] * 5 + [0.01] * 55
    ast = [m * 2.0 for m in mkt]
    assert estimate_beta(_series(ast), _series(mkt), skip_initial=0) is not None
    assert estimate_downside_beta(_series(ast), _series(mkt), skip_initial=0) is None


def test_a_market_that_never_fell_has_no_downside_beta():
    mkt = [0.01] * 60
    ast = [0.02] * 60
    assert estimate_downside_beta(_series(ast), _series(mkt), skip_initial=0) is None


def test_refactor_did_not_change_the_full_sample_arithmetic():
    """全样本和下跌日共用 _fit_returns。共用之后全样本的结果必须逐位不变,
    否则两个 beta 的差就分不清是真实差异还是实现差异。"""
    import random
    random.seed(7)
    mkt = [random.gauss(0, 0.01) for _ in range(120)]
    ast = [1.8 * m + random.gauss(0, 0.005) for m in mkt]
    fit = estimate_beta(_series(ast), _series(mkt), skip_initial=0)
    assert fit.beta == pytest.approx(1.8, abs=0.1)
    assert fit.observations == 120
    assert 0.0 <= fit.r_squared <= 1.0


# ── 精度门槛 ────────────────────────────────────────────────────────────

def test_a_noisy_estimate_is_rejected_even_when_it_is_physically_possible():
    """ONTO 的真实情形：β=2.08 但 95% 区间 [-0.65, 4.80] 跨过 0。

    plausible_beta 只管"物理上可不可能"（波动率比值上界），跨过 0 的区间
    它一样放行——所以需要第二关。
    """
    import random
    random.seed(11)
    mkt = [random.gauss(0, 0.01) for _ in range(120)]
    ast = [2.0 * m + random.gauss(0, 0.08) for m in mkt]     # 残差远大于信号
    fit = estimate_beta(_series(ast), _series(mkt), skip_initial=0)
    assert plausible_beta(fit)          # 物理上可能
    assert not precise_enough(fit)      # 但跟噪声区分不开


def test_a_clean_estimate_passes_both_gates():
    import random
    random.seed(3)
    mkt = [random.gauss(0, 0.01) for _ in range(120)]
    ast = [2.0 * m + random.gauss(0, 0.003) for m in mkt]
    fit = estimate_beta(_series(ast), _series(mkt), skip_initial=0)
    assert plausible_beta(fit) and precise_enough(fit)


def test_nothing_measured_is_not_precise_enough():
    assert not precise_enough(None)
