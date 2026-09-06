"""Universe-wide Bull Put / Bull Call spread screeners.

The manual "期权价差评分" page (bull_put_spread_module.py) scores every
candidate spread on ONE user-typed ticker. This module instead scans a
pre-filtered pool of tickers drawn from the AI-stock scoring universe
(results_validated.csv), pulls each one's option chain, generates spread
candidates with the *unmodified* bull_put_spread.py / bull_call_spread.py
engines, keeps the single best-scoring spread per ticker, and ranks across
the whole pool -- producing a top-10 across many stocks instead of a
top-10 across many strikes on one stock.

Two design decisions, confirmed with the user 2026-09-06:

1. The option-quant total_score (already-validated 4-factor formula, same
   engine as the manual page) is the ONLY ranking key. Each output row is
   *annotated* with the ticker's final_score and 5 dimension scores from
   the fundamental scoring system for reference, never blended into the
   ranking. A blend weight would be exactly the kind of unverified,
   made-up number this project's owner has pushed back on before (see the
   "15%-position-limit" incident in project history) -- so it's not done.

2. `select_candidate_pool` below is an explicit v1 placeholder for
   "unlikely to crash soon" (puts) / "likely to run soon" (calls). It
   reuses existing final_score / momentum columns and nothing more
   sophisticated. The user's own stated next step is a dedicated
   quantitative judgment for exactly this question -- do not mistake this
   placeholder for that module, and replace it wholesale once that lands.
"""
from __future__ import annotations

import datetime
import pathlib
import sys
from typing import Callable

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bull_call_spread import (       # noqa: E402
    BullCallScore,
    generate_call_candidates_from_chain,
)
from bull_call_spread import rank_candidates as _rank_call_candidates   # noqa: E402
from bull_put_spread import (        # noqa: E402
    BullPutScore,
    generate_put_candidates_from_chain,
)
from bull_put_spread import rank_candidates as _rank_put_candidates     # noqa: E402

_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_CSV = _ROOT / "results_validated.csv"

# ── results_validated.csv 列名映射（避免到处手打含中文/括号的原始表头）──
COL_TICKER          = "ticker"
COL_FINAL_SCORE     = "final_综合得分(0-100)"
COL_VALUATION       = "val_估值得分(PEG/EV/ERG/PE/FCFYld)"
COL_GROWTH          = "grw_成长得分(营收/EPS/FCF/指引增速)"
COL_QUALITY         = "qlt_质量得分(毛利率/FCF率/ROIC/负债)"
COL_AI_EXPOSURE     = "ai_AI暴露得分(AI营收/平台/订单占比)"
COL_EXPECTATION_GAP = "exp_预期差得分(超预期营收EPS指引)"
COL_MOMENTUM        = "mom_动量得分(RSI14/价格vs200日均)"
COL_RATING          = "rating_评级"
COL_CIRCUIT         = "circuit_熔断(Beta>2.2且回撤>35%或DE>1.8)"

_ANNOTATION_COLS = {
    COL_FINAL_SCORE:     "final_score",
    COL_VALUATION:       "valuation_score",
    COL_GROWTH:          "growth_score",
    COL_QUALITY:         "quality_score",
    COL_AI_EXPOSURE:     "ai_exposure_score",
    COL_EXPECTATION_GAP: "expectation_gap_score",
    COL_MOMENTUM:        "momentum_score",
    COL_RATING:          "rating",
}

_HIGH_RISK_RATING = "🚫 风险较高"


def load_universe(csv_path: pathlib.Path | str | None = None) -> pd.DataFrame:
    """Load results_validated.csv with the columns this screener needs."""
    path = pathlib.Path(csv_path) if csv_path else DEFAULT_RESULTS_CSV
    return pd.read_csv(path)


