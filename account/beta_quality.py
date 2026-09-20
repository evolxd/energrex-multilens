"""Betas derived here by regression, kept with the evidence behind them.

`_BETA_BASE` in account_monitor.py is a bare symbol -> float map, and anything
missing from it silently falls back to 1.0 in
`account.risk.compute_portfolio_stress_test` (`beta_map.get(sym, 1.0)`). That
default is invisible in the risk snapshot: a holding whose beta was never
measured looks exactly like one measured at 1.00.

It bit two of the largest exposures at once. On 2026-09-19 SPCX and ETHU were
both absent from the table and both running at beta 1.0, while their actual
market sensitivity is roughly 2.6 and 3.4 -- Beta-Delta was understating the
portfolio, in the direction that makes a leveraged book look calmer than it is.

Vendor numbers were no help. Firstrade reported 25.14 for SPCX, which is not a
beta anyone should use: the volatility ratio caps beta at 7.30 even under
perfect correlation, so 25 is arithmetically impossible as a regression result.
Alpha Vantage returned None for both. So they are measured here instead, and
the measurement is stored with the sample that produced it.

The R² is the part worth reading. Both names clear a beta above 2.5 while the
market explains under a fifth of their variance -- beta describes how they move
*when the market moves*, and most of what they do is not that. A hedge sized off
beta alone will under-perform on both of these.

Method: ordinary least squares of daily simple returns against SPY, implemented
in account/beta_regression.py. The values below are seeds; `_refresh_beta_spy()`
re-measures weekly and writes the fit into data/beta_cache.json, which
`load_measured()` prefers. Seeds only apply until the first refresh completes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# Below this, beta is a weak description of the holding and the risk snapshot
# says so rather than presenting the number as equivalent to a well-fit one.
LOW_CONFIDENCE_R2 = 0.30


@dataclass(frozen=True)
class DerivedBeta:
    symbol: str
    beta: float
    r_squared: float
    std_error: float
    sample_start: str
    sample_end: str
    observations: int
    note: str

    @property
    def is_low_confidence(self) -> bool:
        return self.r_squared < LOW_CONFIDENCE_R2

    def describe(self) -> str:
        return (f"{self.symbol} β={self.beta:.2f} (R²={self.r_squared:.2f}, "
                f"{self.observations}日 {self.sample_start}~{self.sample_end})")


DERIVED_BETAS: dict[str, DerivedBeta] = {
    # IPO 2026-06-12. The first five sessions are price discovery, not market
    # sensitivity -- 2026-06-12 traded 522M shares and 06-16 ranged 195->225.64.
    # Including them pulls beta to 3.25; dropping them gives 2.57, and the
    # estimate then holds across every cut tried (skip 3 -> 2.62, skip 5 -> 2.57,
    # skip 10 -> 2.72, skip 15 -> 2.63). That stability is why 2.6 is trusted
    # and 3.25 is not: an estimate that moved with the window would mean the
    # regression was fitting the IPO, not the stock.
    "SPCX": DerivedBeta(
        symbol="SPCX", beta=2.57, r_squared=0.163, std_error=0.75,
        sample_start="2026-06-22", sample_end="2026-09-18", observations=62,
        note="剔除上市后前5个交易日（定价发现期）；年化波动 73.6%",
    ),
    # No IPO effect -- ETHU has a long history -- but the window is matched to
    # SPCX so the two are comparable. Firstrade's 5.44 sits outside the 95%
    # interval [0.88, 4.87]; a 2x ether ETF is violent (104.7% annualised) but
    # that violence is mostly crypto, not the S&P.
    "ETHU": DerivedBeta(
        symbol="ETHU", beta=3.43, r_squared=0.163, std_error=0.79,
        sample_start="2026-04-29", sample_end="2026-09-18", observations=99,
        note="2倍做多以太币ETF；非新上市，用完整窗口不剔除起始日",
    ),
}


def _cache_path():
    import pathlib
    return pathlib.Path(__file__).resolve().parent.parent / "data" / "beta_cache.json"


def load_measured() -> dict[str, DerivedBeta]:
    """The committed seeds, overlaid with whatever the weekly refresh measured.

    `_refresh_beta_spy()` re-runs the regression and writes its fits into
    `data/beta_cache.json`, so these numbers track the market instead of
    freezing at whatever they were the day they were written. The seeds below
    stay as the offline fallback: a machine that has never completed a refresh
    still gets a measured beta rather than the silent 1.0.
    """
    measured = dict(DERIVED_BETAS)
    try:
        import json

        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
        for symbol, fit in (raw.get("fits") or {}).items():
            skipped = fit.get("skipped_initial", 0)
            measured[symbol] = DerivedBeta(
                symbol=symbol,
                beta=float(fit["beta"]),
                r_squared=float(fit.get("r_squared", 0.0)),
                std_error=float(fit.get("std_error", 0.0)),
                sample_start=str(fit.get("sample_start", "")),
                sample_end=str(fit.get("sample_end", "")),
                observations=int(fit.get("observations", 0)),
                note=(f"每周自动回归；剔除起始 {skipped} 个交易日"
                      if skipped else "每周自动回归"),
            )
    except Exception:
        pass            # 缓存缺失/损坏就用种子值，不该因此没有 beta
    return measured


def beta_overrides() -> dict[str, float]:
    """Symbol -> beta, for merging into the main table."""
    return {symbol: entry.beta for symbol, entry in load_measured().items()}


def low_confidence_held(symbols: Iterable[str]) -> list[DerivedBeta]:
    """The held symbols whose beta is a weak fit, worst first.

    Only reports names actually in the portfolio: a warning about a holding you
    do not have is noise, and noise is how the 1.0 default went unnoticed.
    """
    held = {str(s).strip().upper() for s in symbols if s}
    found = [entry for symbol, entry in load_measured().items()
             if symbol in held and entry.is_low_confidence]
    return sorted(found, key=lambda e: e.r_squared)
