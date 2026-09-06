from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scoring"))
from bull_put_spread import (
    BullPutCandidate,
    breakeven,
    compute_adr,
    compute_breakeven_win_rate,
    compute_buffer_pct,
    compute_risk_reward,
    compute_rom,
    generate_put_candidates_from_chain,
    is_valid_bull_put,
    max_loss,
    rank_candidates,
    score_bull_put_spread,
    spread_width,
)


def _candidate(**overrides) -> BullPutCandidate:
    defaults = dict(
        ticker="NVDA", expiration="2026-10-16", dte=35,
        short_strike=170.0, long_strike=165.0, net_credit=1.50,
        stock_price=180.0,
    )
    defaults.update(overrides)
    return BullPutCandidate(**defaults)


def _template_reference_score(net_credit, spread_width_, dte, stock_price, short_strike):
    """The template's own Section 6 Python reference, transcribed verbatim
    (not imported) so it stays an independent check on our implementation."""
    max_loss_ = spread_width_ - net_credit
    rom = net_credit / max_loss_
    adr = (rom / dte) * 365
    buffer_percent = (stock_price - (short_strike - net_credit)) / stock_price

    score_adr = min(35.0, (adr / 3.5) * 35.0)
    score_buffer = min(30.0, (buffer_percent / 0.05) * 30.0)
    score_rom = min(25.0, (rom / 0.40) * 25.0)
    score_dte = 10.0 if 30 <= dte <= 45 else 8.5

    total_score = score_adr + score_buffer + score_rom + score_dte
    return round(total_score, 1)


def test_basic_metrics_match_hand_computation():
    c = _candidate(short_strike=170.0, long_strike=165.0, net_credit=1.50, dte=35, stock_price=180.0)
    assert spread_width(c) == pytest.approx(5.0)
    assert max_loss(c) == pytest.approx(3.50)
    assert breakeven(c) == pytest.approx(168.50)
    assert compute_rom(c) == pytest.approx(1.50 / 3.50)
    assert compute_adr(c) == pytest.approx((1.50 / 3.50 / 35) * 365)
    assert compute_buffer_pct(c) == pytest.approx((180.0 - 168.50) / 180.0)
    assert compute_breakeven_win_rate(c) == pytest.approx(1 - 1.50 / 5.0)
    assert compute_risk_reward(c) == pytest.approx((3.50, 1.50))


@pytest.mark.parametrize("short,long_,credit,dte,price", [
    (170.0, 165.0, 1.50, 35, 180.0),
    (450.0, 440.0, 3.20, 20, 470.0),
    (95.0, 90.0, 0.80, 52, 102.0),
    (300.0, 280.0, 6.00, 44, 305.0),   # wide spread, near breakeven
])
def test_total_score_matches_template_reference_python(short, long_, credit, dte, price):
    c = _candidate(short_strike=short, long_strike=long_, net_credit=credit, dte=dte, stock_price=price)
    ours = score_bull_put_spread(c).total_score
    reference = _template_reference_score(credit, short - long_, dte, price, short)
    assert ours == pytest.approx(reference, abs=0.01)


def test_dte_score_full_marks_only_inside_30_45_window():
    in_window  = _candidate(dte=30)
    also_in    = _candidate(dte=45)
    below      = _candidate(dte=29)
    above      = _candidate(dte=46)
    assert score_bull_put_spread(in_window).score_dte == 10.0
    assert score_bull_put_spread(also_in).score_dte == 10.0
    assert score_bull_put_spread(below).score_dte == 8.5
    assert score_bull_put_spread(above).score_dte == 8.5


def test_component_scores_cap_at_their_ceiling_even_when_benchmark_is_blown_past():
    # Absurdly rich credit relative to width -> ROM/ADR/Buffer all blow past
    # their benchmarks; scores must still cap at 25/35/30, never exceed them.
    c = _candidate(short_strike=100.0, long_strike=95.0, net_credit=4.9, dte=10, stock_price=200.0)
    s = score_bull_put_spread(c)
    assert s.score_rom == 25.0
    assert s.score_adr == 35.0
    assert s.score_buffer == 30.0
    assert s.total_score == pytest.approx(25.0 + 35.0 + 30.0 + 8.5)