def select_candidate_pool(
    df: pd.DataFrame,
    top_n: int = 60,
    require_above_median_momentum: bool = False,
) -> pd.DataFrame:
    """v1 candidate-pool pre-filter (see module docstring, point 2):

      - drop circuit-breaker-triggered tickers (existing danger flag)
      - drop the existing "🚫 风险较高" rating bucket
      - rank by the existing final_score, take the top `top_n`
      - for calls (require_above_median_momentum=True), additionally
        require the momentum score to be at/above the pool's own median
        BEFORE the top_n cut -- a bullish tilt using an existing column,
        not a new composite metric.

    Returns the filtered+sorted DataFrame (not just a ticker list) so
    callers can inspect what got excluded and why.
    """
    pool = df[df[COL_CIRCUIT].isna()].copy()
    pool = pool[pool[COL_RATING] != _HIGH_RISK_RATING]
    if require_above_median_momentum:
        median_mom = pool[COL_MOMENTUM].median()
        pool = pool[pool[COL_MOMENTUM] >= median_mom]
    pool = pool.sort_values(COL_FINAL_SCORE, ascending=False)
    return pool.head(top_n)


def _annotations_for(universe_df: pd.DataFrame) -> pd.DataFrame:
    cols = [COL_TICKER] + list(_ANNOTATION_COLS.keys())
    out = universe_df[cols].copy()
    return out.rename(columns={COL_TICKER: "ticker", **_ANNOTATION_COLS})


# ════════════════════════════════════════════════════════════════════════
# Bull Put Spread — 30-60 DTE window across the pool
# ════════════════════════════════════════════════════════════════════════

def screen_bull_put_spreads(
    tickers: list[str],
    fetch_expirations: Callable[[str], list[str]],
    fetch_chain: Callable[[str, str], pd.DataFrame],
    dte_lo: int = 30,
    dte_hi: int = 60,
    widths: list[float] | None = None,
    otm_lo_pct: float = 3,
    otm_hi_pct: float = 15,
    today: datetime.date | None = None,
) -> dict[str, BullPutScore | None]:
    """For each ticker: pick the single expiration inside [dte_lo, dte_hi]
    closest to the window's midpoint (one API round-trip's worth of chain
    data per ticker, not one per expiration -- 447 tickers x N expirations
    would blow through most API plans' rate limits), generate put
    candidates, and keep the single best-scoring spread.

    Returns {ticker: BullPutScore or None}. None means "no usable
    expiration/chain data", never "scored zero" -- callers must not
    conflate a missing quote with a bad spread.

    `fetch_expirations`/`fetch_chain` are injected (not imported directly
    from scoring.options_chain) so this is testable against a fake data
    source without hitting MarketData.app or importing streamlit.
    """
    today = today or datetime.date.today()
    widths = list(widths) if widths else [5.0, 10.0]
    mid = (dte_lo + dte_hi) / 2
    out: dict[str, BullPutScore | None] = {}

    for ticker in tickers:
        expirations = fetch_expirations(ticker)
        in_window = []
        for e in expirations:
            try:
                dte = (datetime.date.fromisoformat(e) - today).days
            except ValueError:
                continue
            if dte_lo <= dte <= dte_hi:
                in_window.append((e, dte))
        if not in_window:
            out[ticker] = None
            continue

        exp, dte = min(in_window, key=lambda x: abs(x[1] - mid))
        chain = fetch_chain(ticker, exp)
        if chain is None or chain.empty:
            out[ticker] = None
            continue
        puts = chain[chain["side"].astype(str).str.lower() == "put"]
        candidates = generate_put_candidates_from_chain(
            ticker, puts, exp, dte, widths, otm_lo_pct, otm_hi_pct,
        )
        ranked = _rank_put_candidates(candidates, top_n=1)
        out[ticker] = ranked[0] if ranked else None

    return out


