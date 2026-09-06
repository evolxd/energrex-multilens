from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scoring"))
from spread_universe_screener import (
    COL_CIRCUIT,
    COL_FINAL_SCORE,
    COL_MOMENTUM,
    COL_RATING,
    COL_TICKER,
    rank_call_screen_results,
    rank_put_screen_results,
    screen_bull_call_spreads,
    screen_bull_put_spreads,
    select_candidate_pool,
)

import datetime

TODAY = datetime.date(2026, 9, 6)


# ── select_candidate_pool ─────────────────────────────────────────────────

def _universe_row(ticker, final_score, momentum=50.0, rating="👀 综合中性", circuit=None):
    return {
        COL_TICKER: ticker, COL_FINAL_SCORE: final_score,
        COL_MOMENTUM: momentum, COL_RATING: rating, COL_CIRCUIT: circuit,
        "val_估值得分(PEG/EV/ERG/PE/FCFYld)": 50.0,
        "grw_成长得分(营收/EPS/FCF/指引增速)": 50.0,
        "qlt_质量得分(毛利率/FCF率/ROIC/负债)": 50.0,
        "ai_AI暴露得分(AI营收/平台/订单占比)": 50.0,
        "exp_预期差得分(超预期营收EPS指引)": 50.0,
    }


def _universe_df(rows):
    return pd.DataFrame(rows)


def test_select_candidate_pool_excludes_circuit_breaker_and_high_risk_rating():
    df = _universe_df([
        _universe_row("GOOD", 80.0),
        _universe_row("CIRCUIT", 90.0, circuit="YES"),
        _universe_row("RISKY", 85.0, rating="🚫 风险较高"),
    ])
    pool = select_candidate_pool(df, top_n=10)
    assert set(pool[COL_TICKER]) == {"GOOD"}


def test_select_candidate_pool_sorts_by_final_score_and_caps_at_top_n():
    df = _universe_df([_universe_row(f"T{i}", float(i)) for i in range(5)])
    pool = select_candidate_pool(df, top_n=2)
    assert list(pool[COL_TICKER]) == ["T4", "T3"]


def test_select_candidate_pool_momentum_gate_uses_pool_median_not_topn_slice():
    # 4 names, momentum 10/20/30/40 -> median 25. Gate should drop the two
    # below 25 BEFORE the top_n cut, not after.
    df = _universe_df([
        _universe_row("LOWMOM_HIGHSCORE", 100.0, momentum=10.0),
        _universe_row("LOWMOM2", 90.0, momentum=20.0),
        _universe_row("HIMOM1", 50.0, momentum=30.0),
        _universe_row("HIMOM2", 40.0, momentum=40.0),
    ])
    pool = select_candidate_pool(df, top_n=10, require_above_median_momentum=True)
    assert "LOWMOM_HIGHSCORE" not in set(pool[COL_TICKER])
    assert set(pool[COL_TICKER]) == {"HIMOM1", "HIMOM2"}


# ── screen_bull_put_spreads (fake data sources, no network) ───────────────

def _fake_chain(stock_price, put_rows=(), call_rows=()):
    rows = [{"strike": s, "bid": b, "ask": a, "und_px": stock_price, "side": "put"} for s, b, a in put_rows]
    rows += [{"strike": s, "bid": b, "ask": a, "und_px": stock_price, "side": "call"} for s, b, a in call_rows]
    return pd.DataFrame(rows)


def test_screen_bull_put_spreads_picks_expiration_closest_to_window_midpoint():
    # window (30,60) -> midpoint 45. Expirations at DTE 31 and 44 available;
    # 44 is closer to 45 than 31 is, so 44 should be chosen.
    exp_31 = (TODAY + datetime.timedelta(days=31)).isoformat()
    exp_44 = (TODAY + datetime.timedelta(days=44)).isoformat()
    exp_90 = (TODAY + datetime.timedelta(days=90)).isoformat()  # outside window

    def fetch_expirations(ticker):
        return [exp_31, exp_44, exp_90]

    calls_seen = []

    def fetch_chain(ticker, exp):
        calls_seen.append(exp)
        return _fake_chain(100.0, put_rows=[(95.0, 2.0, 2.2), (90.0, 0.8, 1.0)])

    result = screen_bull_put_spreads(
        ["T"], fetch_expirations, fetch_chain,
        dte_lo=30, dte_hi=60, widths=[5.0], otm_lo_pct=1, otm_hi_pct=15, today=TODAY,
    )
    assert calls_seen == [exp_44]
    assert result["T"] is not None
    assert result["T"].candidate.expiration == exp_44


def test_screen_bull_put_spreads_returns_none_when_no_expiration_in_window():
    def fetch_expirations(ticker):
        return [(TODAY + datetime.timedelta(days=10)).isoformat()]   # too soon

    def fetch_chain(ticker, exp):
        raise AssertionError("should never fetch a chain with no expiration in window")

    result = screen_bull_put_spreads(["T"], fetch_expirations, fetch_chain, today=TODAY)
    assert result["T"] is None


def test_screen_bull_put_spreads_returns_none_on_empty_chain():
    exp = (TODAY + datetime.timedelta(days=45)).isoformat()

    def fetch_expirations(ticker):
        return [exp]

    def fetch_chain(ticker, e):
        return pd.DataFrame()

    result = screen_bull_put_spreads(["T"], fetch_expirations, fetch_chain, today=TODAY)
    assert result["T"] is None