def test_is_valid_bull_put_rejects_non_credit_or_inverted_strikes():
    assert is_valid_bull_put(_candidate(short_strike=170, long_strike=165, net_credit=1.5))
    assert not is_valid_bull_put(_candidate(short_strike=165, long_strike=170, net_credit=1.5))  # inverted
    assert not is_valid_bull_put(_candidate(short_strike=170, long_strike=165, net_credit=0))     # no credit
    assert not is_valid_bull_put(_candidate(short_strike=170, long_strike=165, net_credit=-0.5))  # debit
    assert not is_valid_bull_put(_candidate(short_strike=170, long_strike=165, net_credit=5.5))   # credit > width


def test_rank_candidates_sorts_descending_and_drops_invalid():
    good_high  = _candidate(short_strike=170, long_strike=165, net_credit=2.0, dte=35, stock_price=200.0)
    good_low   = _candidate(short_strike=170, long_strike=165, net_credit=0.5, dte=60, stock_price=175.0)
    invalid    = _candidate(short_strike=165, long_strike=170, net_credit=1.0)   # inverted, dropped

    ranked = rank_candidates([good_low, invalid, good_high], top_n=None)
    assert len(ranked) == 2
    assert ranked[0].candidate is good_high
    assert ranked[1].candidate is good_low
    assert ranked[0].total_score >= ranked[1].total_score


def _fake_put_chain(stock_price, rows):
    """rows: list of (strike, bid, ask)."""
    return pd.DataFrame(
        [{"strike": s, "bid": b, "ask": a, "und_px": stock_price} for s, b, a in rows]
    )


def test_generate_put_candidates_builds_short_bid_long_ask_credit():
    chain = _fake_put_chain(180.0, [
        (175.0, 3.20, 3.40),   # OTM ~2.8%, inside default-style OTM window
        (170.0, 1.50, 1.70),
        (165.0, 0.80, 0.95),
    ])
    cands = generate_put_candidates_from_chain(
        "NVDA", chain, "2026-10-16", 35, widths=[5.0], otm_lo_pct=1, otm_hi_pct=10,
    )
    # short=175/long=170: credit = short.bid(3.20) - long.ask(1.70) = 1.50
    match = [c for c in cands if c.short_strike == 175.0 and c.long_strike == 170.0]
    assert len(match) == 1
    assert match[0].net_credit == pytest.approx(1.50)
    assert match[0].stock_price == pytest.approx(180.0)
    assert match[0].ticker == "NVDA" and match[0].expiration == "2026-10-16" and match[0].dte == 35


def test_generate_put_candidates_skips_missing_long_strike_and_zero_bid():
    chain = _fake_put_chain(100.0, [
        (95.0, 0.0, 0.10),     # short bid is 0 -> skipped
        (90.0, 1.00, 1.20),    # would-be long for a different short, has no partner here
    ])
    cands = generate_put_candidates_from_chain(
        "T", chain, "2026-10-16", 30, widths=[5.0], otm_lo_pct=1, otm_hi_pct=15,
    )
    assert cands == []


def test_generate_put_candidates_respects_otm_window():
    chain = _fake_put_chain(100.0, [
        (99.0, 2.0, 2.2),   # 1% OTM -- outside a (5,15) window
        (85.0, 0.5, 0.6),   # 15% OTM -- inside
        (80.0, 0.2, 0.3),   # long leg for the 85 short
    ])
    cands = generate_put_candidates_from_chain(
        "T", chain, "2026-10-16", 40, widths=[5.0], otm_lo_pct=5, otm_hi_pct=15,
    )
    assert all(c.short_strike != 99.0 for c in cands)
    assert any(c.short_strike == 85.0 for c in cands)


def test_generate_put_candidates_empty_chain_returns_empty():
    assert generate_put_candidates_from_chain(
        "T", pd.DataFrame(), "2026-10-16", 30, widths=[5.0], otm_lo_pct=1, otm_hi_pct=15,
    ) == []


def test_rank_candidates_respects_top_n():
    cands = [
        _candidate(short_strike=170, long_strike=165, net_credit=c, dte=35, stock_price=200.0)
        for c in [0.5, 1.0, 1.5, 2.0, 2.5]
    ]
    top3 = rank_candidates(cands, top_n=3)
    assert len(top3) == 3
    assert [s.total_score for s in top3] == sorted([s.total_score for s in top3], reverse=True)