def rank_put_screen_results(
    scores: dict[str, BullPutScore | None],
    universe_df: pd.DataFrame,
    top_n: int = 10,
) -> pd.DataFrame:
    rows = []
    for ticker, s in scores.items():
        if s is None:
            continue
        c = s.candidate
        rows.append({
            "ticker": ticker, "expiration": c.expiration, "dte": c.dte,
            "short_strike": c.short_strike, "long_strike": c.long_strike,
            "width": s.width, "net_credit": c.net_credit,
            "max_profit": c.net_credit, "max_loss": s.max_loss,
            "breakeven": s.breakeven, "rom": s.rom, "adr": s.adr,
            "buffer_pct": s.buffer_pct,
            "breakeven_win_rate": s.breakeven_win_rate,
            "total_score": s.total_score,
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("total_score", ascending=False)
    df = df.merge(_annotations_for(universe_df), on="ticker", how="left")
    df.insert(0, "rank", range(1, len(df) + 1))
    return df.head(top_n).reset_index(drop=True) if top_n else df.reset_index(drop=True)


# ════════════════════════════════════════════════════════════════════════
# Bull Call Spread — Sep-Dec 2026 expiration calendar window
# ════════════════════════════════════════════════════════════════════════

def screen_bull_call_spreads(
    tickers: list[str],
    fetch_expirations: Callable[[str], list[str]],
    fetch_chain: Callable[[str, str], pd.DataFrame],
    expiry_year: int = 2026,
    expiry_months: tuple[int, ...] = (9, 10, 11, 12),
    widths: list[float] | None = None,
    long_moneyness_lo_pct: float = -5,
    long_moneyness_hi_pct: float = 5,
    today: datetime.date | None = None,
) -> dict[str, BullCallScore | None]:
    """For each ticker: pull every expiration falling in the given
    calendar-month window (a MONTH filter, not a DTE-count filter --
    Sep-Dec 2026 spans ~2 weeks to ~4 months of DTE depending on today's
    date), generate call candidates on each, and keep the single
    best-scoring spread across all of that ticker's matching expirations.

    Returns {ticker: BullCallScore or None}, same None-means-no-data
    convention as screen_bull_put_spreads.
    """
    today = today or datetime.date.today()
    widths = list(widths) if widths else [5.0, 10.0]
    out: dict[str, BullCallScore | None] = {}

    for ticker in tickers:
        expirations = fetch_expirations(ticker)
        all_candidates = []
        for e in expirations:
            try:
                d = datetime.date.fromisoformat(e)
            except ValueError:
                continue
            if d.year != expiry_year or d.month not in expiry_months or d <= today:
                continue
            dte = (d - today).days
            chain = fetch_chain(ticker, e)
            if chain is None or chain.empty:
                continue
            calls = chain[chain["side"].astype(str).str.lower() == "call"]
            all_candidates.extend(generate_call_candidates_from_chain(
                ticker, calls, e, dte, widths,
                long_moneyness_lo_pct, long_moneyness_hi_pct,
            ))
        if not all_candidates:
            out[ticker] = None
            continue
        ranked = _rank_call_candidates(all_candidates, top_n=1)
        out[ticker] = ranked[0] if ranked else None

    return out


def rank_call_screen_results(
    scores: dict[str, BullCallScore | None],
    universe_df: pd.DataFrame,
    top_n: int = 10,
) -> pd.DataFrame:
    rows = []
    for ticker, s in scores.items():
        if s is None:
            continue
        c = s.candidate
        rows.append({
            "ticker": ticker, "expiration": c.expiration, "dte": c.dte,
            "long_strike": c.long_strike, "short_strike": c.short_strike,
            "width": s.width, "net_debit": c.net_debit,
            "max_profit": s.max_profit, "max_loss": s.max_loss,
            "breakeven": s.breakeven, "rom": s.rom, "adr": s.adr,
            "move_needed_pct": s.move_needed_pct,
            "total_score": s.total_score,
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("total_score", ascending=False)
    df = df.merge(_annotations_for(universe_df), on="ticker", how="left")
    df.insert(0, "rank", range(1, len(df) + 1))
    return df.head(top_n).reset_index(drop=True) if top_n else df.reset_index(drop=True)
