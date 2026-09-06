from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scoring"))
from bull_call_spread import (
    BullCallCandidate,
    breakeven,
    compute_adr,
    compute_move_needed_pct,
    compute_rom,
    generate_call_candidates_from_chain,
    is_valid_bull_call,
    max_loss,
    max_profit,
    rank_candidates,
    score_bull_call_spread,
    spread_width,
)


def _candidate(**overrides) -> BullCallCandidate:
    defaults = dict(
        ticker="NVDA", expiration="2026-11-20", dte=76,
        long_strike=180.0, short_strike=200.0, net_debit=8.0,
        stock_price=185.0,
    )
    defaults.update(overrides)
    return BullCallCandidate(**defaults)


def test_basic_metrics_match_hand_computation():
    c = _candidate(long_strike=180.0, short_strike=200.0, net_debit=8.0, dte=76, stock_price=185.0)
    assert spread_width(c) == pytest.approx(20.0)
    assert max_profit(c) == pytest.approx(12.0)   # width - debit = 20 - 8
    assert max_loss(c) == pytest.approx(8.0)       # = net_debit
    assert breakeven(c) == pytest.approx(188.0)    # long + debit = 180 + 8
    assert compute_rom(c) == pytest.approx(12.0 / 8.0)             # max_profit/max_loss
    assert compute_adr(c) == pytest.approx((12.0 / 8.0 / 76) * 365)
    # move needed = (breakeven - price)/price = (188 - 185)/185
    assert compute_move_needed_pct(c) == pytest.approx((188.0 - 185.0) / 185.0)


def test_move_needed_is_negative_when_price_already_past_breakeven():
    # long 100, short 120, debit 5 -> breakeven 105; price 110 is already past it.
    c = _candidate(long_strike=100.0, short_strike=120.0, net_debit=5.0, stock_price=110.0)
    move = compute_move_needed_pct(c)
    assert move < 0
    # score_move must still cap at 30, not exceed it for being "extra good"
    assert score_bull_call_spread(c).score_move == 30.0


def test_score_move_hits_zero_at_5pct_and_ceiling_at_or_below_0pct():
    # Construct exact breakeven distances via net_debit at a $100 stock.
    # move_needed = (long+debit-100)/100
    at_5pct  = _candidate(long_strike=100.0, short_strike=120.0, net_debit=5.0, stock_price=100.0)   # breakeven 105 -> 5%
    at_0pct  = _candidate(long_strike=100.0, short_strike=120.0, net_debit=0.0 + 0.0001, stock_price=100.0)
    assert score_bull_call_spread(at_5pct).score_move == pytest.approx(0.0, abs=0.05)
    assert score_bull_call_spread(at_0pct).score_move == pytest.approx(30.0, abs=0.1)


def test_component_scores_cap_at_their_ceiling_even_when_benchmark_is_blown_past():
    # Deep ITM long leg, cheap debit relative to width, short DTE -> ADR/ROM
    # both blow past benchmark; move_needed is deeply negative (already ITM).
    c = _candidate(long_strike=100.0, short_strike=200.0, net_debit=5.0, dte=10, stock_price=250.0)
    s = score_bull_call_spread(c)
    assert s.score_rom == 25.0
    assert s.score_adr == 35.0
    assert s.score_move == 30.0
    assert s.score_dte == 10.0
    assert s.total_score == pytest.approx(25.0 + 35.0 + 30.0 + 10.0)


def test_score_dte_is_always_full_marks():
    """Unlike the put module, DTE screening happens upstream (Sep-Dec 2026
    calendar filter) -- any candidate that reaches the scorer gets 10/10."""
    assert score_bull_call_spread(_candidate(dte=14)).score_dte == 10.0
    assert score_bull_call_spread(_candidate(dte=120)).score_dte == 10.0


def test_is_valid_bull_call_rejects_non_debit_or_inverted_strikes():
    assert is_valid_bull_call(_candidate(long_strike=180, short_strike=200, net_debit=8))
    assert not is_valid_bull_call(_candidate(long_strike=200, short_strike=180, net_debit=8))   # inverted
    assert not is_valid_bull_call(_candidate(long_strike=180, short_strike=200, net_debit=0))    # no debit
    assert not is_valid_bull_call(_candidate(long_strike=180, short_strike=200, net_debit=-1))   # credit, not debit
    assert not is_valid_bull_call(_candidate(long_strike=180, short_strike=200, net_debit=21))   # debit > width


