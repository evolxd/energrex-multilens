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
    n = len(asset_r)
    if n < MIN_OBSERVATIONS:
        return None

    mean_a = sum(asset_r) / n
    mean_m = sum(market_r) / n
    cov = sum((a - mean_a) * (m - mean_m) for a, m in zip(asset_r, market_r))
    var_m = sum((m - mean_m) ** 2 for m in market_r)
    if var_m <= 0:
        return None

    beta = cov / var_m
    alpha = mean_a - beta * mean_m
    residuals = [a - (alpha + beta * m) for a, m in zip(asset_r, market_r)]
    ss_res = sum(r * r for r in residuals)
    ss_tot = sum((a - mean_a) ** 2 for a in asset_r)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    std_error = ((ss_res / (n - 2)) / var_m) ** 0.5 if n > 2 else float("inf")

    market_sd = _stdev(market_r)
    vol_ratio = _stdev(asset_r) / market_sd if market_sd > 0 else float("inf")

    return BetaFit(
        beta=round(beta, 4),
        r_squared=round(r_squared, 4),
        std_error=round(std_error, 4),
        observations=n,
        sample_start=dates[1] if len(dates) > 1 else dates[0],
        sample_end=dates[-1],
        volatility_ratio=round(vol_ratio, 4),
        skipped_initial=skip_initial,
    )


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
