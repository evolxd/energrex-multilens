"""Bull Put Spread quantitative scoring.

Formulas and the 4-factor scoring model (weights + benchmarks) are copied
verbatim from the user-supplied template
"期权价差量化评分系统_1.md" (uploaded 2026-09-03, Sections 2/3/6). Do not
change the benchmarks (ADR 350%, Buffer 5%, ROM 40%, DTE 30-45d full-score
window) without the user re-confirming against the template -- they are the
whole point of this module, not tuning knobs.

A Bull Put Spread here: sell a higher-strike put (short leg), buy a
lower-strike put (long leg), same underlying and expiry, net credit
collected upfront. Profits if the underlying stays above the short strike
through expiration; max loss is capped at spread width minus the credit.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BullPutCandidate:
    """One (expiration, short_strike, long_strike) bull put spread candidate."""
    ticker:        str
    expiration:    str     # ISO date string, e.g. "2026-10-16"
    dte:           int     # days to expiration
    short_strike:  float   # sold put strike (higher of the two)
    long_strike:   float   # bought put strike (lower of the two)
    net_credit:    float   # per-share credit received (not multiplied by 100)
    stock_price:   float   # underlying price at analysis time


def is_valid_bull_put(c: BullPutCandidate) -> bool:
    """A bull put spread requires short > long strike and a positive credit
    smaller than the width (otherwise it's not a credit spread at all)."""
    width = c.short_strike - c.long_strike
    return width > 0 and 0 < c.net_credit < width


def spread_width(c: BullPutCandidate) -> float:
    return round(c.short_strike - c.long_strike, 4)


def max_loss(c: BullPutCandidate) -> float:
    """单张最大风险 Max Loss/Margin = Width - Net Credit."""
    return round(spread_width(c) - c.net_credit, 4)


def breakeven(c: BullPutCandidate) -> float:
    """损益平衡点 Breakeven = Short Strike - Net Credit."""
    return round(c.short_strike - c.net_credit, 4)


def compute_rom(c: BullPutCandidate) -> float:
    """2.1 保证金回报率 ROM = Net Credit / (Width - Net Credit)."""
    ml = max_loss(c)
    if ml <= 0:
        return 0.0
    return c.net_credit / ml


def compute_adr(c: BullPutCandidate) -> float:
    """2.2 日均年化回报率 ADR = (ROM / DTE) x 365."""
    if c.dte <= 0:
        return 0.0
    return compute_rom(c) / c.dte * 365


def compute_buffer_pct(c: BullPutCandidate) -> float:
    """2.3 安全垫 Buffer% = (Stock Price - (Short Strike - Net Credit)) / Stock Price."""
    if c.stock_price <= 0:
        return 0.0
    return (c.stock_price - breakeven(c)) / c.stock_price


def compute_breakeven_win_rate(c: BullPutCandidate) -> float:
    """2.5 理论盈亏平衡胜率 = 1 - Net Credit / Width."""
    width = spread_width(c)
    if width <= 0:
        return 0.0
    return 1 - c.net_credit / width


def compute_risk_reward(c: BullPutCandidate) -> tuple[float, float]:
    """2.4 R:R = Max Loss : Net Credit, returned as (max_loss, net_credit)."""
    return (max_loss(c), c.net_credit)


# ── 3. 多维度量化评分模型（100分制）── benchmarks from the template ──
_ADR_BENCHMARK    = 3.50   # 350% annualized -> full marks
_BUFFER_BENCHMARK = 0.05   # 5% safety margin -> full marks
_ROM_BENCHMARK    = 0.40   # 40% return on margin -> full marks
_DTE_FULL_RANGE   = (30, 45)   # inclusive DTE window that scores 10/10


def score_adr(adr: float) -> float:
    return round(min(35.0, (adr / _ADR_BENCHMARK) * 35.0), 2)


def score_buffer(buffer_pct: float) -> float:
    return round(min(30.0, (buffer_pct / _BUFFER_BENCHMARK) * 30.0), 2)


def score_rom(rom: float) -> float:
    return round(min(25.0, (rom / _ROM_BENCHMARK) * 25.0), 2)


def score_dte(dte: int) -> float:
    lo, hi = _DTE_FULL_RANGE
    return 10.0 if lo <= dte <= hi else 8.5


@dataclass(frozen=True)
class BullPutScore:
    candidate:          BullPutCandidate
    width:              float
    max_loss:           float
    breakeven:          float
    rom:                float
    adr:                float
    buffer_pct:         float
    breakeven_win_rate: float
    score_adr:          float
    score_buffer:       float
    score_rom:          float
    score_dte:          float
    total_score:        float


def score_bull_put_spread(c: BullPutCandidate) -> BullPutScore:
    rom  = compute_rom(c)
    adr  = compute_adr(c)
    buf  = compute_buffer_pct(c)
    bewr = compute_breakeven_win_rate(c)

    s_adr = score_adr(adr)
    s_buf = score_buffer(buf)
    s_rom = score_rom(rom)
    s_dte = score_dte(c.dte)
    total = round(s_adr + s_buf + s_rom + s_dte, 1)

    return BullPutScore(
        candidate=c, width=spread_width(c), max_loss=max_loss(c),
        breakeven=breakeven(c), rom=rom, adr=adr, buffer_pct=buf,
        breakeven_win_rate=bewr, score_adr=s_adr, score_buffer=s_buf,
        score_rom=s_rom, score_dte=s_dte, total_score=total,
    )


def generate_put_candidates_from_chain(
    ticker: str,
    chain: pd.DataFrame,
    expiration: str,
    dte: int,
    widths: list[float],
    otm_lo_pct: float,
    otm_hi_pct: float,
) -> list[BullPutCandidate]:
    """Build every (short_strike, width) candidate from one expiration's put
    chain. Extracted from bull_put_spread_module.py's inline loop so the
    single-ticker manual page and any batch/universe screener share the
    exact same candidate-construction rule instead of two copies drifting
    apart over time.

    `chain` must be a MarketData.app-shaped put-side DataFrame (columns:
    strike/bid/ask/und_px) for one ticker+expiration, e.g. the "put" rows
    of scoring/options_chain.py's fetch_chain_marketdata() output.

    Net credit assumption: short leg sold at bid, long leg bought at ask —
    a conservative, actually-fillable estimate, not the more flattering but
    not-necessarily-tradeable mid price.
    """
    if chain.empty:
        return []
    stock_price_s = pd.to_numeric(chain["und_px"], errors="coerce").dropna()
    if stock_price_s.empty:
        return []
    stock_price = float(stock_price_s.iloc[0])

    strikes = chain.set_index("strike")[["bid", "ask"]].sort_index()
    short_lo = stock_price * (1 - otm_hi_pct / 100)
    short_hi = stock_price * (1 - otm_lo_pct / 100)
    short_candidates = strikes[(strikes.index >= short_lo) & (strikes.index <= short_hi)]

    out: list[BullPutCandidate] = []
    for short_strike, short_row in short_candidates.iterrows():
        short_bid = short_row["bid"]
        if pd.isna(short_bid) or short_bid <= 0:
            continue
        for width in widths:
            long_strike = round(short_strike - width, 2)
            if long_strike not in strikes.index:
                continue
            long_ask = strikes.loc[long_strike, "ask"]
            if pd.isna(long_ask) or long_ask <= 0:
                continue
            net_credit = round(float(short_bid) - float(long_ask), 4)
            if net_credit <= 0:
                continue
            out.append(BullPutCandidate(
                ticker=ticker, expiration=expiration, dte=dte,
                short_strike=float(short_strike), long_strike=float(long_strike),
                net_credit=net_credit, stock_price=stock_price,
            ))
    return out


def rank_candidates(candidates: list[BullPutCandidate], top_n: int | None = 10) -> list[BullPutScore]:
    """Score every valid candidate and return them sorted by total_score
    descending. Invalid candidates (see is_valid_bull_put) are dropped
    silently -- they're not tradeable credit spreads, not low-scoring ones.
    top_n=None returns the full ranked list (used for the comparison table);
    the default 10 matches the template's Section 4 排名前十汇总表.
    """
    scored = [score_bull_put_spread(c) for c in candidates if is_valid_bull_put(c)]
    scored.sort(key=lambda s: s.total_score, reverse=True)
    return scored if top_n is None else scored[:top_n]
