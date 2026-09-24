"""Measure beta by regression when the data vendor will not supply one.

`_refresh_beta_spy()` reads `yf.Ticker(sym).info["beta"]`, which is empty for
anything recently listed or for many ETFs. Those symbols fell through to
`_BETA_BASE`, and a symbol absent from that table ends up at
`beta_map.get(sym, 1.0)` -- beta 1.0, applied silently, in the direction that
makes a leveraged holding look tame. SPCX and ETHU sat there for weeks.

Vendor numbers are not automatically better than no number. Firstrade published
25.14 for SPCX; the volatility ratio caps beta at 7.30 even if correlation were
a perfect 1.0, so that figure cannot have come from a regression. Hence
`plausible_beta()`: an estimate is only accepted if it stays under the ceiling
its own volatility ratio allows.

Everything here is a pure function over price series so the arithmetic can be
tested without a network.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Mapping

# A newly listed stock's first sessions are price discovery -- allocation
# flipping, stabilisation, index inclusion flows -- not market sensitivity.
# SPCX listed 2026-06-12 on 522M shares and ranged 195->225.64 on day three;
# including those days put its beta at 3.25, excluding them at 2.57, and the
# 2.57 then held steady whether 3, 5, 10 or 15 sessions were dropped. The
# stability across cuts is the evidence that the early days were the anomaly.
IPO_SKIP_SESSIONS = 5

# A listing is detected by the asset's history starting materially later than
# the market's, not by the series being short. Series length depends on how much
# data the caller happened to fetch: with a truncated market series ETHU -- which
# has years of history -- looked "short" and had five good sessions cut, moving
# its beta from 2.88 to 2.17. Comparing start dates is invariant to the window.
NEW_LISTING_LAG_SESSIONS = 10

# Fewer observations than this and the standard error swamps the estimate.
MIN_OBSERVATIONS = 30


#: 下跌日 beta 至少要有这么多个下跌日才算得出来。整段样本大约一半是下跌日，
#: 所以它比全样本的 MIN_OBSERVATIONS 低，但不能再低——条件子样本上的标准误
#: 本来就比全样本大约 √2 倍，样本再少就只剩噪声。
MIN_DOWN_OBSERVATIONS = 25


@dataclass(frozen=True)
class BetaFit:
    beta: float
    r_squared: float
    std_error: float
    observations: int
    sample_start: str
    sample_end: str
    volatility_ratio: float
    skipped_initial: int
    #: "full" = 全样本；"downside" = 只用大盘下跌的交易日。两者是**不同的
    #: 参数**，不是同一个数的两种精度。压力测试算的是 beta × 负的冲击，要
    #: 的是 downside；而一张表里混着两种而不标出来，是最难查的那类错。
    kind: str = "full"

    @property
    def is_plausible(self) -> bool:
        """Beta cannot exceed the volatility ratio -- that needs correlation > 1."""
        return abs(self.beta) <= self.volatility_ratio + 1e-9


def _aligned_returns(
    asset: Mapping[str, float],
    market: Mapping[str, float],
    skip_initial: int,
) -> tuple[list[float], list[float], list[str]]:
    """Simple daily returns on the dates both series share.

    The skipped sessions are dropped whole rather than merely excluded from the
    output: using a skipped day's close as the base for the next return would
    let the price-discovery period back in through the denominator.
    """
    dates = sorted(set(asset) & set(market))[skip_initial:]
    asset_returns: list[float] = []
    market_returns: list[float] = []
    for prev, cur in zip(dates, dates[1:]):
        if asset[prev] <= 0 or market[prev] <= 0:
            continue
        asset_returns.append(asset[cur] / asset[prev] - 1.0)
        market_returns.append(market[cur] / market[prev] - 1.0)
    return asset_returns, market_returns, dates


def _stdev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return (sum((v - mean) ** 2 for v in values) / (n - 1)) ** 0.5


def looks_recently_listed(
    asset_closes: Mapping[str, float],
    market_closes: Mapping[str, float],
) -> bool:
    """True when the asset's history begins well after the market's.

    The market series is the yardstick: SPY has been trading throughout, so an
    asset whose first close lands many sessions later started trading there.
    An asset that merely has fewer rows because of a short fetch window starts
    on the same date as the market and is correctly left alone.
    """
    if not asset_closes or not market_closes:
        return False
    asset_start = min(asset_closes)
    market_dates = sorted(market_closes)
    later = [d for d in market_dates if d < asset_start]
    return len(later) >= NEW_LISTING_LAG_SESSIONS


def estimate_beta(
    asset_closes: Mapping[str, float],
    market_closes: Mapping[str, float],
    skip_initial: int | None = None,
) -> BetaFit | None:
    """OLS of the asset's daily returns on the market's. None if too thin.

    `skip_initial=None` decides automatically: drop the opening sessions only
    when the series is short enough to look like a recent listing.
    """
    if skip_initial is None:
        skip_initial = (IPO_SKIP_SESSIONS
                        if looks_recently_listed(asset_closes, market_closes) else 0)

    asset_r, market_r, dates = _aligned_returns(asset_closes, market_closes, skip_initial)
    if not asset_r:
        return None
    return _fit_returns(
        list(zip(asset_r, market_r)),
        dates[1:] if len(dates) > 1 else dates,
        kind="full",
        skip_initial=skip_initial,
        min_observations=MIN_OBSERVATIONS,
    )


def _fit_returns(
    pairs: list[tuple[float, float]],
    dates: list[str],
    *,
    kind: str,
    skip_initial: int,
    min_observations: int,
) -> BetaFit | None:
    """OLS 的算术部分。`pairs` 是 (标的收益, 大盘收益)。

    从 estimate_beta 里抽出来，好让下跌日版本走的是同一段代码——两个 beta
    必须用同一套算法算，否则它们的差就分不清是"下跌日真的不一样"还是
    "两个实现不一样"。
    """
    n = len(pairs)
    if n < min_observations:
        return None
    mean_a = sum(p[0] for p in pairs) / n
    mean_m = sum(p[1] for p in pairs) / n
    cov = sum((a - mean_a) * (m - mean_m) for a, m in pairs)
    var_m = sum((m - mean_m) ** 2 for _, m in pairs)
    if var_m <= 0:
        return None

    beta = cov / var_m
    alpha = mean_a - beta * mean_m
    residuals = [a - (alpha + beta * m) for a, m in pairs]
    ss_res = sum(r * r for r in residuals)
    ss_tot = sum((a - mean_a) ** 2 for a, _ in pairs)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    std_error = ((ss_res / (n - 2)) / var_m) ** 0.5 if n > 2 else float("inf")

    market_sd = _stdev([m for _, m in pairs])
    vol_ratio = _stdev([a for a, _ in pairs]) / market_sd if market_sd > 0 else float("inf")

    return BetaFit(
        beta=round(beta, 4),
        r_squared=round(r_squared, 4),
        std_error=round(std_error, 4),
        observations=n,
        sample_start=dates[0],
        sample_end=dates[-1],
        volatility_ratio=round(vol_ratio, 4),
        skipped_initial=skip_initial,
        kind=kind,
    )


def estimate_downside_beta(
    asset_closes: Mapping[str, float],
    market_closes: Mapping[str, float],
    skip_initial: int | None = None,
) -> BetaFit | None:
    """只用**大盘下跌**的交易日回归出来的 beta。

    压力测试问的是"大盘跌 10% 时我亏多少"，它算的是 `beta × 负的冲击`。
    这个条件下该用的系数就是下跌日的斜率，不是全样本的。两者可以差很远，
    而且差的方向不固定：

    2026-09-21 实测（对 SPY）——
        KLAC  全样本 3.37（近5个月日频）/ 1.43（5年月频）；下跌日 2.33 / 2.27
        ONTO  全样本 3.80          / 1.57          ；下跌日 2.08 / 2.72

    全样本 beta 在两个窗口下差一倍多，下跌日 beta 却几乎一致。这说明短窗那
    个 3.37 是被上涨日撑起来的——用它做压力测试会在错误的方向上高估，用
    1.43 又会低估真实的下行敏感度。下跌日 beta 两头都不沾。

    它同时也更**稳**：跨窗口一致意味着这个数不依赖你恰好取了哪段历史，而
    这正是一个要拿去管限额的输入最需要的性质。

    代价是样本只剩一半，标准误大约 √2 倍——所以门槛是 MIN_DOWN_OBSERVATIONS
    而不是 MIN_OBSERVATIONS，且调用方应当读 std_error 而不是只看点估计。
    """
    if skip_initial is None:
        skip_initial = (IPO_SKIP_SESSIONS
                        if looks_recently_listed(asset_closes, market_closes) else 0)

    asset_r, market_r, dates = _aligned_returns(asset_closes, market_closes, skip_initial)
    # dates[0] 是第一个收益率的**基准**日，收益率本身从 dates[1] 起，所以
    # 逐日对齐时要跳过第一个日期。
    return_dates = dates[1:] if len(dates) > 1 else dates

    picked = [(a, m, d) for a, m, d in zip(asset_r, market_r, return_dates) if m < 0]
    if not picked:
        return None
    return _fit_returns(
        [(a, m) for a, m, _ in picked],
        [d for _, _, d in picked],
        kind="downside",
        skip_initial=skip_initial,
        min_observations=MIN_DOWN_OBSERVATIONS,
    )


#: 点估计至少要是自身标准误的这么多倍，才算"测出来了"而不是"噪声凑巧落在
#: 这个数上"。2.0 对应 95% 区间不跨过 0。
MIN_T_STAT = 2.0


def plausible_beta(fit: BetaFit | None, hard_ceiling: float = 15.0) -> bool:
    """Whether an estimate is safe to publish into the beta table.

    The volatility-ratio test is the one that matters: it is what rejects a
    25.14 against a 7.30 ceiling. The absolute ceiling only guards against a
    degenerate market series.
    """
    if fit is None:
        return False
    if not fit.is_plausible:
        return False
    return 0.0 < abs(fit.beta) < hard_ceiling


def precise_enough(fit: BetaFit | None, min_t: float = MIN_T_STAT) -> bool:
    """点估计是否跟自身噪声区分得开（|β| / 标准误 ≥ min_t）。

    下跌日 beta 只用得上大约一半的样本，标准误约为全样本的 √2 倍——概念上
    它是压力测试该用的那个系数，但样本不够时它只是一个方向都定不下来的数。
    2026-09-21 在 99 个交易日的窗口上实测：

        KLAC  下跌日 β=2.33  SE=1.15  t=2.0   95%区间 [ 0.08, 4.59]
        ONTO  下跌日 β=2.08  SE=1.39  t=1.5   95%区间 [-0.65, 4.80]  ← 跨过 0
        PATH  下跌日 β=-0.06 SE=1.24  t=0.1   95%区间 [-2.49, 2.36]  ← 纯噪声

    ONTO 那个区间跨过 0，意思是"大盘跌的时候它到底跟跌还是逆势涨"这件事，
    这段样本都没答出来。把它写进管限额的表里，等于用一个方向未知的数去决定
    要不要减仓。

    所以下跌日 beta 必须过这一关才允许覆盖全样本值；过不了就保留全样本值并
    把 kind 标成 "full"——宁可让表里口径不齐但**看得见**，也不要让它整齐而
    其中一半是噪声。
    """
    if fit is None or not fit.std_error or fit.std_error <= 0:
        return False
    return abs(fit.beta) / fit.std_error >= min_t


def fetch_closes(symbol: str, period: str = "1y") -> dict[str, float]:
    """Daily closes keyed by ISO date, via yfinance. {} on any failure.

    Kept separate from the maths above so the regression stays testable
    offline; callers treat an empty dict as "no data, leave the beta alone".
    """
    try:
        import yfinance as yf

        history = yf.Ticker(symbol).history(period=period, auto_adjust=True)
        if history is None or history.empty:
            return {}
        return {
            (idx.date() if hasattr(idx, "date") else dt.date.fromisoformat(str(idx)[:10])).isoformat(): float(row)
            for idx, row in history["Close"].items()
            if row == row and float(row) > 0            # skip NaN
        }
    except Exception:
        return {}