def test_rank_candidates_sorts_descending_and_drops_invalid():
    good_high = _candidate(long_strike=180, short_strike=200, net_debit=5.0, dte=76, stock_price=195.0)
    good_low  = _candidate(long_strike=180, short_strike=200, net_debit=15.0, dte=76, stock_price=181.0)
    invalid   = _candidate(long_strike=200, short_strike=180, net_debit=5.0)   # inverted, dropped

    ranked = rank_candidates([good_low, invalid, good_high], top_n=None)
    assert len(ranked) == 2
    assert ranked[0].candidate is good_high
    assert ranked[1].candidate is good_low
    assert ranked[0].total_score >= ranked[1].total_score


def _fake_call_chain(stock_price, rows):
    """rows: list of (strike, bid, ask)."""
    return pd.DataFrame(
        [{"strike": s, "bid": b, "ask": a, "und_px": stock_price} for s, b, a in rows]
    )


def test_generate_call_candidates_builds_long_ask_short_bid_debit():
    chain = _fake_call_chain(185.0, [
        (180.0, 9.60, 9.90),   # ~2.7% ITM, inside default-style moneyness window
        (200.0, 1.20, 1.40),
        (190.0, 4.80, 5.10),
    ])
    cands = generate_call_candidates_from_chain(
        "NVDA", chain, "2026-11-20", 76, widths=[20.0], long_moneyness_lo_pct=-10, long_moneyness_hi_pct=10,
    )
    # long=180/short=200: debit = long.ask(9.90) - short.bid(1.20) = 8.70
    match = [c for c in cands if c.long_strike == 180.0 and c.short_strike == 200.0]
    assert len(match) == 1
    assert match[0].net_debit == pytest.approx(8.70)
    assert match[0].stock_price == pytest.approx(185.0)
    assert match[0].ticker == "NVDA" and match[0].expiration == "2026-11-20" and match[0].dte == 76


def test_generate_call_candidates_skips_missing_short_strike_and_zero_ask():
    chain = _fake_call_chain(100.0, [
        (95.0, 6.0, 0.0),     # long ask is 0 -> skipped
        (110.0, 1.0, 1.2),    # would-be short for a different long, no partner here
    ])
    cands = generate_call_candidates_from_chain(
        "T", chain, "2026-11-20", 60, widths=[10.0], long_moneyness_lo_pct=-10, long_moneyness_hi_pct=10,
    )
    assert cands == []


def test_generate_call_candidates_respects_moneyness_window():
    chain = _fake_call_chain(100.0, [
        (115.0, 0.5, 0.6),    # 15% OTM -- outside a (-10,10) window
        (95.0, 6.5, 6.8),     # 5% ITM -- inside
        (105.0, 2.0, 2.2),    # short leg for the 95 long
    ])
    cands = generate_call_candidates_from_chain(
        "T", chain, "2026-11-20", 60, widths=[10.0], long_moneyness_lo_pct=-10, long_moneyness_hi_pct=10,
    )
    assert all(c.long_strike != 115.0 for c in cands)
    assert any(c.long_strike == 95.0 for c in cands)


def test_generate_call_candidates_empty_chain_returns_empty():
    assert generate_call_candidates_from_chain(
        "T", pd.DataFrame(), "2026-11-20", 60, widths=[10.0], long_moneyness_lo_pct=-10, long_moneyness_hi_pct=10,
    ) == []


def test_rank_candidates_respects_top_n():
    cands = [
        _candidate(long_strike=180, short_strike=200, net_debit=d, dte=76, stock_price=195.0)
        for d in [5.0, 7.0, 9.0, 11.0, 13.0]
    ]
    top3 = rank_candidates(cands, top_n=3)
    assert len(top3) == 3
    assert [s.total_score for s in top3] == sorted([s.total_score for s in top3], reverse=True)
