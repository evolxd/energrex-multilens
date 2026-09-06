"""Bull Call Spread quantitative scoring.

The user's uploaded template ("期权价差量化评分系统_1.md", 2026-09-03 — see
scoring/bull_put_spread.py) only specifies the credit-spread (Bull Put)
formulas. This module is a *symmetric construction* for the debit-spread
counterpart, built by mirroring the same 4-factor philosophy — it is NOT
something the template itself specifies for calls, and the one place where
that matters (see MOVE_NEEDED_BENCHMARK below) is called out explicitly.
Do not treat this module's benchmarks as user-verified in the same sense as
bull_put_spread.py's; they're a disclosed mirror, not a re-derivation.

A Bull Call Spread here: buy a lower-strike call (long leg), sell a
higher-strike call (short leg), same underlying and expiry, net debit paid
upfront. Profits if the underlying rises above breakeven (long strike + net
debit) through expiration; max profit is capped at spread width minus the
debit; max loss is capped at the debit paid (you can never lose more than
you paid, unlike a naked long call there's also no assignment risk before
expiration on the short leg beyond early-exercise dividend edge cases).

What's mirrored 1:1 from the put template, and why it transfers cleanly:
  - ROM ("Return on Margin/capital" here) = max_profit / max_loss. This is
    just reward:risk — for a credit spread max_loss = width - credit and
    max_profit = credit; for a debit spread max_loss = debit and
    max_profit = width - debit. Same ratio, same 40% benchmark, same 25%
    weight: the formula doesn't actually depend on credit vs. debit
    structure, only on what "profit" and "loss" resolve to.
  - ADR = (ROM / DTE) * 365. Same reasoning — annualizing a reward:risk
    ratio is structure-agnostic. Same 350% benchmark, same 35% weight.

What's a deliberate symmetric substitution (not from the template):
  - Buffer% (put side: (stock_price - breakeven) / stock_price — a
    DOWNSIDE cushion, since a credit spread just needs the stock to not
    crash) has no direct analog for a debit spread, which explicitly needs
    the stock to rise. The substituted factor here is "Move Needed %" =
    (breakeven - stock_price) / stock_price — how far the stock must still
    rise to reach breakeven. It's scored the mirror-image way: less
    required move -> more points, hitting full marks at move_needed <= 0
    (already past breakeven) and zero at move_needed >= 5%. The 5%
    threshold is chosen ONLY because it mirrors the put template's own 5%
    Buffer benchmark by magnitude, not because it was independently
    derived or user-confirmed for this instrument -- flag this if you
    want a different threshold.

What's deliberately dropped (not carried over):
  - "Breakeven win rate" (put side: 1 - net_credit/width). That formula is
    not a real derived probability -- transcribed as-is per the template
    for puts because the template says so. Its structural analog for a
    debit spread does not reduce to the same expression, and inventing a
    new one here would be exactly the kind of unverified numeric guess
    this project's owner has explicitly pushed back on before. Skipped
    entirely rather than fabricated.
  - DTE scoring is a flat 10/10 (not a 30-45-day window like puts). This
    module is meant to be driven by an explicit Sep-Dec-2026 expiration
    calendar filter upstream (a calendar-month window, not a day-count
    window), so by the time a candidate reaches this scorer it has already
    passed the DTE screen -- there is no partial credit to award.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BullCallCandidate:
    """One (expiration, long_strike, short_strike) bull call spread candidate."""
    ticker:        str
    expiration:    str     # ISO date string, e.g. "2026-11-20"
    dte:           int     # days to expiration
    long_strike:   float   # bought call strike (lower of the two)
    short_strike:  float   # sold call strike (higher of the two)
    net_debit:     float   # per-share debit paid (not multiplied by 100)
    stock_price:   float   # underlying price at analysis time


def is_valid_bull_call(c: BullCallCandidate) -> bool:
    """A bull call spread requires short > long strike and a positive debit
    smaller than the width (otherwise it's not a debit spread at all)."""
    width = c.short_strike - c.long_strike
    return width > 0 and 0 < c.net_debit < width


def spread_width(c: BullCallCandidate) -> float:
    return round(c.short_strike - c.long_strike, 4)


def max_profit(c: BullCallCandidate) -> float:
    """单张最大收益 Max Profit = Width - Net Debit."""
    return round(spread_width(c) - c.net_debit, 4)


def max_loss(c: BullCallCandidate) -> float:
    """单张最大风险 Max Loss = Net Debit（已付出的权利金，就是全部风险）。"""
    return round(c.net_debit, 4)


def breakeven(c: BullCallCandidate) -> float:
    """损益平衡点 Breakeven = Long Strike + Net Debit."""
    return round(c.long_strike + c.net_debit, 4)


def compute_rom(c: BullCallCandidate) -> float:
    """ROM（资金回报率）= Max Profit / Max Loss = (Width - Debit) / Debit."""
    ml = max_loss(c)
    if ml <= 0:
        return 0.0
    return max_profit(c) / ml


def compute_adr(c: BullCallCandidate) -> float:
    """ADR（日均年化回报率）= (ROM / DTE) x 365."""
    if c.dte <= 0:
        return 0.0
    return compute_rom(c) / c.dte * 365


def compute_move_needed_pct(c: BullCallCandidate) -> float:
    """所需涨幅 = (Breakeven - 现价) / 现价。

    可以为负——如果现价已经超过 breakeven（价差在当前价位已经"账面盈利"）。
    这是 Buffer% 的镜像替代，方向相反：数值越小（甚至为负）越好。
    """
    if c.stock_price <= 0:
        return 0.0
    return (breakeven(c) - c.stock_price) / c.stock_price


# ── 4因子评分模型（100分制）── ROM/ADR 基准沿用模板；Move Needed 是本模块
#    对 Buffer% 的镜像替代（见模块顶部说明），5% 阈值只是量级对称，不是
#    独立验证过的数字 ──
_ADR_BENCHMARK         = 3.50   # 350% annualized -> full marks（沿用模板）
_MOVE_NEEDED_BENCHMARK = 0.05   # 所需涨幅 <=5% -> 满分（镜像 Buffer 5% 基准）
_ROM_BENCHMARK         = 0.40   # 40% return on capital -> full marks（沿用模板）


def score_adr(adr: float) -> float:
    return round(min(35.0, (adr / _ADR_BENCHMARK) * 35.0), 2)


def score_move_needed(move_needed_pct: float) -> float:
    """所需涨幅越小分越高；<=0（已经过盈亏平衡点）封顶30分；
    >=5% 记0分（不给负分）。"""
    raw = (_MOVE_NEEDED_BENCHMARK - move_needed_pct) / _MOVE_NEEDED_BENCHMARK * 30.0
    return round(min(30.0, max(0.0, raw)), 2)


def score_rom(rom: float) -> float:
    return round(min(25.0, (rom / _ROM_BENCHMARK) * 25.0), 2)


def score_dte(dte: int) -> float:
    """固定10分——DTE 筛选（9-12月交割日日历窗口）在上游做，走到这里的candidate
    已经过筛，没有"部分及格"的概念（不像 put 模板那样有单一天数区间）。"""
    return 10.0


@dataclass(frozen=True)
class BullCallScore:
    candidate:        BullCallCandidate
    width:            float
    max_profit:       float
    max_loss:         float
    breakeven:        float
    rom:              float
    adr:              float
    move_needed_pct:  float
    score_adr:        float
    score_move:       float
    score_rom:        float
    score_dte:        float
    total_score:      float


def score_bull_call_spread(c: BullCallCandidate) -> BullCallScore:
    rom  = compute_rom(c)
    adr  = compute_adr(c)
    move = compute_move_needed_pct(c)

    s_adr  = score_adr(adr)
    s_move = score_move_needed(move)
    s_rom  = score_rom(rom)
    s_dte  = score_dte(c.dte)
    total  = round(s_adr + s_move + s_rom + s_dte, 1)

    return BullCallScore(
        candidate=c, width=spread_width(c), max_profit=max_profit(c),
        max_loss=max_loss(c), breakeven=breakeven(c), rom=rom, adr=adr,
        move_needed_pct=move, score_adr=s_adr, score_move=s_move,
        score_rom=s_rom, score_dte=s_dte, total_score=total,
    )


def generate_call_candidates_from_chain(
    ticker: str,
    chain: pd.DataFrame,
    expiration: str,
    dte: int,
    widths: list[float],
    long_moneyness_lo_pct: float,
    long_moneyness_hi_pct: float,
) -> list[BullCallCandidate]:
    """Build every (long_strike, width) candidate from one expiration's call
    chain. Mirrors bull_put_spread.generate_put_candidates_from_chain, but
    the swept leg is the LONG (bought) one here rather than the short —
    for a bull call spread the long leg's moneyness is what you're really
    choosing (how much you pay to participate), and the short leg falls
    out mechanically as long_strike + width.

    `chain` must be a MarketData.app-shaped call-side DataFrame (columns:
    strike/bid/ask/und_px) for one ticker+expiration.

    long_moneyness_lo_pct/hi_pct are relative to spot, e.g. (-10, 5) means
    "sweep long strikes from 10% ITM to 5% OTM". Negative = ITM, positive
    = OTM, mirroring the put module's OTM-from-spot convention.

    Net debit assumption: long leg bought at ask, short leg sold at bid —
    the conservative, actually-fillable estimate (mirrors the put module).
    """
    if chain.empty:
        return []
    stock_price_s = pd.to_numeric(chain["und_px"], errors="coerce").dropna()
    if stock_price_s.empty:
        return []
    stock_price = float(stock_price_s.iloc[0])

    strikes = chain.set_index("strike")[["bid", "ask"]].sort_index()
    long_lo = stock_price * (1 + long_moneyness_lo_pct / 100)
    long_hi = stock_price * (1 + long_moneyness_hi_pct / 100)
    long_candidates = strikes[(strikes.index >= long_lo) & (strikes.index <= long_hi)]

    out: list[BullCallCandidate] = []
    for long_strike, long_row in long_candidates.iterrows():
        long_ask = long_row["ask"]
        if pd.isna(long_ask) or long_ask <= 0:
            continue
        for width in widths:
            short_strike = round(long_strike + width, 2)
            if short_strike not in strikes.index:
                continue
            short_bid = strikes.loc[short_strike, "bid"]
            if pd.isna(short_bid) or short_bid <= 0:
                continue
            net_debit = round(float(long_ask) - float(short_bid), 4)
            if net_debit <= 0:
                continue
            out.append(BullCallCandidate(
                ticker=ticker, expiration=expiration, dte=dte,
                long_strike=float(long_strike), short_strike=float(short_strike),
                net_debit=net_debit, stock_price=stock_price,
            ))
    return out


def rank_candidates(candidates: list[BullCallCandidate], top_n: int | None = 10) -> list[BullCallScore]:
    """Score every valid candidate and return them sorted by total_score
    descending. Invalid candidates (see is_valid_bull_call) are dropped
    silently -- they're not tradeable debit spreads, not low-scoring ones.
    top_n=None returns the full ranked list; default 10 mirrors the put
    module's convention.
    """
    scored = [score_bull_call_spread(c) for c in candidates if is_valid_bull_call(c)]
    scored.sort(key=lambda s: s.total_score, reverse=True)
    return scored if top_n is None else scored[:top_n]