# ── screen_bull_call_spreads ────────────────────────────────────────────

def test_screen_bull_call_spreads_only_considers_sep_dec_2026_expirations():
    exp_aug = "2026-08-15"   # before Sep -- excluded
    exp_sep = "2026-09-18"   # included
    exp_dec = "2026-12-18"   # included
    exp_2027jan = "2027-01-15"  # wrong year -- excluded

    def fetch_expirations(ticker):
        return [exp_aug, exp_sep, exp_dec, exp_2027jan]

    fetched = []

    def fetch_chain(ticker, exp):
        fetched.append(exp)
        return _fake_chain(100.0, call_rows=[(95.0, 6.5, 6.8), (105.0, 2.0, 2.2)])

    screen_bull_call_spreads(
        ["T"], fetch_expirations, fetch_chain,
        expiry_year=2026, expiry_months=(9, 10, 11, 12),
        widths=[10.0], long_moneyness_lo_pct=-10, long_moneyness_hi_pct=10, today=TODAY,
    )
    assert set(fetched) == {exp_sep, exp_dec}


def test_screen_bull_call_spreads_keeps_best_across_multiple_matching_expiries():
    exp_sep = "2026-09-18"
    exp_dec = "2026-12-18"

    def fetch_expirations(ticker):
        return [exp_sep, exp_dec]

    def fetch_chain(ticker, exp):
        if exp == exp_sep:
            # short leg only fetches 0.6 -> net_debit 6.2 (expensive, thin cushion)
            return _fake_chain(100.0, call_rows=[(95.0, 6.5, 6.8), (105.0, 0.5, 0.6)])
        # short leg fetches 3.2 -> net_debit 3.6 (cheaper, past breakeven already)
        return _fake_chain(100.0, call_rows=[(95.0, 6.5, 6.8), (105.0, 3.0, 3.2)])

    result = screen_bull_call_spreads(
        ["T"], fetch_expirations, fetch_chain,
        widths=[10.0], long_moneyness_lo_pct=-10, long_moneyness_hi_pct=10, today=TODAY,
    )
    assert result["T"] is not None
    # Dec's lower net_debit (3.6 vs 6.2) means a lower breakeven -- already
    # past it here -- so it wins on score_move even though both candidates'
    # ADR/ROM are capped identically at their ceilings.
    assert result["T"].candidate.expiration == exp_dec


def test_screen_bull_call_spreads_returns_none_when_no_expiration_matches():
    def fetch_expirations(ticker):
        return ["2026-06-19"]   # before Sep

    def fetch_chain(ticker, exp):
        raise AssertionError("should never fetch with no matching expiration")

    result = screen_bull_call_spreads(["T"], fetch_expirations, fetch_chain, today=TODAY)
    assert result["T"] is None


# ── rank_*_screen_results: annotation + sort ──────────────────────────────

def test_rank_put_screen_results_sorts_desc_and_attaches_fundamentals():
    from bull_put_spread import BullPutCandidate, score_bull_put_spread
    a = score_bull_put_spread(BullPutCandidate("A", "2026-10-16", 45, 170, 165, 3.0, 200.0))
    b = score_bull_put_spread(BullPutCandidate("B", "2026-10-16", 45, 170, 165, 1.0, 175.0))
    universe = _universe_df([_universe_row("A", 80.0), _universe_row("B", 60.0)])

    out = rank_put_screen_results({"A": a, "B": None, "C": b}, universe, top_n=10)
    assert list(out["ticker"]) == sorted(["A", "C"], key=lambda t: -{"A": a, "C": b}[t].total_score)
    assert "final_score" in out.columns
    # C has no fundamental row in `universe` -> NaN, not a crash
    c_row = out[out["ticker"] == "C"].iloc[0]
    assert pd.isna(c_row["final_score"])
    a_row = out[out["ticker"] == "A"].iloc[0]
    assert a_row["final_score"] == 80.0


def test_rank_put_screen_results_respects_top_n():
    from bull_put_spread import BullPutCandidate, score_bull_put_spread
    scores = {
        t: score_bull_put_spread(BullPutCandidate(t, "2026-10-16", 45, 170, 165, c, 200.0))
        for t, c in [("A", 4.0), ("B", 3.0), ("C", 2.0), ("D", 1.0)]
    }
    universe = _universe_df([_universe_row(t, 50.0) for t in scores])
    out = rank_put_screen_results(scores, universe, top_n=2)
    assert len(out) == 2
    assert list(out["rank"]) == [1, 2]


def test_rank_call_screen_results_sorts_desc_and_attaches_fundamentals():
    from bull_call_spread import BullCallCandidate, score_bull_call_spread
    a = score_bull_call_spread(BullCallCandidate("A", "2026-11-20", 76, 180, 200, 5.0, 195.0))
    b = score_bull_call_spread(BullCallCandidate("B", "2026-11-20", 76, 180, 200, 15.0, 181.0))
    universe = _universe_df([_universe_row("A", 80.0), _universe_row("B", 60.0)])

    out = rank_call_screen_results({"A": a, "B": b}, universe, top_n=10)
    assert out.iloc[0]["ticker"] == "A"   # cheaper debit, better ROM -> higher score
    assert "final_score" in out.columns and out.iloc[0]["final_score"] == 80.0
